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

from backend.app.hasn.crud.crud_hasn_conversations import hasn_conversations_dao
from backend.app.hasn.model import HasnConversations, HasnMessages
from backend.app.hasn.model.hasn_agents import HasnAgents
from backend.app.hasn.service.hasn_conversations_service import hasn_conversations_service
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

# sync/push 上来的会话事件 → 方向。主人提问 message.sent = outbound；分身回复
# message.agent_reply / 主人在别设备收到 message.received = inbound。
_OUTBOUND_EVENT_TYPES = frozenset({'message.sent'})


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


def _entity_type_int(hasn_id: str) -> int:
    """hasn_id → from_type/to_type 数字。"""
    if hasn_id.startswith('h_'):
        return 1  # human
    if hasn_id.startswith('a_'):
        return 2  # agent
    if hasn_id.startswith('g:'):
        return 4  # group
    if hasn_id.startswith('sv_'):
        return 5  # service（统一通知服务）
    return 3  # system


def _asset_id_from_uri(uri: object) -> str | None:
    """`hasn://asset/{asset_id}` → `asset_id`。"""
    if isinstance(uri, str) and uri.startswith('hasn://asset/'):
        candidate = uri[len('hasn://asset/') :].strip('/')
        return candidate or None
    return None


async def _grant_private_attachments(db: AsyncSession, conversation_id: str, content: dict | None) -> None:
    """为消息内私有附件按会话写读权 grant（1f），public 附件跳过。"""
    if not isinstance(content, dict):
        return
    attachments = content.get('attachments')
    if not isinstance(attachments, list) or not attachments:
        return
    asset_ids = [aid for a in attachments if isinstance(a, dict) and (aid := _asset_id_from_uri(a.get('uri')))]
    if not asset_ids:
        return
    # 延迟 import 避免潜在循环依赖
    from backend.app.hasn.service.hasn_asset_service import hasn_asset_service

    assets = await hasn_asset_service.get_many(db, asset_ids)
    for asset in assets.values():
        if asset.access == 'private':
            await hasn_asset_service.grant_to_conversation(db, asset_id=asset.asset_id, conversation_id=conversation_id)


