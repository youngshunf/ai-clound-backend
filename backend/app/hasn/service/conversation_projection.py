"""会话一等实体：会话对象投影 + 受众计算 + message.new/conversation.updated 事件（doc02 §3.1–§3.4）。

本模块是「会话一等实体重构」的云端契约核心：
- **会话对象投影**（build_conversation_projection）：把 hasn_conversations（+群名册）序列化成 daemon 可消费的
  标准形态 `{conversation_id, type, participants[], group|null, revision, created_time, updated_time}`——
  daemon 的唯一会话认知来源，替代「从每条消息 payload 里猜会话长什么样」。
- **受众计算**（compute_audience_owner_ids）：`audience(conversation) = ⋃ resolve_owner(participant)`——
  投递链路唯一的「判断」，且是纯查表。A2A 会话两个分身各自解析出主人 → 两个主人都在受众里
  → A2AFIRST「要不要补推发送方」的补丁天然消失（doc02 §3.3）。
- **鉴权**：viewer 能看到一个会话 ⟺ viewer ∈ 受众（是参与者本人 / 参与分身的主人 / 群成员）——
  这就是「主人通道」的权限本体（doc02 §3.2）。
- **瘦事件**：message.new（替代 message.sent/received/agent_reply）+ conversation.updated（新增）。
"""
from __future__ import annotations

from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.app.hasn.model import HasnConversations
from backend.app.hasn.model.hasn_agents import HasnAgents
from backend.app.hasn.model.hasn_group_members import HasnGroupMembers

# ─── 事件类型常量（doc02 §3.2）───
# 投递/同步事件瘦身为唯一形态 message.new（方向是渲染概念，由 sender 派生，不落事件）。
MESSAGE_NEW_EVENT_TYPE = 'message.new'
# 会话元数据变更事件：建群/改名/加退成员/策略变更时写给相关 owner 的 feed，daemon 按 revision 判是否重拉。
CONVERSATION_UPDATED_EVENT_TYPE = 'conversation.updated'

# content_type 数字 → MIME 串（与 message_router 现有口径一致，供瘦事件 content_type 字段用）。
_CONTENT_TYPE_MIME = {
    1: 'text',
    2: 'image/*',
    3: 'application/octet-stream',
    4: 'audio/*',
    5: 'application/x.card+json',
}


def content_type_to_mime(content_type: int) -> str:
    """content_type 数字枚举 → MIME 串（未知回退 text）。"""
    return _CONTENT_TYPE_MIME.get(content_type, 'text')


def _entity_type_str(hasn_id: str) -> str:
    """hasn_id 前缀 → 参与者类型串（human/agent/service/group/system）。"""
    if hasn_id.startswith('h_'):
        return 'human'
    if hasn_id.startswith('a_'):
        return 'agent'
    if hasn_id.startswith('sv_'):
        return 'service'
    if hasn_id.startswith('g:'):
        return 'group'
    return 'system'


async def _resolve_owner_ids(db: AsyncSession, hasn_ids: list[str]) -> dict[str, str | None]:
    """批量把参与者 hasn_id 解析为「其归属 owner 的 human hasn_id」。

    - human（h_）→ 自己就是 owner；
    - agent（a_）→ 查 hasn_agents.owner_id；
    - service/system → 无 owner（None，不进受众——服务号消息由 route 另行处理，本模块不扇出到它）。

    一次查询批量解析所有 agent，避免逐个 N+1。
    """
    resolved: dict[str, str | None] = {}
    agent_ids: list[str] = []
    for hid in hasn_ids:
        if not hid:
            continue
        if hid.startswith('h_'):
            resolved[hid] = hid
        elif hid.startswith('a_'):
            agent_ids.append(hid)
        else:
            resolved[hid] = None
    if agent_ids:
        rows = await db.execute(
            select(HasnAgents.hasn_id, HasnAgents.owner_id).where(HasnAgents.hasn_id.in_(agent_ids))
        )
        owner_by_agent = {r[0]: r[1] for r in rows.all()}
        for aid in agent_ids:
            resolved[aid] = owner_by_agent.get(aid)
    return resolved


def _direct_participant_hasn_ids(conv: HasnConversations) -> list[str]:
    ids = [conv.participant_a_id]
    if conv.participant_b_id:
        ids.append(conv.participant_b_id)
    return [i for i in ids if i]


