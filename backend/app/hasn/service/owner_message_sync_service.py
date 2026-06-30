"""MEMCLOUD-A1：owner↔自有分身 IM 消息异步上云（消息上云 / messages:sync）。

背景（doc16 §4 Phase A）：主人与自己分身的对话目前**短路在本地 daemon**，云端
``hasn_messages`` 里根本没有这部分数据 —— 既导致换设备看不到聊天历史，也让「记忆
提取」拿不到最值钱的 owner 输入信号。本服务提供 **owner-scoped 幂等上行**：daemon
本地落库后经 outbox 异步把每条消息 upsert 进云端会话表（**以 client ``local_id``
去重**），返回**云端权威 message id**（铁律：跨设备/分享/URI 一律用云端权威 id）。

边界（与 ``message_router.route_message`` 泾渭分明）：
- **纯持久化**：复用 ``get_or_create_conversation`` 原子会话 + ``persist`` 落 ``hasn_messages``
  + 写 ``hasn_sync_events`` 供其它设备 sync/pull 恢复历史。
- **绝不重投递**：消息已在源设备本地派发过，这里**不做** WS 推送 / runtime dispatch /
  未读计数，避免把已派发的 loopback 消息二次投递。
- **只收 owner↔自有分身 loopback**：``agent_hasn_id`` 必须是**本主人名下**的分身
  （owner↔他人 走 ``route_message`` 早已上云，不归本端点）。这是清晰的安全边界：
  主人只能同步自己分身会话的消息，无法往任意会话注入。
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError

from backend.app.hasn.model import HasnConversations, HasnMessages
from backend.app.hasn.model.hasn_agents import HasnAgents
from backend.app.hasn.service.message_router import (
    _entity_type_int,
    _entity_type_str,
    _find_message_by_local_id,
    _grant_private_attachments,
    get_or_create_conversation,
)
from backend.common.exception import errors
from backend.utils.timezone import timezone

if TYPE_CHECKING:
    from datetime import datetime

    from sqlalchemy.ext.asyncio import AsyncSession

# content_type int → 同步事件里用的 MIME 风格字符串（与 route_message 同口径，便于
# 其它设备 sync/pull 消费端按统一形状解析）。
_CONTENT_TYPE_STR = {
    1: 'text',
    2: 'image/*',
    3: 'application/octet-stream',
    4: 'audio/*',
    5: 'application/x.card+json',
}

_DIRECTION_OUTBOUND = 'outbound'  # owner（human）→ agent
_DIRECTION_INBOUND = 'inbound'  # agent → owner（human）
_VALID_DIRECTIONS = (_DIRECTION_OUTBOUND, _DIRECTION_INBOUND)


@dataclass(frozen=True, slots=True)
class SyncedMessageResult:
    """上行结果：云端权威 id + 是否命中去重。"""

    message_id: str
    conversation_id: str
    deduped: bool


def _coerce_created_time(created_at_unix: int | None) -> datetime:
    """client unix 秒 → 本时区 datetime；缺失/非法回落服务端 now（保留真实发送时序，
    供记忆提取与跨设备排序用）。"""
    if created_at_unix is None:
        return timezone.now()
    try:
        return timezone.from_datetime(timezone.to_utc(int(created_at_unix)))
    except (TypeError, ValueError, OSError, OverflowError):
        return timezone.now()


async def _resolve_owned_agent(
    db: AsyncSession, *, owner_human_hasn_id: str, agent_hasn_id: str
) -> None:
    """校验 ``agent_hasn_id`` 是本主人名下的活跃分身；否则按越权/不存在拒绝。"""
    if not agent_hasn_id.startswith('a_'):
        raise errors.RequestError(msg='messages:sync 仅支持 owner↔自有分身 loopback，peer 必须是分身 (a_*)')
    owner_id = (
        await db.execute(select(HasnAgents.owner_id).where(HasnAgents.hasn_id == agent_hasn_id))
    ).scalar_one_or_none()
    if owner_id is None:
        raise errors.NotFoundError(msg='分身不存在')
    if owner_id != owner_human_hasn_id:
        raise errors.ForbiddenError(msg='无权同步该分身会话')


async def _persist_in_savepoint(
    db: AsyncSession,
    *,
    conversation_id: str,
    from_id: str,
    to_id: str,
    content: dict,
    content_type: int,
    msg_type: str,
    local_id: str,
    process_blocks: list[dict] | None,
    created_time: datetime,
) -> HasnMessages:
    """在 SAVEPOINT 内落消息行（无未读、无投递副作用）。

    用 ``begin_nested`` 包裹：并发 drain 重试若撞 ``local_id`` partial unique index，
    回滚 savepoint 而不污染外层事务，调用方据此再查一次返回 deduped。
    """
    now = timezone.now()
    async with db.begin_nested():
        msg = HasnMessages(
            conversation_id=conversation_id,
            from_id=from_id,
            from_type=_entity_type_int(from_id),
            to_id=to_id,
            to_type=_entity_type_int(to_id),
            content_type=content_type,
            content=content,
            process_blocks=process_blocks or [],
            msg_type=msg_type,
            status=1,  # sent
            priority='normal',
            reply_to_id=None,
            local_id=local_id,
            context=None,
            mentions=None,
            mention_all=False,
            server_received_at=now,
        )
        # 保留真实发送时序（backfill 历史不会被压成上云时刻）。
        msg.created_time = created_time
        db.add(msg)
        await db.flush()

        # 私有附件按会话授权（对齐 persist_message §1f；loopback 同 owner 仍需 grant 以走
        # 统一 resolve 路径）。
        await _grant_private_attachments(db, conversation_id, content)

        conv = await db.get(HasnConversations, conversation_id)
        if conv:
            conv.last_message_id = msg.id
            conv.last_message_at = created_time
            conv.last_message_from = from_id
            conv.message_count = (conv.message_count or 0) + 1
            conv.last_message_preview = _preview_for(content, content_type)
        await db.flush()
    return msg


def _preview_for(content: dict, content_type: int) -> str:
    if content_type == 1:
        text = content.get('text', '') if isinstance(content, dict) else ''
        return text[:200] if text else ''
    return {2: '[图片]', 3: '[文件]', 4: '[语音]', 5: '[卡片]'}.get(content_type, '[消息]')


async def _append_owner_sync_event(
    db: AsyncSession,
    *,
    owner_id: str,
    msg: HasnMessages,
    from_id: str,
    to_id: str,
    direction: str,
    content: dict,
    content_type: int,
    local_id: str,
) -> None:
    """写一条 owner-scoped 同步事件，供主人其它设备 sync/pull 恢复 loopback 历史。

    loopback 两端同属一个 owner（human），故只写**一条**事件（方向取真实方向：主人发
    = ``message.sent``，分身回 = ``message.received``），不像 route_message 跨 owner 两写。
    """
    from backend.app.hasn.service.hasn_sync_service import SqlAlchemySyncGateway

    event_type = 'message.sent' if direction == _DIRECTION_OUTBOUND else 'message.received'
    await SqlAlchemySyncGateway()._append_sync_event(
        db,
        owner_id=owner_id,
        hasn_id=from_id,
        event_type=event_type,
        aggregate_type='message',
        aggregate_id=str(msg.id),
        payload={
            'message_id': str(msg.id),
            'conversation_id': str(msg.conversation_id),
            'owner_id': owner_id,
            'hasn_id': from_id,
            'sender_hasn_id': from_id,
            'recipient_hasn_id': to_id,
            'direction': direction,
            'content_type': _CONTENT_TYPE_STR.get(content_type, 'text'),
            'content_body': content,
            'local_id': local_id,
            'created_at': int(msg.created_time.timestamp()) if msg.created_time else 0,
        },
    )


async def sync_owner_conversation_message(
    db: AsyncSession,
    *,
    owner_human_hasn_id: str,
    agent_hasn_id: str,
    direction: str,
    content: dict,
    content_type: int = 1,
    msg_type: str = 'message',
    local_id: str,
    created_at_unix: int | None = None,
    process_blocks: list[dict] | None = None,
) -> SyncedMessageResult:
    """幂等上行一条 owner↔自有分身 loopback 消息，返回云端权威 id。

    幂等键 = client ``local_id``（daemon 生成的全局唯一 uuid）。已存在 → 直接返回既有
    云端 id（deduped=True），不重复落库、不重复写事件。
    """
    if not local_id:
        raise errors.RequestError(msg='缺少 local_id（消息上云幂等键）')
    if direction not in _VALID_DIRECTIONS:
        raise errors.RequestError(msg=f'direction 非法（须为 {_DIRECTION_OUTBOUND}/{_DIRECTION_INBOUND}）')
    if not isinstance(content, dict):
        raise errors.RequestError(msg='content 必须是对象')

    await _resolve_owned_agent(db, owner_human_hasn_id=owner_human_hasn_id, agent_hasn_id=agent_hasn_id)

    # 去重快路：已上云直接回既有云端 id。
    existing = await _find_message_by_local_id(db, local_id)
    if existing is not None:
        return SyncedMessageResult(
            message_id=str(existing.id),
            conversation_id=str(existing.conversation_id),
            deduped=True,
        )

    if direction == _DIRECTION_OUTBOUND:
        from_id, to_id = owner_human_hasn_id, agent_hasn_id
    else:
        from_id, to_id = agent_hasn_id, owner_human_hasn_id

    conv = await get_or_create_conversation(
        db,
        from_id,
        _entity_type_str(from_id),
        to_id,
        _entity_type_str(to_id),
    )

    created_time = _coerce_created_time(created_at_unix)
    try:
        msg = await _persist_in_savepoint(
            db,
            conversation_id=str(conv.id),
            from_id=from_id,
            to_id=to_id,
            content=content,
            content_type=content_type,
            msg_type=msg_type,
            local_id=local_id,
            process_blocks=process_blocks,
            created_time=created_time,
        )
    except IntegrityError:
        # 并发 drain 抢先落了同一 local_id：savepoint 已回滚，外层事务仍在，再查一次返回 deduped。
        existing = await _find_message_by_local_id(db, local_id)
        if existing is not None:
            return SyncedMessageResult(
                message_id=str(existing.id),
                conversation_id=str(existing.conversation_id),
                deduped=True,
            )
        raise

    await _append_owner_sync_event(
        db,
        owner_id=owner_human_hasn_id,
        msg=msg,
        from_id=from_id,
        to_id=to_id,
        direction=direction,
        content=content,
        content_type=content_type,
        local_id=local_id,
    )

    return SyncedMessageResult(
        message_id=str(msg.id),
        conversation_id=str(msg.conversation_id),
        deduped=False,
    )
