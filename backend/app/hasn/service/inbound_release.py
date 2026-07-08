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


def _preview_from_content(content: Any) -> str:
    """从消息 content（JSONB dict / 文本）提取一段简短预览文本（最长 80 字）。"""
    text = ''
    if isinstance(content, dict):
        raw = content.get('text') or content.get('content') or ''
        text = raw if isinstance(raw, str) else ''
    elif isinstance(content, str):
        text = content
    text = text.strip().replace('\n', ' ')
    return text[:80]


def _original_body_from_content(content: Any) -> str:
    """构造 daemon 端 `extract_text_from_body` 可解析的 `{"text": ...}` JSON 串。"""
    import json

    text = ''
    if isinstance(content, dict):
        raw = content.get('text') or content.get('content') or ''
        text = raw if isinstance(raw, str) else ''
    elif isinstance(content, str):
        text = content
    return json.dumps({'text': text}, ensure_ascii=False)


async def _resolve_sender_owners(
    db: AsyncSession, agent_sender_ids: list[str],
) -> dict[str, tuple[str, str | None]]:
    """批量解析一组发送分身（`a_` 前缀的 `from_id`）的主人 hasn_id + 昵称（RT1.5·§4.1「来源主人」）。

    两趟批量查（避免 N+1）：① 发送分身 → 主人 hasn_id（`HasnAgents.owner_id`）；② 主人 hasn_id →
    昵称（`HasnHumans.nickname`）。返回 `{from_id: (owner_hasn_id, owner_name)}`；远端分身 / 无从解析
    的发送方**不入表**（调用方回落 null，诚实不造假，零 fake）。
    """
    if not agent_sender_ids:
        return {}
    from backend.app.hasn.model.hasn_agents import HasnAgents
    from backend.app.hasn.model.hasn_humans import HasnHumans

    # ① 发送分身 → 主人 hasn_id
    agent_rows = (
        await db.execute(
            select(HasnAgents.hasn_id, HasnAgents.owner_id).where(
                HasnAgents.hasn_id.in_(agent_sender_ids)
            )
        )
    ).all()
    sender_to_owner = {r.hasn_id: r.owner_id for r in agent_rows if r.owner_id}
    if not sender_to_owner:
        return {}

    # ② 主人 hasn_id → 昵称（一次批量取，缺昵称留 None）
    owner_ids = list(set(sender_to_owner.values()))
    human_rows = (
        await db.execute(
            select(HasnHumans.hasn_id, HasnHumans.nickname).where(
                HasnHumans.hasn_id.in_(owner_ids)
            )
        )
    ).all()
    name_by_owner = {r.hasn_id: (r.nickname or None) for r in human_rows}

    return {
        sender_id: (owner_hasn_id, name_by_owner.get(owner_hasn_id))
        for sender_id, owner_hasn_id in sender_to_owner.items()
    }


def _build_suppressed_item(
    row: Any, sender_owners: dict[str, tuple[str, str | None]],
) -> dict[str, Any]:
    """把一行抑制记录 + 预解析的发送方主人映射，拼成对 daemon 的 item（RT1.5 拦截箱列表）。

    纯函数（不碰 DB），便于单测断言 RT1.5 新增字段：
      - `sender_hasn_id`：发送方（`m.from_id`）hasn_id，供拦截卡展示「来源分身」；
      - `sender_owner_hasn_id` / `sender_owner_name`：来源分身的主人（远端 / 无从解析 → null）；
      - `pending_request_id`：拦截行 `policy_snapshot` 里关联的好友请求 id（无则 null）。
    其余既有字段（suppressed_id/message_id/... /message_preview/original_body）维持不变。
    """
    content = row['content']
    from_id = row.get('from_id')
    policy_snapshot = row.get('policy_snapshot') or {}
    pending_request_id = policy_snapshot.get('pending_request_id')
    owner_hasn_id, owner_name = sender_owners.get(from_id, (None, None)) if from_id else (None, None)
    return {
        'suppressed_id': str(row['suppressed_id']),
        'message_id': str(row['message_id']),
        'conversation_id': str(row['conversation_id']),
        'agent_hasn_id': row['hasn_id'],
        'reason': row['suppress_reason'] or 'permission_denied',
        'created_at': int(row['created_time'].timestamp()) if row['created_time'] else 0,
        'message_preview': _preview_from_content(content),
        'original_body': _original_body_from_content(content),
        # RT1.5·§4.1：拦截卡展示「关联好友请求 + 来源主人」——无值一律 null（零 fake）
        'sender_hasn_id': from_id,
        'sender_owner_hasn_id': owner_hasn_id,
        'sender_owner_name': owner_name,
        'pending_request_id': str(pending_request_id) if pending_request_id is not None else None,
    }