async def _load_group_members(db: AsyncSession, conversation_id: str) -> list[HasnGroupMembers]:
    rows = await db.execute(
        select(HasnGroupMembers).where(HasnGroupMembers.conversation_id == conversation_id)
    )
    return list(rows.scalars().all())


async def _fetch_conversation(db: AsyncSession, conversation_id: str) -> HasnConversations | None:
    """按 id 取会话行（抽出以便单测 monkeypatch）。"""
    row = await db.execute(select(HasnConversations).where(HasnConversations.id == conversation_id))
    return row.scalar_one_or_none()


async def compute_audience_owner_ids(
    db: AsyncSession,
    conv: HasnConversations,
    *,
    members: list[HasnGroupMembers] | None = None,
) -> list[str]:
    """受众计算（doc02 §3.3）：会话参与者展开出的 owner 集合（去重、稳定排序）。

    direct → 两方各解析 owner；group → 名册每个成员解析 owner。这是投递 fan-out 的目标，
    也是鉴权判据（viewer ∈ audience ⟺ 有权看到该会话）。
    """
    if conv.type == 'group':
        if members is None:
            members = await _load_group_members(db, str(conv.id))
        participant_ids = [m.member_id for m in members if m.member_id]
    else:
        participant_ids = _direct_participant_hasn_ids(conv)

    resolved = await _resolve_owner_ids(db, participant_ids)
    owners = {o for o in resolved.values() if o}
    return sorted(owners)


def build_conversation_projection(
    conv: HasnConversations,
    *,
    members: list[HasnGroupMembers] | None = None,
    viewer_owner_hasn_id: str | None = None,
) -> dict[str, Any]:
    """会话对象投影（doc02 §3.1）：daemon 消费的标准序列化。

    形态：`{conversation_id, type, participants:[{hasn_id, hasn_type, role}], group|null,
    revision, created_time, updated_time}`。direct 的 participants 是 A/B 两方；group 从名册取。

    ``viewer_owner_hasn_id``（doc14 §6.5）：观看者 owner，用于 ``mission_note`` 的**私有裁剪**
    ——差事背景是发送方 owner 的私有框定，只对 ``mission_note_owner_id`` 序列化，对端 owner
    一律不含该键。不传 viewer（内部/无观看者语境）→ 一律裁剪（fail-closed，宁可不给）。
    """
    if conv.type == 'group':
        member_list = members or []
        participants = [
            {
                'hasn_id': m.member_id,
                'hasn_type': m.member_type or _entity_type_str(m.member_id),
                'role': m.role or 'member',
            }
            for m in member_list
            if m.member_id
        ]
        group_meta: dict[str, Any] | None = {
            'group_id': conv.group_id,
            'name': conv.group_name,
            'avatar': conv.group_avatar_url,
            'owner_id': conv.group_owner_id,
            'description': conv.group_description,
            'agent_policy': conv.agent_policy,
        }
    else:
        participants = [
            {'hasn_id': conv.participant_a_id, 'hasn_type': conv.participant_a_type or _entity_type_str(conv.participant_a_id), 'role': 'member'},
        ]
        if conv.participant_b_id:
            participants.append(
                {'hasn_id': conv.participant_b_id, 'hasn_type': conv.participant_b_type or _entity_type_str(conv.participant_b_id), 'role': 'member'}
            )
        group_meta = None

    projection: dict[str, Any] = {
        'conversation_id': str(conv.id),
        'type': conv.type or 'direct',
        'participants': participants,
        'group': group_meta,
        'revision': int(conv.revision or 1),
        'created_time': conv.created_time.isoformat() if getattr(conv, 'created_time', None) else None,
        'updated_time': conv.updated_time.isoformat() if getattr(conv, 'updated_time', None) else None,
    }

    # doc14 §6.5：差事背景仅对其归属 owner（= 发起方）序列化，对端 owner / 无 viewer 语境一律裁剪。
    mission_note = getattr(conv, 'mission_note', None)
    mission_owner = getattr(conv, 'mission_note_owner_id', None)
    if mission_note and viewer_owner_hasn_id and viewer_owner_hasn_id == mission_owner:
        projection['mission_note'] = mission_note

    return projection


