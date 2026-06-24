"""入站门控放行（主人覆盖门控、真实投递被抑制的外部→Agent 消息）。

事实源：docs/hasn-node设计文档/05-安全与权限/06-入站消息门控与抑制箱(外部→Agent全门控).md

主人在抑制箱看到被门控的外部消息后，可主动放行。放行按 suppress_reason 分类覆盖门控：
  - social_disabled  + persist → 永久开社交（agent.social_enabled=true）；once → 仅放行本条
  - permission_denied/abuse_restricted + persist → 加联系人 / 提信任到普通(≥2)；once → 仅放行本条
  - agent_frozen     → 不放行（分身非 active，唤醒无意义），提示主人先恢复分身
  - manual_only      → 仅放行本条（策略不变）

放行成功 = 真实投递（重推 hasn.message.received + 写 message.received sync 事件供主人会话恢复）
+ 唤醒接收方节点 + 删抑制箱行 + WSPUSH suppressed 失效（让多端 daemon 同步移除镜像）。
权威 reason 取**已落库的 suppress_reason**（不信客户端传值，避免越权放行别的理由）。
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

import sqlalchemy as sa

from sqlalchemy import select

from backend.app.hasn.model.hasn_contacts import HasnContacts
from backend.app.hasn.model.hasn_messages import HasnMessages
from backend.app.hasn.model.hasn_suppressed_messages import HasnSuppressedMessages
from backend.app.hasn.service import sync_invalidate_service
from backend.common.log import log

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession

# 放行模式
MODE_ONCE = 'once'
MODE_PERSIST = 'persist'

# 不可放行（需先恢复分身）的门控理由
NON_RELEASABLE = {'agent_frozen'}

# 放行时按 persist 提信任的理由（permission/abuse 类——主人选「永久允许 peer」时建/升联系人）
TRUST_RAISING = {'permission_denied', 'abuse_restricted'}

# persist 放行后联系人最低信任等级（普通联系人，矩阵 social[2] send_message=ALLOW）
NORMAL_TRUST_LEVEL = 2

_CONTENT_TYPE_STR = {
    1: 'text',
    2: 'image/*',
    3: 'application/octet-stream',
    4: 'audio/*',
    5: 'application/x.card+json',
}


async def _load_suppressed(db: AsyncSession, *, owner_id: str, message_id: int) -> HasnSuppressedMessages | None:
    """加载主人名下的抑制箱行（owner 隔离：只能放行自己的抑制消息）。"""
    result = await db.execute(
        select(HasnSuppressedMessages).where(
            HasnSuppressedMessages.message_id == message_id,
            HasnSuppressedMessages.owner_id == owner_id,
        )
    )
    return result.scalar_one_or_none()


async def _upsert_contact_trust(db: AsyncSession, *, owner_id: str, peer_id: str, peer_type: str) -> None:
    """persist 放行：建/升 peer 联系人到普通信任（≥2），下次同 peer 入站矩阵直接放行。

    已存在 → 取 max(现值, 2) 不降级、status 转 connected（若曾 blocked 则解封）；
    不存在 → 新建普通联系人（social/connected，add_source=inbound_release）。
    """
    existing = (
        await db.execute(
            select(HasnContacts).where(
                HasnContacts.owner_id == owner_id,
                HasnContacts.peer_id == peer_id,
            )
        )
    ).scalar_one_or_none()
    if existing is not None:
        existing.trust_level = max(existing.trust_level or 0, NORMAL_TRUST_LEVEL)
        existing.status = 'connected'
        return
    db.add(
        HasnContacts(
            owner_id=owner_id,
            peer_id=peer_id,
            peer_type=peer_type,
            relation_type='social',
            trust_level=NORMAL_TRUST_LEVEL,
            status='connected',
            add_source='inbound_release',
        )
    )


async def _deliver_message(db: AsyncSession, msg: HasnMessages, *, owner_id: str) -> None:
    """重投已落库的受抑制消息：写 message.received sync 事件 + 推送给接收方/主人节点（唤醒）。

    复用 route_message 的投递形态（hasn.message.received 帧 + 同步事件），不二次落库——
    消息在 _suppress_inbound 时已 persist，这里只补「被门控时没做的投递+唤醒」。
    """
    from backend.app.hasn.service.hasn_sync_service import SqlAlchemySyncGateway
    from backend.app.hasn.service.message_router import _push_message_to
    from backend.app.hasn.service.ws_router import ws_router

    from_id = msg.from_id
    to_id = msg.to_id
    content = msg.content or {}
    content_type = msg.content_type or 1
    content_type_str = _CONTENT_TYPE_STR.get(content_type, 'text')

    # 接收方（被放行 Agent）owner 写 message.received，使主人会话列表恢复这条消息
    sync_gw = SqlAlchemySyncGateway()
    await sync_gw._append_sync_event(
        db,
        owner_id=owner_id,
        hasn_id=to_id,
        event_type='message.received',
        aggregate_type='message',
        aggregate_id=str(msg.id),
        payload={
            'message_id': str(msg.id),
            'conversation_id': str(msg.conversation_id),
            'owner_id': owner_id,
            'hasn_id': to_id,
            'sender_hasn_id': from_id,
            'recipient_hasn_id': to_id,
            'direction': 'inbound',
            'content_type': content_type_str,
            'content_body': content,
            'local_id': msg.local_id,
            'created_at': int(msg.created_time.timestamp()) if msg.created_time else 0,
            'released_from_suppression': True,
        },
    )
    await db.flush()

    from_entity_type = 'human' if from_id.startswith('h_') else ('agent' if from_id.startswith('a_') else 'system')
    to_entity_type = 'human' if to_id.startswith('h_') else ('agent' if to_id.startswith('a_') else 'system')
    hasn_envelope = {
        'id': msg.id,
        'conversation_id': str(msg.conversation_id),
        'from_id': from_id,
        'from_type': msg.from_type,
        'to_id': to_id,
        'to_type': msg.to_type,
        'content_type': content_type,
        'content': content,
        'msg_type': msg.msg_type or 'message',
        'status': 1,
        'priority': msg.priority or 'normal',
        'reply_to_id': msg.reply_to_id,
        'local_id': msg.local_id,
        'created_time': msg.created_time.isoformat() if msg.created_time else None,
        'from_owner_id': from_id if from_entity_type == 'human' else None,
        'to_owner_id': owner_id if to_entity_type == 'agent' else to_id,
        # 放行 = 主人显式覆盖门控，等价 ALLOW
        'permission': {'decision': 'ALLOW', 'reason': 'owner_released', 'allowed_fields': None},
    }
    payload = {
        'hasn': 'hasn/0.2',
        'method': 'hasn.message.received',
        'params': {'to_id': to_id, 'message': hasn_envelope},
    }
    # 投递给接收方实体节点（Agent 所在节点 → 唤醒 runtime）
    await _push_message_to(to_id, payload)
    # 主人在线节点（排除 Agent 所在节点，避免同节点收两遍）也作为 IM 客户端收到
    if to_entity_type == 'agent' and owner_id and owner_id != to_id:
        await ws_router.push_to_owner_excluding_agent_node(owner_id, to_id, payload)


async def release_suppressed(
    db: AsyncSession,
    *,
    owner_id: str,
    message_id: int,
    mode: str = MODE_ONCE,
) -> dict[str, Any]:
    """主人放行一条被门控的入站消息。

    返回 {released: bool, status, reason, message?}；released=False 表示分身需先恢复（agent_frozen）。
    owner 隔离：只能放行自己名下抑制箱的消息，否则 not_found。
    """
    mode = mode if mode in (MODE_ONCE, MODE_PERSIST) else MODE_ONCE
    row = await _load_suppressed(db, owner_id=owner_id, message_id=message_id)
    if row is None:
        return {'released': False, 'status': 'not_found', 'reason': None, 'message': '抑制消息不存在或不属于你'}

    reason = row.suppress_reason or 'permission_denied'

    # agent_frozen：分身非 active，放行无意义——提示主人先恢复分身，保留抑制行
    if reason in NON_RELEASABLE:
        return {
            'released': False,
            'status': 'agent_frozen',
            'reason': reason,
            'message': '分身已冻结/停用，请先恢复分身后再放行此消息',
        }

    msg = (await db.execute(select(HasnMessages).where(HasnMessages.id == message_id))).scalar_one_or_none()
    if msg is None:
        # 原消息已被清理（异常）——直接删抑制行收口，不投递
        await db.execute(sa.delete(HasnSuppressedMessages).where(HasnSuppressedMessages.message_id == message_id))
        await db.commit()
        return {'released': False, 'status': 'message_gone', 'reason': reason, 'message': '原始消息已不存在'}

    from_id = msg.from_id
    from_type = 'agent' if from_id.startswith('a_') else 'human'

    # 1. 按 reason + mode 应用持久化覆盖
    if mode == MODE_PERSIST:
        if reason == 'social_disabled':
            # 永久开社交（agent.social_enabled=true）——之后该分身对外部可达
            from backend.app.hasn.model.hasn_agents import HasnAgents

            await db.execute(
                sa
                .update(HasnAgents)
                .where(HasnAgents.hasn_id == row.hasn_id, HasnAgents.owner_id == owner_id)
                .values(social_enabled=True)
            )
        elif reason in TRUST_RAISING:
            # 永久允许此 peer（建/升联系人到普通信任）
            await _upsert_contact_trust(db, owner_id=owner_id, peer_id=from_id, peer_type=from_type)
        # manual_only persist 无持久副作用（策略由主人在分身设置里改，不在放行里翻策略）

    # 2. 真实投递 + 唤醒接收方
    await _deliver_message(db, msg, owner_id=owner_id)

    # 3. 删抑制箱行（放行即出箱）
    await db.execute(
        sa.delete(HasnSuppressedMessages).where(
            HasnSuppressedMessages.message_id == message_id,
            HasnSuppressedMessages.owner_id == owner_id,
        )
    )
    await db.commit()

    # 4. WSPUSH suppressed 失效——多端 daemon 重拉镜像即移除此条
    try:
        await sync_invalidate_service.bump_owner('suppressed', db, owner_id)
    except Exception as exc:
        log.warning(f'[inbound_release] bump suppressed failed: {exc}')

    return {
        'released': True,
        'status': 'delivered',
        'reason': reason,
        'mode': mode,
        'message_id': message_id,
        'conversation_id': str(msg.conversation_id),
    }