async def list_suppressed_for_owner(db: AsyncSession, *, owner_id: str) -> list[dict[str, Any]]:
    """列出主人名下、对主人可见的全部被抑制消息（门控 + 运行时入站类），供 daemon 镜像桥拉取。

    owner 隔离：只返回 `owner_id` 名下 `visible_to_owner=true` 的行。每行携带 `suppress_reason`
    （daemon 据此分诊放行三分）、`message_preview`、`original_body`，按 message_id 升序。

    RT1.5·§4.1：额外携带「关联好友请求 + 来源主人」——`pending_request_id`（取自
    `policy_snapshot`）、`sender_hasn_id`（`m.from_id`）、`sender_owner_hasn_id` /
    `sender_owner_name`（批量解析发送分身的主人；远端 / 无从解析留 null）。
    """
    rows = (
        await db.execute(
            sa.text(
                """
                SELECT s.id              AS suppressed_id,
                       s.message_id      AS message_id,
                       s.owner_id        AS owner_id,
                       s.hasn_id         AS hasn_id,
                       s.conversation_id::text AS conversation_id,
                       s.suppress_reason AS suppress_reason,
                       s.policy_snapshot AS policy_snapshot,
                       s.created_time    AS created_time,
                       m.from_id         AS from_id,
                       m.content         AS content
                FROM public.hasn_suppressed_messages s
                LEFT JOIN public.hasn_messages m ON m.id = s.message_id
                WHERE s.owner_id = :owner_id
                  AND s.visible_to_owner = true
                ORDER BY s.message_id ASC
                """
            ),
            {'owner_id': owner_id},
        )
    ).mappings().all()

    # 先批量解析「来源主人」：只对分身发送方（`a_` 前缀）解析，避免逐行 N+1 查询
    agent_sender_ids = list({
        row['from_id'] for row in rows
        if row['from_id'] and str(row['from_id']).startswith('a_')
    })
    sender_owners = await _resolve_sender_owners(db, agent_sender_ids)

    return [_build_suppressed_item(row, sender_owners) for row in rows]


async def _load_suppressed(db: AsyncSession, *, owner_id: str, message_id: int) -> HasnSuppressedMessages | None:
    """加载主人名下的抑制箱行（owner 隔离：只能放行自己的抑制消息）。"""
    result = await db.execute(
        select(HasnSuppressedMessages).where(
            HasnSuppressedMessages.message_id == message_id,
            HasnSuppressedMessages.owner_id == owner_id,
        )
    )
    return result.scalar_one_or_none()


async def _resolve_peer_owner(db: AsyncSession, peer_id: str) -> str | None:
    """peer 是分身时解析其主人 hasn_id（写进 peer_owner_id，供后续主人派生/展示）。"""
    if not peer_id.startswith('a_'):
        return None
    from backend.app.hasn.model.hasn_agents import HasnAgents

    row = await db.execute(select(HasnAgents.owner_id).where(HasnAgents.hasn_id == peer_id))
    return row.scalar_one_or_none()


async def _upsert_contact_trust(
    db: AsyncSession, *, owner_id: str, peer_id: str, peer_type: str,
) -> HasnContacts:
    """放行三合一第①步：建/升 peer 联系人到普通信任（≥2）→ 下次同 peer 入站矩阵直接放行。

    已存在 → 取 max(现值, 2) 不降级、status 转 connected（若曾 blocked 则解封）；
    不存在 → 新建普通联系人（social/connected，add_source=inbound_release，peer 是分身则回填
    peer_owner_id）。返回该联系人行，供 resulting_contact_id 审计链回填。
    """
    peer_owner_id = await _resolve_peer_owner(db, peer_id)
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
        if peer_owner_id and not existing.peer_owner_id:
            existing.peer_owner_id = peer_owner_id
        return existing
    contact = HasnContacts(
        owner_id=owner_id,
        peer_id=peer_id,
        peer_owner_id=peer_owner_id,
        peer_type=peer_type,
        relation_type='social',
        trust_level=NORMAL_TRUST_LEVEL,
        status='connected',
        add_source='inbound_release',
    )
    db.add(contact)
    await db.flush()
    return contact


async def _redeliver_all_suppressed_for_peer(
    db: AsyncSession, *, owner_id: str, peer_id: str,
) -> int:
    """放行三合一第③步：重投该 owner 名下、来自某 peer 的**全部**暂存拦截消息（不只当前这条）。

    联系人页 accept 与拦截箱放行共用（反向联动·D6）：同意即把该 peer 积压的所有暂存消息一并
    送达+删箱。返回重投条数。
    """
    rows = (
        await db.execute(
            sa.text(
                """
                SELECT s.message_id AS message_id
                FROM public.hasn_suppressed_messages s
                JOIN public.hasn_messages m ON m.id = s.message_id
                WHERE s.owner_id = :owner_id
                  AND s.visible_to_owner = true
                  AND m.from_id = :peer_id
                ORDER BY s.message_id ASC
                """
            ),
            {'owner_id': owner_id, 'peer_id': peer_id},
        )
    ).mappings().all()
    count = 0
    for r in rows:
        mid = r['message_id']
        msg = (await db.execute(select(HasnMessages).where(HasnMessages.id == mid))).scalar_one_or_none()
        if msg is None:
            continue
        await _deliver_message(db, msg, owner_id=owner_id)
        await db.execute(
            sa.delete(HasnSuppressedMessages).where(
                HasnSuppressedMessages.message_id == mid,
                HasnSuppressedMessages.owner_id == owner_id,
            )
        )
        count += 1
    return count


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