async def load_conversation_object(
    db: AsyncSession,
    conversation_id: str,
    *,
    viewer_owner_hasn_id: str,
) -> dict[str, Any] | None:
    """按 conversation_id 拉会话对象投影（daemon 详情读端点本体，doc02 §3.2）。

    鉴权：viewer_owner_hasn_id 必须是参与者本人 / 参与分身的主人 / 群成员（= viewer ∈ 受众）。
    - 会话不存在 → None（端点回 404）。
    - 无权限（viewer ∉ 受众）→ None（端点回 403，语义上不泄露存在性由端点决定；本函数只判权）。
    - 有权限 → 返回投影 dict。
    """
    conv = await _fetch_conversation(db, conversation_id)
    if conv is None:
        return None

    members = await _load_group_members(db, str(conv.id)) if conv.type == 'group' else None
    audience = await compute_audience_owner_ids(db, conv, members=members)
    if viewer_owner_hasn_id not in audience:
        return None

    return build_conversation_projection(conv, members=members, viewer_owner_hasn_id=viewer_owner_hasn_id)


async def bump_conversation_revision(db: AsyncSession, conversation_id: str) -> int:
    """会话元数据变更时 +1 revision，返回新值（成员/群名/策略变更调用，doc02 §3.2）。"""
    row = await db.execute(select(HasnConversations).where(HasnConversations.id == conversation_id))
    conv = row.scalar_one_or_none()
    if conv is None:
        return 0
    conv.revision = int(conv.revision or 1) + 1
    await db.flush()
    return int(conv.revision)


async def emit_conversation_updated(
    sync_gw: Any,
    db: AsyncSession,
    *,
    conversation_id: str,
    revision: int,
    owner_ids: list[str],
) -> None:
    """给相关 owner 的 feed 各写一条 conversation.updated（daemon 按 revision 判是否重拉，doc02 §3.2）。"""
    for owner_id in sorted(set(o for o in owner_ids if o)):
        await sync_gw._append_sync_event(
            db,
            owner_id=owner_id,
            hasn_id=owner_id,
            event_type=CONVERSATION_UPDATED_EVENT_TYPE,
            aggregate_type='conversation',
            aggregate_id=str(conversation_id),
            payload={'conversation_id': str(conversation_id), 'revision': int(revision)},
        )


async def append_message_new_event(
    sync_gw: Any,
    db: AsyncSession,
    *,
    owner_id: str,
    conversation_id: str,
    message_id: str,
    sender_hasn_id: str,
    origin_node_id: str | None,
    content_type: int,
    content_body: dict,
    local_id: str | None,
    created_at: int,
    origin_session_id: str | None = None,
) -> int:
    """给某受众 owner 的 feed 写一条瘦事件 message.new（doc02 §3.2）。

    形态：`{conversation_id, message_id, sender_hasn_id, origin_node_id, content_type(MIME),
    content_body, local_id, created_at}`——不再携带 relation_view/peer/conversation_type/group_name，
    方向由 sender 在渲染时派生。幂等键 = (owner_id, aggregate_id=message_id, event_type)，由
    _append_message_feed_event_idempotent / 上层去重保证。

    ``origin_session_id``（doc14 §6.2）：**条件字段**，只有发送方 owner 的那份事件才传值
    （受众分叉由调用方 ``_fanout_message_new`` 判定，本函数只负责「给了就带、没给就不带」）。
    传 None 时 payload 里**不出现该键**——对端 owner 的事件形态与 doc02 原样一致。
    """
    payload: dict[str, Any] = {
        'conversation_id': str(conversation_id),
        'message_id': str(message_id),
        'sender_hasn_id': sender_hasn_id,
        'origin_node_id': origin_node_id,
        'content_type': content_type_to_mime(content_type),
        'content_body': content_body,
        'local_id': local_id,
        'created_at': int(created_at),
    }
    if origin_session_id:
        payload['origin_session_id'] = origin_session_id

    return await sync_gw._append_sync_event(
        db,
        owner_id=owner_id,
        hasn_id=owner_id,
        event_type=MESSAGE_NEW_EVENT_TYPE,
        aggregate_type='message',
        aggregate_id=str(message_id),
        payload=payload,
    )