async def _find_message_by_local_id(db: AsyncSession, local_id: str) -> HasnMessages | None:
    """按 local_id 查既有消息，用于 loopback 的幂等去重。"""
    result = await db.execute(select(HasnMessages).where(HasnMessages.local_id == local_id).limit(1))
    return result.scalar_one_or_none()


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
        # R2-02（doc16 §4.1）：在 SAVEPOINT 内取号——若并发 drain 撞 local_id 唯一索引
        # 回滚 savepoint，本次分配的 seq 一并回滚、不被 deduped 消息白白消耗。
        conversation_seq = await hasn_conversations_dao.allocate_seq(db, conversation_id)
        if conversation_seq is None:
            raise ValueError(f'allocate_seq 失败：会话 {conversation_id} 不存在，无法分配 conversation_seq')

        msg = HasnMessages(
            conversation_id=conversation_id,
            conversation_seq=conversation_seq,
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
    write_sync_event: bool = True,
) -> SyncedMessageResult:
    """幂等上行一条 owner↔自有分身 loopback 消息，返回云端权威 id。

    幂等键 = client ``local_id``（daemon 生成的全局唯一 uuid）。已存在 → 直接返回既有
    云端 id（deduped=True），不重复落库、不重复写事件。

    ``write_sync_event``：是否追加 owner 同步事件（``hasn_sync_events`` feed）。**经
    ``/sync/push`` 调用时传 ``False``**——该路径已由 ``_append_message_feed_event_idempotent``
    写 feed（跨设备权威），本服务只补 ``hasn_messages``（提取数据源），避免 feed 双写。
    独立 ``messages:sync`` HTTP 端点传 ``True``（直推时一并写 feed）。
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

    conv = await hasn_conversations_service.ensure_conversation(
        db=db,
        caller_hasn_id=from_id,
        peer_hasn_id=to_id,
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

    if write_sync_event:
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


def _content_type_str_to_int(value: object) -> int:
    """会话同步事件里的 ``content_type`` 字符串 → ``hasn_messages`` 的 int 编码（缺省 1=文本）。

    daemon 出站镜像用 ContentEnvelope 判别词（``text``/``image``/``file``/``voice``/``card``/
    ``json``）；分身回复镜像用 ``text/plain``。两套都归一到 int（1 文本/2 图片/3 文件/4 语音/
    5 卡片）。未知/缺失 → 文本（不伪造类型）。
    """
    if not isinstance(value, str):
        return 1
    v = value.lower()
    if v.startswith('image'):
        return 2
    if v == 'card' or 'card' in v:  # 'card' / 'application/x.card+json'
        return 5
    if v.startswith(('voice', 'audio')):
        return 4
    if v.startswith(('file', 'application')):
        return 3
    return 1  # text / text/plain / json / 未知 → 文本


def _loopback_agent_hasn_id(payload: dict) -> str | None:
    """从一条 owner↔自有分身会话同步事件 payload 取分身 hasn_id（``a_*``）。

    loopback 两端一个是 owner(human) 一个是 ``a_*`` 分身。优先 ``peer_hasn_id``（会话对端），
    回退 ``recipient_hasn_id``/``sender_hasn_id`` 中以 ``a_`` 开头者。两端都非分身 → ``None``
    （不该进 ``FEED_MESSAGE_EVENT_TYPES``，诚实跳过）。
    """
    for key in ('peer_hasn_id', 'recipient_hasn_id', 'sender_hasn_id'):
        candidate = payload.get(key)
        if isinstance(candidate, str) and candidate.startswith('a_'):
            return candidate
    return None


async def persist_loopback_message_from_sync_event(
    db: AsyncSession, *, owner_id: str, event_type: str, payload: dict
) -> SyncedMessageResult | None:
    """把一条经 ``/sync/push`` 上来的 owner↔自有分身会话事件落入权威 ``hasn_messages``。

    doc16 Phase A「消息上云」的 sink：daemon 的本地短路会话经既有 ``sync_outbox`` →
    ``SyncPushWorker`` → ``/sync/push`` 把每条消息作为 feed 事件上推（跨设备）；本函数让
    **同一条事件额外落入权威 ``hasn_messages``**（记忆提取的数据源），复用 A1 服务、以
    ``local_id``（缺失回退 daemon 本地 ``message_id``）幂等。**不再重复写 feed**
    （``/sync/push`` 已写）。

    仅处理 owner↔自有分身 loopback（peer 为 ``a_*`` 且本主人名下）；其余诚实跳过返回
    ``None``。**不抛非法输入异常**（缺字段直接 ``None``），保证 sink 端最坏只是跳过、绝不
    连累 feed 写入与跨设备同步。
    """
    direction = _DIRECTION_OUTBOUND if event_type in _OUTBOUND_EVENT_TYPES else _DIRECTION_INBOUND
    agent_hasn_id = _loopback_agent_hasn_id(payload)
    if agent_hasn_id is None:
        return None
    content = payload.get('content_body')
    if not isinstance(content, dict):
        return None
    # 幂等键：outbound 用 client local_id；inbound 回复 local_id 缺失 → 回退 daemon 本地
    # message_id（设备内全局唯一、跨上推稳定，与 feed 去重键同源）。
    local_id = payload.get('local_id') or payload.get('message_id')
    if not local_id:
        return None
    created_at = payload.get('created_at')
    return await sync_owner_conversation_message(
        db,
        owner_human_hasn_id=owner_id,
        agent_hasn_id=agent_hasn_id,
        direction=direction,
        content=content,
        content_type=_content_type_str_to_int(payload.get('content_type')),
        msg_type='message',
        local_id=str(local_id),
        created_at_unix=created_at if isinstance(created_at, int) else None,
        process_blocks=None,  # 设计 06：分身 verbose 不上云，feed 也只载最终文本
        write_sync_event=False,  # feed 由 /sync/push 的 _append_message_feed_event_idempotent 写
    )