async def accept_first_contact_request(
    db: AsyncSession, *, request: Any, approver_id: str,
) -> dict[str, Any]:
    """联系人页 accept 一条「首联自动请求」= 与拦截箱放行三合一对称（反向联动·D6）。

    ① 建/升边 approver→请求发起实体（social 普通朋友，peer 是分身回填 peer_owner_id）；
    ② mark_accepted + resulting_contact_id；③ 重投该 peer 的**全部**暂存拦截消息。
    调用方（contacts respond 端点）在 request.add_source == 'auto_first_contact' 时改走本路径，
    以纠正通用 respond 建反向边的方向问题（首联请求 from=发送分身，需建 A→分身而非分身→A）。
    """
    from backend.app.hasn.crud.crud_hasn_contact_requests import hasn_contact_requests_dao

    from_id = request.from_id
    peer_type = 'agent' if from_id.startswith('a_') else 'human'
    contact = await _upsert_contact_trust(db, owner_id=approver_id, peer_id=from_id, peer_type=peer_type)
    await hasn_contact_requests_dao.mark_accepted(
        db, request.id, decided_by=approver_id, resulting_contact_id=contact.id,
    )
    redelivered = await _redeliver_all_suppressed_for_peer(db, owner_id=approver_id, peer_id=from_id)
    await db.commit()
    try:
        await sync_invalidate_service.bump_owner('suppressed', db, approver_id)
    except Exception as exc:
        log.warning(f'[inbound_release] bump suppressed failed: {exc}')
    return {'contact_id': contact.id, 'redelivered': redelivered, 'trust_level': contact.trust_level}


async def release_suppressed(
    db: AsyncSession,
    *,
    owner_id: str,
    message_id: int,
    mode: str = MODE_PERSIST,
) -> dict[str, Any]:
    """主人放行一条被门控的入站消息 = **同意并添加联系人**（D6 三合一，去 once/persist 两档）。

    统一语义（不想加对方 = 不放行，走拒绝/忽略）：
      ① accept 关联的好友请求（若拦截行 snapshot 带 pending_request_id）→ 写 resulting_contact_id；
      ② 建/升边 `A→peer`（social，普通朋友档，peer 是分身则回填 peer_owner_id）；
      ③ 重投该 peer 的**全部**暂存拦截消息（不只当前这条）+ 逐条出箱。

    `mode` 入参保留仅为 daemon/webui 旧调用兼容，**不再分叉**（放行只有一种语义）。
    social_disabled/manual_only 等非「关系类」原因：仅单条放行（不翻策略），维持原行为。
    返回 {released: bool, status, reason, ...}；released=False 表示分身需先恢复（agent_frozen）。
    owner 隔离：只能放行自己名下抑制箱的消息，否则 not_found。
    """
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
    redelivered = 1

    if reason == 'social_disabled':
        # 永久开社交（agent.social_enabled=true）——之后该分身对外部可达；单条放行即可
        from backend.app.hasn.model.hasn_agents import HasnAgents

        await db.execute(
            sa
            .update(HasnAgents)
            .where(HasnAgents.hasn_id == row.hasn_id, HasnAgents.owner_id == owner_id)
            .values(social_enabled=True)
        )
        await _deliver_message(db, msg, owner_id=owner_id)
        await db.execute(
            sa.delete(HasnSuppressedMessages).where(
                HasnSuppressedMessages.message_id == message_id,
                HasnSuppressedMessages.owner_id == owner_id,
            )
        )
    elif reason in TRUST_RAISING:
        # 放行三合一（D6）：① 建/升边 → ② accept 关联好友请求 → ③ 重投该 peer 全部暂存消息
        contact = await _upsert_contact_trust(db, owner_id=owner_id, peer_id=from_id, peer_type=from_type)
        pending_request_id = (row.policy_snapshot or {}).get('pending_request_id')
        if pending_request_id:
            from backend.app.hasn.crud.crud_hasn_contact_requests import hasn_contact_requests_dao

            await hasn_contact_requests_dao.mark_accepted(
                db, int(pending_request_id), decided_by=owner_id, resulting_contact_id=contact.id,
            )
        redelivered = await _redeliver_all_suppressed_for_peer(db, owner_id=owner_id, peer_id=from_id)
    else:
        # manual_only 等：单条放行（策略不翻，由主人在分身设置里改）
        await _deliver_message(db, msg, owner_id=owner_id)
        await db.execute(
            sa.delete(HasnSuppressedMessages).where(
                HasnSuppressedMessages.message_id == message_id,
                HasnSuppressedMessages.owner_id == owner_id,
            )
        )

    await db.commit()

    # WSPUSH suppressed 失效——多端 daemon 重拉镜像即移除已放行的行
    try:
        await sync_invalidate_service.bump_owner('suppressed', db, owner_id)
    except Exception as exc:
        log.warning(f'[inbound_release] bump suppressed failed: {exc}')

    return {
        'released': True,
        'status': 'delivered',
        'reason': reason,
        'message_id': message_id,
        'conversation_id': str(msg.conversation_id),
        'redelivered': redelivered,
    }
