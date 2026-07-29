"""HASN 消息路由核心服务

实现消息路由全流程（对齐协议 02-消息与通信.md §3.1）：
认证 → 目标解析 → 关系查询 → 权限检查（三维矩阵）→ 铁律检查 → 持久化 → 投递
"""

import uuid

from typing import Any, cast

from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession

from backend.app.hasn.constants import (
    ALLOW,
    CONFIRM,
    DENY,
    SCOPE_LTD,
    check_action_permission,
)
from backend.app.hasn.crud.crud_hasn_conversations import hasn_conversations_dao
from backend.app.hasn.model import HasnConversations, HasnMessages
from backend.app.hasn_core import HasnAgents, HasnHumans
from backend.app.hasn.model.hasn_conversation_memberships import HasnConversationMemberships
from backend.app.hasn.model.hasn_contacts import HasnContacts
from backend.app.hasn.model.hasn_conversation_memberships import (
    HasnConversationMemberships as HasnGroupMembers,
)
from backend.app.hasn.service import sync_invalidate_service

# 入站门控（外部→Agent 全门控，设计 05/06）：五闸判定 + 抑制记录
from backend.app.hasn.service.inbound_gatekeeper import (
    REJECT_SILENT,
    SUPPRESS,
    evaluate_a2h_inbound,
    evaluate_inbound,
    record_suppression,
    suppression_command_identity,
)

# Phase 7 (07-02): A 路线中央判决器；替换 check_relation_permission 在 route_message 中的调用
from backend.app.hasn.service.permission_engine import permission_engine
from backend.app.hasn_im.application import membership_service
from backend.app.hasn_im.application.event_appender import append_event
from backend.app.hasn_im.consumers.facts import IM_MESSAGE_COMMITTED
from backend.common.log import log
from backend.database.schema_names import SCHEMA_NAMES
from backend.utils.timezone import timezone


_node_session_gateway = None


def _get_node_session_gateway():
    """延迟获取会话网关，避免 provider 与 message_router 的循环依赖。"""
    global _node_session_gateway
    if _node_session_gateway is None:
        from backend.app.hasn_im.application.provider import get_node_session_gateway

        _node_session_gateway = get_node_session_gateway()
    return _node_session_gateway


# ─── 目标解析 ───


async def _push_message_to(hasn_id: str, payload: dict[str, Any]) -> None:
    """延迟导入 WS 路由器，避免服务模块启动时与 binding_event_service 循环导入。"""
    gateway = cast(Any, _get_node_session_gateway())
    await gateway.push_message_to(hasn_id, payload)


async def resolve_target(db: AsyncSession, target: str) -> dict[str, Any] | None:
    """
    解析目标地址（Star ID 或 HASN ID）→ 实体信息

    返回: {hasn_id, star_id, entity_type, name} 或 None
    """
    # 直接是 HASN ID
    if target.startswith('h_'):
        human_result = await db.execute(select(HasnHumans).where(HasnHumans.hasn_id == target))
        human = human_result.scalar_one_or_none()
        if human:
            return {
                'hasn_id': human.hasn_id,
                'star_id': human.star_id,
                'entity_type': 'human',
                'name': human.nickname,  # HasnHumans 使用 nickname 字段
            }
        return None

    if target.startswith('a_'):
        agent_result = await db.execute(select(HasnAgents).where(HasnAgents.hasn_id == target))
        agent = agent_result.scalar_one_or_none()
        if agent:
            return {
                'hasn_id': agent.hasn_id,
                'star_id': agent.star_id,
                'entity_type': 'agent',
                'name': agent.display_name,  # HasnAgents 使用 display_name 字段
                'owner_id': agent.owner_id,
            }
        return None

    # 群组公开 ID（g:500001）解析为群会话。
    # HASN 群组暂以 hasn_conversations(type='group') 作为群主表，group_id 是协议层公开标识。
    if target.startswith('g:'):
        group_result = await db.execute(
            select(HasnConversations).where(
                HasnConversations.type == 'group',
                HasnConversations.group_id == target,
                HasnConversations.status == 'active',
            )
        )
        group = group_result.scalar_one_or_none()
        if group:
            return {
                'hasn_id': group.group_id,
                'star_id': group.group_id,
                'entity_type': 'group',
                'name': group.group_name or group.group_id,
                'conversation_id': str(group.id),
                'owner_id': group.group_owner_id,
            }
        return None

    # Star ID 解析
    if '#' in target:
        # Agent Star ID: 100001#star
        agent_by_star_result = await db.execute(select(HasnAgents).where(HasnAgents.star_id == target))
        agent_by_star = agent_by_star_result.scalar_one_or_none()
        if agent_by_star:
            return {
                'hasn_id': agent_by_star.hasn_id,
                'star_id': agent_by_star.star_id,
                'entity_type': 'agent',
                'name': agent_by_star.display_name,  # HasnAgents 使用 display_name 字段
                'owner_id': agent_by_star.owner_id,
            }
    else:
        # Human Star ID: 100001 或 fuzi
        human_by_star_result = await db.execute(select(HasnHumans).where(HasnHumans.star_id == target))
        human_by_star = human_by_star_result.scalar_one_or_none()
        if human_by_star:
            return {
                'hasn_id': human_by_star.hasn_id,
                'star_id': human_by_star.star_id,
                'entity_type': 'human',
                'name': human_by_star.nickname,  # HasnHumans 使用 nickname 字段
            }

    return None


# ─── 关系与权限检查 ───


async def check_relation_permission(
    db: AsyncSession,
    sender_id: str,
    receiver_id: str,
    msg_type: str = 'message',
) -> dict[str, Any]:
    """
    检查发送方与接收方之间的关系和权限（集成三维权限矩阵）

    返回: {allowed: bool, relation_type, trust_level, reason,
           permission_state: allow/deny/confirm_required/scope_limited}
    """
    # 自己给自己发 → 始终允许（Owner 控制权）
    if sender_id == receiver_id:
        return {'allowed': True, 'relation_type': 'social', 'trust_level': 5, 'permission_state': ALLOW}

    # 检查是否是 Owner 给自己的 Agent 发消息
    if sender_id.startswith('h_') and receiver_id.startswith('a_'):
        agent_result = await db.execute(
            select(HasnAgents).where(
                HasnAgents.hasn_id == receiver_id,
                HasnAgents.owner_id == sender_id,
            )
        )
        if agent_result.scalar_one_or_none():
            return {'allowed': True, 'relation_type': 'social', 'trust_level': 5, 'permission_state': ALLOW}

    # Agent 给自己的 Owner 发消息 → 始终允许
    if sender_id.startswith('a_') and receiver_id.startswith('h_'):
        agent_result = await db.execute(
            select(HasnAgents).where(
                HasnAgents.hasn_id == sender_id,
                HasnAgents.owner_id == receiver_id,
            )
        )
        if agent_result.scalar_one_or_none():
            return {'allowed': True, 'relation_type': 'social', 'trust_level': 5, 'permission_state': ALLOW}

    # 同一 Owner 的 Agent 之间 → 始终允许
    if sender_id.startswith('a_') and receiver_id.startswith('a_'):
        sender_agent = await db.execute(select(HasnAgents.owner_id).where(HasnAgents.hasn_id == sender_id))
        receiver_agent = await db.execute(select(HasnAgents.owner_id).where(HasnAgents.hasn_id == receiver_id))
        s_owner = sender_agent.scalar()
        r_owner = receiver_agent.scalar()
        if s_owner and r_owner and s_owner == r_owner:
            return {'allowed': True, 'relation_type': 'social', 'trust_level': 5, 'permission_state': ALLOW}

    # 查询关系记录（双向查找，取发送方视角）
    # 对于 Agent，用其 Owner 的身份查关系
    sender_lookup = sender_id
    receiver_lookup = receiver_id

    if sender_id.startswith('a_'):
        result = await db.execute(select(HasnAgents.owner_id).where(HasnAgents.hasn_id == sender_id))
        owner = result.scalar()
        if owner:
            sender_lookup = owner

    if receiver_id.startswith('a_'):
        result = await db.execute(select(HasnAgents.owner_id).where(HasnAgents.hasn_id == receiver_id))
        owner = result.scalar()
        if owner:
            receiver_lookup = owner

    # 查询关系
    relation_result = await db.execute(
        select(HasnContacts).where(
            HasnContacts.owner_id == sender_lookup,
            HasnContacts.peer_id == receiver_lookup,
            HasnContacts.status == 'connected',
        )
    )
    relation = relation_result.scalar_one_or_none()

    # 拉黑双向强制：对方是否拉黑了我（receiver → sender 方向，trust_level=0）。
    # 现网历史只查发送方单向，导致"被拉黑方仍能发消息"。任一方向 blocked 一律拒绝，
    # 且对 contact_request 类消息同样生效（被拉黑就不能再申请加好友）。
    reverse_blocked = await db.execute(
        select(HasnContacts.id).where(
            HasnContacts.owner_id == receiver_lookup,
            HasnContacts.peer_id == sender_lookup,
            HasnContacts.trust_level == 0,
        )
    )
    if reverse_blocked.scalar() is not None:
        return {
            'allowed': False,
            'relation_type': relation.relation_type if relation else 'social',
            'trust_level': 0,
            'permission_state': 'deny',
            'reason': '已被对方拉黑',
        }

    if not relation:
        # 好友请求类消息不需要已有关系
        if msg_type in ('contact_request', 'contact_accept', 'contact_reject'):
            return {'allowed': True, 'relation_type': 'social', 'trust_level': 1, 'permission_state': ALLOW}
        return {
            'allowed': False,
            'relation_type': None,
            'trust_level': 0,
            'permission_state': 'deny',
            'reason': '双方无关系，请先添加好友',
        }

    # 铁律 1: trust_level=0 → 完全屏蔽
    if relation.trust_level == 0:
        return {
            'allowed': False,
            'relation_type': relation.relation_type,
            'trust_level': 0,
            'permission_state': 'deny',
            'reason': '已被对方拉黑',
        }

    # ── 三维权限矩阵检查 ──────────────────────────────
    # 将消息类型映射到行为类型
    action = _msg_type_to_action(msg_type)
    perm_state = check_action_permission(
        relation_type=relation.relation_type,
        trust_level=relation.trust_level,
        action=action,
        custom_permissions=relation.custom_permissions,
    )

    if perm_state == 'deny':
        reason_map = {
            1: '信任等级不足，仅允许好友请求',
            2: '权限不足',
        }
        return {
            'allowed': False,
            'relation_type': relation.relation_type,
            'trust_level': relation.trust_level,
            'permission_state': perm_state,
            'reason': reason_map.get(relation.trust_level, '权限不足'),
        }

    # scope_limited: 允许但标记需 scope 限制（由业务层进一步控制）
    return {
        'allowed': True,
        'relation_type': relation.relation_type,
        'trust_level': relation.trust_level,
        'permission_state': perm_state,
        # confirm_required: 调用方需要触发人类确认流程
        'requires_confirm': perm_state == CONFIRM,
    }


def _msg_type_to_action(msg_type: str) -> str:
    """将消息类型映射到权限矩阵的行为类型"""
    map_ = {
        'message': 'send_message',
        'text': 'send_message',
        'contact_request': 'send_message',
        'contact_accept': 'send_message',
        'contact_reject': 'send_message',
        'discovery_query': 'view_public_info',
        'schedule_query': 'view_schedule',
        'preference_query': 'view_preferences',
        'location_query': 'view_location',
        'appointment': 'make_appointment',
        'commitment': 'make_commitment',
        'sensitive_query': 'view_sensitive',
        'product_inquiry': 'product_inquiry',
        'trade_comm': 'trade_communication',
        'push_notification': 'send_push',
        'order_comm': 'order_communication',
        'decrypt_address': 'decrypt_address',
        'professional_consult': 'professional_consult',
    }
    return map_.get(msg_type, 'send_message')


# ─── 会话管理 ───


async def get_or_create_conversation(
    db: AsyncSession,
    participant_a_id: str,
    participant_a_type: str,
    participant_b_id: str,
    participant_b_type: str,
    relation_type: str = 'social',
    mission_note: str | None = None,
    mission_note_owner_id: str | None = None,
) -> HasnConversations:
    """获取或创建单聊会话。

    ``mission_note``（doc14 §6.5）：差事背景，**仅新建会话时写入**——既有会话直接返回、
    绝不覆盖（一个对外会话的差事背景由发起那一刻定调，后续消息不改写）。
    ``mission_note_owner_id`` 是其归属 owner（= 发起方 owner），投影裁剪据此判定。

    唯一性不变量：一对参与者（排序后 a<b）只允许**一个** direct 会话，
    **与 relation_type 无关**——同一对人/人-agent 永远收敛到同一行
    （对齐 DB partial unique index `uq_hasn_conversations_direct`）。
    relation_type 仅作为新建时的初值，不参与查找键。

    并发安全：用事务级 advisory lock 按「参与者对」分桶串行化「查-改」，
    消除并发下两个事务都 SELECT 不到对方未提交行、双双 INSERT 出重复行的
    竞态（同 `hasn_sync_service` 的 revision 分配套路）。锁随事务结束自动释放。
    """
    # 排序保证唯一性（与 DB partial unique index 一致）
    if participant_a_id > participant_b_id:
        participant_a_id, participant_b_id = participant_b_id, participant_a_id
        participant_a_type, participant_b_type = participant_b_type, participant_a_type

    # 事务级 advisory lock：按参与者对分桶串行化查改，杜绝重复行竞态。
    await db.execute(
        text('SELECT pg_advisory_xact_lock(hashtext(:pair_key))'),
        {'pair_key': f'hasn_conv_direct:{participant_a_id}:{participant_b_id}'},
    )

    # 查找已有会话（仅按 type+参与者对，不含 relation_type）；存量去重前可能有
    # 多行，取最早 created_time 为 canonical，与清洗迁移的选取口径一致。
    result = await db.execute(
        select(HasnConversations)
        .where(
            HasnConversations.type == 'direct',
            HasnConversations.participant_a_id == participant_a_id,
            HasnConversations.participant_b_id == participant_b_id,
        )
        .order_by(HasnConversations.created_time.asc())
    )
    conv = result.scalars().first()

    if conv:
        return conv

    # 创建新会话（direct 标准默认值，与 hasn_conversations_service.ensure_conversation 对齐）
    conv = HasnConversations(
        type='direct',
        relation_type=relation_type,
        participant_a_id=participant_a_id,
        participant_b_id=participant_b_id,
        participant_a_type=participant_a_type,
        participant_b_type=participant_b_type,
        agent_policy='free',
        join_policy='',
        max_members=2,
        allow_invite=False,
        mute_all=False,
        member_count=2,
        message_count=0,
        status='active',
        # doc14 §6.5：差事背景只在建会话这一刻落（上方 `if conv: return conv` 已保证既有会话不覆盖）。
        mission_note=mission_note or None,
        mission_note_owner_id=(mission_note_owner_id or None) if mission_note else None,
    )
    db.add(conv)
    await db.flush()
    db.add_all(
        [
            HasnConversationMemberships(
                conversation_id=conv.id,
                member_hasn_id=participant_a_id,
                member_type=participant_a_type,
                role='member',
                joined_seq=1,
                read_seq=0,
                state='active',
                joined_at=timezone.now(),
                history_complete_from_seq=1,
            ),
            HasnConversationMemberships(
                conversation_id=conv.id,
                member_hasn_id=participant_b_id,
                member_type=participant_b_type,
                role='member',
                joined_seq=1,
                read_seq=0,
                state='active',
                joined_at=timezone.now(),
                history_complete_from_seq=1,
            ),
        ]
    )
    await db.flush()
    return conv


async def get_group_conversation(db: AsyncSession, group_id: str) -> HasnConversations | None:
    """按协议层 group_id 读取活跃群会话。"""
    result = await db.execute(
        select(HasnConversations).where(
            HasnConversations.type == 'group',
            HasnConversations.group_id == group_id,
            HasnConversations.status == 'active',
        )
    )
    return result.scalar_one_or_none()


async def list_group_members(db: AsyncSession, conversation_id: str) -> list[HasnGroupMembers]:
    """列出群活动成员周期。"""
    result = await db.execute(
        select(HasnGroupMembers).where(
            HasnGroupMembers.conversation_id == conversation_id,
            HasnGroupMembers.left_seq.is_(None),
            HasnGroupMembers.state == 'active',
        )
    )
    return list(result.scalars().all())


async def check_group_send_permission(
    db: AsyncSession,
    conversation_id: str,
    sender_id: str,
    group: HasnConversations,
) -> dict[str, Any]:
    """群消息发送权限：必须是成员；全员禁言时仅 owner/admin 可发。"""
    result = await db.execute(
        select(HasnGroupMembers).where(
            HasnGroupMembers.conversation_id == conversation_id,
            HasnGroupMembers.member_id == sender_id,
            HasnGroupMembers.left_seq.is_(None),
            HasnGroupMembers.state == 'active',
        )
    )
    member = result.scalar_one_or_none()
    if not member:
        return {'allowed': False, 'reason': '不是该群成员'}
    if group.mute_all and member.role not in ('owner', 'admin'):
        return {'allowed': False, 'reason': '群已全员禁言'}
    return {'allowed': True, 'member': member}


async def _agent_owner_id(db: AsyncSession, agent_id: str) -> str | None:
    result = await db.execute(select(HasnAgents.owner_id).where(HasnAgents.hasn_id == agent_id))
    return result.scalar_one_or_none()


async def _delivery_targets_for_member(db: AsyncSession, member: HasnGroupMembers) -> list[str]:
    """返回群成员消息应投递到的在线实体。

    Agent 成员除了尝试投递给 Agent Runtime，也投递给 Owner 在线节点。
    这样 Runtime 不在线/不存在时，Human 节点仍能作为纯 IM 客户端收到发给自己 Agent 的消息。
    """
    if member.member_type == 'agent' or member.member_id.startswith('a_'):
        owner_id = await _agent_owner_id(db, member.member_id)
        return [x for x in (member.member_id, owner_id) if x]
    return [member.member_id]


async def increment_unread_for(db: AsyncSession, conversation_id: str, hasn_id: str) -> None:
    """按现存消息精确重建指定成员的未读投影。

    ``conversation_seq`` 允许撤回、迁移与失败恢复形成空洞，不能用
    ``current_seq - read_seq`` 推算条数。
    """
    membership = await membership_service.get_active_epoch(
        db,
        conversation_id,
        hasn_id,
    )
    if membership is None:
        return
    conversation = await db.get(HasnConversations, conversation_id)
    if conversation is None:
        raise ValueError(f'会话 {conversation_id} 不存在，无法重建未读投影')
    await membership_service.rebuild_unread_projection(
        db,
        conversation_id,
        hasn_id,
        current_seq=conversation.current_seq,
    )


# ─── 消息持久化 ───


def _entity_type_int(hasn_id: str) -> int:
    """hasn_id → from_type/to_type 数字"""
    if hasn_id.startswith('h_'):
        return 1  # human
    if hasn_id.startswith('a_'):
        return 2  # agent
    if hasn_id.startswith('g:'):
        return 4  # group
    if hasn_id.startswith('sv_'):
        return 5  # service（服务号，统一通知服务 D8；不复用 3=system）
    return 3  # system


def _entity_type_str(hasn_id: str) -> str:
    """hasn_id → 会话 participant_*_type 字符串（与 _entity_type_int 同口径）。

    统一通知服务 D8：服务号 sv_ 作为会话参与方必须落 'service'（不是误判成 'agent'），
    与「服务号 ⇄ 主人」会话的 participant 类型保持一致。
    """
    if hasn_id.startswith('h_'):
        return 'human'
    if hasn_id.startswith('a_'):
        return 'agent'
    if hasn_id.startswith('sv_'):
        return 'service'
    if hasn_id.startswith('g:'):
        return 'group'
    return 'system'


def _asset_id_from_uri(uri: Any) -> str | None:
    """hasn://asset/{asset_id} → asset_id。"""
    if isinstance(uri, str) and uri.startswith('hasn://asset/'):
        candidate = uri[len('hasn://asset/') :].strip('/')
        return candidate or None
    return None


async def _grant_private_attachments(
    db: AsyncSession,
    conversation_id: str,
    message_id: int,
    content: dict | None,
) -> None:
    """为私有附件原子写会话 grant 与删除保护 binding。"""
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
    from backend.app.hasn.service.owner_storage_service import OwnerStorageService

    assets = await hasn_asset_service.get_many(db, asset_ids)
    for asset in assets.values():
        if asset.access == 'private':
            await hasn_asset_service.grant_to_conversation(db, asset_id=asset.asset_id, conversation_id=conversation_id)
            await OwnerStorageService.bind_asset_in_transaction(
                db,
                owner_hasn_id=asset.owner_hasn_id,
                asset_id=asset.asset_id,
                resource_uri=f'hasn://messages/c/{conversation_id}#{message_id}',
                role='attachment',
            )


async def persist_message(
    db: AsyncSession,
    conversation_id: str,
    from_id: str,
    to_id: str,
    content: dict,
    content_type: int = 1,
    msg_type: str = 'message',
    priority: str = 'normal',
    reply_to_id: int | None = None,
    local_id: str | None = None,
    context: dict | None = None,
    process_blocks: list[dict[str, Any]] | None = None,
    mentions: list[dict[str, Any]] | None = None,
    mention_all: bool = False,
    origin_node_id: str | None = None,
    origin_session_id: str | None = None,
    owner_id: str | None = None,
) -> HasnMessages:
    """持久化消息并更新会话。

    ``origin_node_id``（doc02 §3.8）：产生该消息的节点 ID，由 Server 从认证上下文
    自动填入（不可伪造）——node WS/HTTP 带的节点 ID / 云端 runtime 填 'cloud' 哨兵；
    存 node_id 不存设备名，渲染边界 join hasn_nodes.node_name 解析显示名。

    ``origin_session_id``（doc14 §6.2）：产生该消息的**发起方 runtime 会话**（工作会话 id
    或主会话 runtime session id），与 origin_node_id 同款三约束——由 Server 从
    ``AgentContext.session_id`` 自动填、入参 schema 不收、不可伪造。无会话上下文=None。
    daemon 据此登记 session_outbound_links，对端出结果时把结果回灌回发起方会话。
    **发起方私有**：消费者按发送方 owner 做受众分叉，只有发送方 owner 的事件携带此字段。

    ``owner_id``（doc18 P0）：1:1 消息落库时回填「收件方 owner」，令该 owner 的透明视图与
    `hasn.message.search`（硬过滤 `WHERE owner_id`）能读到 route 落库的消息——此前 route 路径
    从不填 owner_id，收件方分身检索不到「对方告诉过我的事」，doc18 L3「聊天记录兜底」失效。
    群消息传空（None）：群读走 `list_group_messages` 按 conversation_id 归属，不看 owner_id。
    发送方的可见性另由会话受众扇出（doc02 §3.3 audience）覆盖，不靠本列。
    """
    now = timezone.now()

    # R2-02（doc16 §4.1）：落库前原子分配会话内序号——同事务 UPDATE ... RETURNING，
    # PG 行锁串行化同会话并发发送，权威顺序事实。会话必已存在（调用链先 get_or_create），
    # None 说明会话行缺失，属不变量破坏，直接抛（终局故障，见「日志分级」铁律）。
    conversation_seq = await hasn_conversations_dao.allocate_seq(db, conversation_id)
    if conversation_seq is None:
        raise ValueError(f'allocate_seq 失败：会话 {conversation_id} 不存在，无法分配 conversation_seq')

    msg = HasnMessages(
        conversation_id=conversation_id,
        conversation_seq=conversation_seq,
        owner_id=owner_id or None,
        from_id=from_id,
        from_type=_entity_type_int(from_id),
        to_id=to_id,
        to_type=_entity_type_int(to_id),
        content_type=content_type,
        content=content,
        process_blocks=process_blocks or [],
        msg_type=msg_type,
        status=1,  # sent
        priority=priority,
        reply_to_id=reply_to_id,
        local_id=local_id,
        context=context,
        mentions=mentions,
        mention_all=mention_all,
        server_received_at=now,
        origin_node_id=origin_node_id,
        origin_session_id=origin_session_id,
    )
    db.add(msg)
    await db.flush()

    # 私有附件按会话授权（1f）：落消息即为 content.attachments 内的私有 asset 写 grant，
    # 关闭跨 owner 越权洞（08 §1.6）。public 附件无需 grant（resolve 直读）。
    await _grant_private_attachments(db, conversation_id, msg.id, content)

    # 更新会话最后消息
    conv = await db.get(HasnConversations, conversation_id)
    if conv:
        conv.last_message_id = msg.id
        conv.last_message_at = now
        conv.last_message_from = from_id
        conv.message_count = (conv.message_count or 0) + 1
        # 生成预览
        if content_type == 1:  # 文本
            text = content.get('text', '')
            conv.last_message_preview = text[:200] if text else ''
        elif content_type == 2:
            conv.last_message_preview = '[图片]'
        elif content_type == 3:
            conv.last_message_preview = '[文件]'
        elif content_type == 4:
            conv.last_message_preview = '[语音]'
        elif content_type == 5:
            conv.last_message_preview = '[卡片]'
        else:
            conv.last_message_preview = '[消息]'

    # 更新接收方未读计数。群聊由 route_message 按成员扇出写未读，避免给 g:* 自身计未读。
    if not to_id.startswith('g:'):
        await increment_unread_for(db, conversation_id, to_id)

    await db.flush()
    return msg


async def _append_message_committed_event(
    db: AsyncSession,
    *,
    conversation_id: str,
    sender_hasn_id: str,
    msg: HasnMessages,
    origin_node_id: str | None,
    origin_session_id: str | None,
    content_body: dict[str, Any] | None = None,
) -> None:
    """在消息事务末尾追加唯一集成事件，扇出由三个独立消费者完成。"""
    created_at = int(msg.created_time.timestamp()) if msg.created_time else 0
    await append_event(
        db,
        event_type=IM_MESSAGE_COMMITTED,
        aggregate_type='conversation',
        aggregate_id=conversation_id,
        aggregate_seq=msg.conversation_seq,
        payload={
            'conversation_id': conversation_id,
            'message_id': str(msg.id),
            'sender_hasn_id': sender_hasn_id,
            'conversation_seq': msg.conversation_seq,
            'origin_node_id': origin_node_id,
            'origin_session_id': origin_session_id,
            'content_type': msg.content_type,
            'content_body': content_body if content_body is not None else msg.content,
            'local_id': msg.local_id,
            'created_at': created_at,
        },
    )


async def commit_released_command(
    db: AsyncSession,
    command: dict[str, Any],
) -> tuple[HasnMessages, bool]:
    """把主人批准的抑制命令按当前时点写成权威消息与唯一集成事件。

    本函数只供 ``ImGateway.release_suppressed`` 在已经完成 owner 判权、锁定抑制行后调用。
    它不重新执行会再次抑制的入站五闸，但仍校验会话参与者、幂等键和命令载荷；消息、
    conversation_seq、会话投影与 integration event 均留在调用方同一事务中提交。
    """
    conversation_id = str(command.get('conversation_id') or '')
    from_id = str(command.get('from_id') or '')
    to_id = str(command.get('to_id') or '')
    idempotency_key = str(command.get('idempotency_key') or '')
    if not conversation_id or not from_id or not to_id or not idempotency_key:
        raise ValueError('抑制命令缺少 conversation/from/to/idempotency_key')

    conversation = await db.get(HasnConversations, conversation_id)
    if conversation is None or conversation.type != 'direct':
        raise ValueError('抑制命令关联的直聊会话不存在')
    participants = {
        conversation.participant_a_id,
        conversation.participant_b_id,
    }
    if participants != {from_id, to_id}:
        raise ValueError('抑制命令参与者与权威会话不一致')

    content = command.get('content')
    if not isinstance(content, dict):
        raise ValueError('抑制命令 content 必须是对象')
    content_type = int(command.get('content_type') or 1)
    msg_type = str(command.get('msg_type') or 'message')
    priority = str(command.get('priority') or 'normal')
    reply_to_id = command.get('reply_to_id')
    context = command.get('context')
    origin_node_id = command.get('origin_node_id')
    origin_session_id = command.get('origin_session_id')

    idempotent = await _resolve_idempotent_send(
        db,
        local_id=idempotency_key,
        conversation_id=conversation_id,
        from_id=from_id,
        to_id=to_id,
        content=content,
        content_type=content_type,
        msg_type=msg_type,
        priority=priority,
        reply_to_id=reply_to_id,
        context=context,
        origin_node_id=origin_node_id,
        origin_session_id=origin_session_id,
    )
    if idempotent is not None:
        if idempotent.get('error'):
            raise ValueError(str(idempotent.get('message') or '抑制命令幂等冲突'))
        existing = await db.get(HasnMessages, int(idempotent['msg_id']))
        if existing is None:
            raise ValueError('幂等记录命中的权威消息不存在')
        return existing, True

    message = await persist_message(
        db=db,
        conversation_id=conversation_id,
        from_id=from_id,
        to_id=to_id,
        content=content,
        content_type=content_type,
        msg_type=msg_type,
        priority=priority,
        reply_to_id=reply_to_id,
        local_id=idempotency_key,
        context=context,
        origin_node_id=origin_node_id,
        origin_session_id=origin_session_id,
        owner_id=command.get('owner_id'),
    )
    await _append_message_committed_event(
        db,
        conversation_id=conversation_id,
        sender_hasn_id=from_id,
        msg=message,
        origin_node_id=origin_node_id,
        origin_session_id=origin_session_id,
    )
    return message, False


# ─── 消息路由主入口 ───


async def _find_message_by_local_id(db: AsyncSession, local_id: str) -> HasnMessages | None:
    """按客户端 local_id 查既有消息，用于出站投递重发的幂等去重。

    hasn_messages 上 local_id 全局唯一（partial unique index，NULL 不约束），故全表
    精确匹配即可命中唯一行；返回 None 表示从未落库（首次投递，正常路由）。
    """
    result = await db.execute(select(HasnMessages).where(HasnMessages.local_id == local_id).limit(1))
    return result.scalar_one_or_none()


async def _resolve_idempotent_send(
    db: AsyncSession,
    *,
    local_id: str | None,
    conversation_id: str,
    from_id: str,
    to_id: str,
    content: dict[str, Any],
    content_type: int,
    msg_type: str,
    priority: str,
    reply_to_id: int | None,
    context: dict[str, Any] | None,
    origin_node_id: str | None,
    origin_session_id: str | None,
) -> dict[str, Any] | None:
    """串行化幂等键并判定重放或冲突；首次请求返回 ``None``。"""
    if not local_id:
        return None
    await db.execute(
        text('SELECT pg_advisory_xact_lock(hashtext(:lock_key))'),
        {'lock_key': f'hasn_im.message.local_id:{local_id}'},
    )
    existing = await _find_message_by_local_id(db, local_id)
    if existing is None:
        return None
    same_command = (
        str(existing.conversation_id) == conversation_id
        and existing.from_id == from_id
        and existing.to_id == to_id
        and existing.content == content
        and existing.content_type == content_type
        and existing.msg_type == msg_type
        and existing.priority == priority
        and existing.reply_to_id == reply_to_id
        and (existing.context or None) == (context or None)
        and existing.origin_node_id == origin_node_id
        and existing.origin_session_id == origin_session_id
    )
    if not same_command:
        return {
            'error': True,
            'code': 3015,
            'message': '同一 local_id 已被不同消息命令使用',
            'local_id': local_id,
        }
    return {
        'error': False,
        'msg_id': existing.id,
        'conversation_id': str(existing.conversation_id),
        'status': 'sent',
        'local_id': local_id,
        'deduped': True,
    }


async def _suppress_inbound(
    db: AsyncSession,
    *,
    from_id: str,
    agent_info: dict[str, Any],
    content: dict,
    content_type: int,
    msg_type: str,
    priority: str,
    reply_to_id: int | None,
    local_id: str | None,
    context: dict | None,
    reason: str,
    snapshot: dict[str, Any],
    to_entity_type: str = 'agent',
    origin_node_id: str | None = None,
    origin_session_id: str | None = None,
) -> dict[str, Any]:
    """入站门控未过：只写待放行命令 + WSPUSH，不落消息、不占 seq、不唤醒 runtime。

    主人在抑制箱可见命令正文；放行时按普通消息重新分配当时的新 conversation_seq 并追加
    integration event，保证已越过原时点的客户端仍能通过 sync 恢复。

    to_entity_type='human' 支持 A2H（分身→人类）接收侧暂存（§4.1.3）：此时收件主体即主人本人
    （hasn_id == owner_id），会话 to_type 记 'human'。返回结构化关系反馈（供消息工具诚实回传，
    修 B12：暂存时 reachable=false + 带 pending_request_id/relation）。
    """
    to_id = agent_info['hasn_id']
    # A2H 收件主体即主人本人；A2A 取分身 owner
    owner_id = agent_info.get('owner_id') or (to_id if to_entity_type == 'human' else '')
    from_type = _entity_type_str(from_id)
    conv = await get_or_create_conversation(db, from_id, from_type, to_id, to_entity_type, 'social')
    if not local_id:
        return {'error': True, 'code': 2002, 'message': 'idempotency_key 必填'}
    idempotent = await _resolve_idempotent_send(
        db,
        local_id=local_id,
        conversation_id=str(conv.id),
        from_id=from_id,
        to_id=to_id,
        content=content,
        content_type=content_type,
        msg_type=msg_type,
        priority=priority,
        reply_to_id=reply_to_id,
        context=context,
        origin_node_id=origin_node_id,
        origin_session_id=origin_session_id,
    )
    if idempotent is not None:
        return idempotent
    command_payload = {
        'conversation_id': str(conv.id),
        'from_id': from_id,
        'to_id': to_id,
        'content': content,
        'content_type': content_type,
        'msg_type': msg_type,
        'priority': priority,
        'reply_to_id': reply_to_id,
        'idempotency_key': local_id,
        'context': context,
        'origin_node_id': origin_node_id,
        'origin_session_id': origin_session_id,
        'owner_id': owner_id,
    }
    idempotency_scope, command_hash = suppression_command_identity(
        sender_hasn_id=from_id,
        origin_node_id=origin_node_id,
        idempotency_key=local_id,
        command_payload=command_payload,
    )
    try:
        suppressed = await record_suppression(
            db,
            owner_id=owner_id,
            hasn_id=to_id,
            conversation_id=str(conv.id),
            sender_hasn_id=from_id,
            idempotency_scope=idempotency_scope,
            command_hash=command_hash,
            command_payload=command_payload,
            reason=reason,
            policy_snapshot=snapshot,
        )
    except ValueError as exc:
        await db.rollback()
        return {'error': True, 'code': 3015, 'message': str(exc)}
    await db.commit()
    # WSPUSH：触发该 owner 在线 daemon 拉取抑制箱镜像（best-effort，不拖垮写点）
    if owner_id:
        try:
            await sync_invalidate_service.bump_owner('suppressed', db, owner_id)
        except Exception as exc:
            log.warning(f'[inbound_gate] bump suppressed failed: {exc}')
    # 结构化关系反馈（§4.1.4 修 B12）：把派生解析得到的关系档 + 代发请求 id 透传给消息工具，
    # 供其诚实回传 reachable=false + relation + pending_request_id + hint（不再误报 reachable=true）。
    from backend.app.hasn.constants import TRUST_LEVEL_LABELS

    rel_trust = snapshot.get('trust_level')
    relation = None
    if rel_trust is not None:
        relation = {
            'relation_type': snapshot.get('relation_type', 'social'),
            'trust_level': rel_trust,
            'label': TRUST_LEVEL_LABELS.get(rel_trust, ''),
        }
    return {
        'error': False,
        'status': 'suppressed',
        'suppress_reason': reason,
        'msg_id': None,
        'suppressed_id': int(suppressed['id']),
        'conversation_id': str(conv.id),
        'local_id': local_id,
        'suppressed': True,
        'pending_request_id': snapshot.get('pending_request_id'),
        'relation': relation,
    }


async def route_message(
    db: AsyncSession,
    from_id: str,
    to_target: str,
    content: dict,
    conversation_id: str | None = None,
    content_type: int = 1,
    msg_type: str = 'message',
    priority: str = 'normal',
    reply_to_id: int | None = None,
    local_id: str | None = None,
    context: dict | None = None,
    origin_node_id: str | None = None,
    origin_session_id: str | None = None,
    mission_note: str | None = None,
) -> dict[str, Any]:
    """
    消息路由主入口（会话一等实体·doc02 §3.3）

    流程：目标解析（发起层 get_or_create 拿权威会话）→ 闸串联（关系/入站门控/披露/拦截）
    → 落库并追加唯一 integration event；Sync、实时和移动推送由独立消费者扇出。

    ``origin_node_id``（§3.8）：产生该消息的节点，由**调用方从认证上下文**传入（node WS/
    HTTP 的节点 ID / 云端 runtime 填 'cloud'），Server 侧不收客户端自报入参、不可伪造。

    ``origin_session_id``（doc14 §6.2）：产生该消息的发起方 runtime 会话，同样由调用方从
    ``AgentContext.session_id`` 传入、不收客户端自报。落 hasn_messages 列 + 仅发送方 owner
    的事件携带（受众分叉由消费者完成）。

    ``mission_note``（doc14 §6.5）：差事背景，仅**新建 direct 会话**时写入 conversation
    （群消息不适用——群不是「差事会话」）。归属 owner = 发送方 owner。

    返回: {msg_id, conversation_id, status, local_id}
    """
    # 1. 目标解析
    target_info = await resolve_target(db, to_target)
    if not target_info:
        return {'error': True, 'code': 3001, 'message': f'目标 {to_target} 不存在'}

    to_id = target_info['hasn_id']
    to_type = target_info['entity_type']

    # 群消息：跳过单聊关系矩阵，按群成员/群设置判权后持久化为群会话并扇出。
    if to_type == 'group':
        group = await get_group_conversation(db, to_id)
        if not group:
            return {'error': True, 'code': 3001, 'message': f'群组 {to_id} 不存在'}
        group_conv_id = str(group.id)
        if conversation_id != group_conv_id:
            return {'error': True, 'code': 2002, 'message': 'conversation_id 与群会话不匹配'}
        group_perm = await check_group_send_permission(db, group_conv_id, from_id, group)
        if not group_perm.get('allowed'):
            return {'error': True, 'code': 2002, 'message': group_perm.get('reason', '无权发送群消息')}

        # @提及（mention_only 策略 + daemon 派发闸的数据载体）：从 context 取出，持久化并随
        # envelope 下发。daemon(G4) 据 group.agent_policy + 这些 mentions 决定唤醒哪些分身。
        grp_ctx = context or {}
        grp_mentions = grp_ctx.get('mentions') if isinstance(grp_ctx.get('mentions'), list) else None
        grp_mention_all = bool(grp_ctx.get('mention_all'))
        message_context = {**grp_ctx, 'conversation_type': 'group', 'group_id': to_id}
        grp_event_content = content
        if grp_mentions or grp_mention_all:
            grp_event_content = {
                **content,
                'mentions': grp_mentions,
                'mention_all': grp_mention_all,
            }
        idempotent = await _resolve_idempotent_send(
            db,
            local_id=local_id,
            conversation_id=group_conv_id,
            from_id=from_id,
            to_id=to_id,
            content=content,
            content_type=content_type,
            msg_type=msg_type,
            priority=priority,
            reply_to_id=reply_to_id,
            context=message_context,
            origin_node_id=origin_node_id,
            origin_session_id=origin_session_id,
        )
        if idempotent is not None:
            return idempotent

        # doc18 P0：群消息不填 owner_id——群读走 list_group_messages 按 conversation_id +
        # 成员资格归属（一条群消息属于全体成员，非单一 owner），单一 owner_id 列表达不了群语义。
        msg = await persist_message(
            db=db,
            conversation_id=group_conv_id,
            from_id=from_id,
            to_id=to_id,
            content=content,
            content_type=content_type,
            msg_type=msg_type,
            priority=priority,
            reply_to_id=reply_to_id,
            local_id=local_id,
            context=message_context,
            mentions=grp_mentions,
            mention_all=grp_mention_all,
            origin_node_id=origin_node_id,
            origin_session_id=origin_session_id,
        )

        members = await list_group_members(db, group_conv_id)
        for member in members:
            if member.member_id == from_id:
                continue
            await increment_unread_for(db, group_conv_id, member.member_id)

        # 会话一等实体·统一受众扇出（doc02 §3.3）：群受众 = 名册每个成员解析出的 owner 集合，
        # emit message.new 瘦事件 + 按 owner push。退役旧 _grp_sync_event 双写（message.sent/
        # received）+ hasn.message.received envelope 直推。daemon 群派发闸（G4）改从**会话对象
        # 镜像**（group_meta.agent_policy）读生效策略、从 content_body 取 @提及——不再靠事件
        # 附带 agent_policy/mentions 字段（§3.4）；故 @提及折进瘦事件的 content_body。
        from backend.app.hasn.service import conversation_projection as cp

        audience = await cp.compute_audience_owner_ids(db, group, members=members)
        await _append_message_committed_event(
            db,
            conversation_id=group_conv_id,
            sender_hasn_id=from_id,
            msg=msg,
            origin_node_id=origin_node_id,
            origin_session_id=origin_session_id,
            content_body=grp_event_content,
        )

        # 消息、未读投影和唯一集成事件同一事务提交；扇出由独立消费者完成。
        await db.commit()

        return {
            'error': False,
            'msg_id': msg.id,
            'conversation_id': group_conv_id,
            'status': 'sent',
            'local_id': local_id,
            'delivered_to': audience,
        }

    # 2. 不能给自己发消息（同一 hasn_id）
    if from_id == to_id:
        return {'error': True, 'code': 2006, 'message': '不能给自己发消息'}

    # 3. Phase 7 (07-02): A 路线 —— 中央统一判决（替换 Phase 3 的 check_relation_permission 调用；
    # 旧 fn 定义保留供回滚/灰度，不再从 route_message 调用）
    ctx_meta = (context or {}) if context else {}
    ctx_relation_type = ctx_meta.get('relation_type') or (
        'social' if to_id.startswith(('h_', 'a_')) else 'social'
    )
    ctx_from_entity_type = 'human' if from_id.startswith('h_') else ('agent' if from_id.startswith('a_') else 'system')
    ctx_to_entity_type = target_info.get('entity_type', 'agent')

    # 3.0 入站门控（外部 → Agent 全门控，设计 05/06）：仅普通消息、接收方是 Agent、发送方非其
    # Owner 时启用。未过门控 → 记录进主人抑制箱（不静默丢弃），主人放行后才真正投递+唤醒。
    # 限流（abuse_restricted）按 D3 由下方 permission_engine DENY 路径落抑制箱，本闸不含限流。
    if (
        ctx_to_entity_type == 'agent'
        and from_id != target_info.get('owner_id')
        and msg_type not in ('notification', 'system')
    ):
        gate = await evaluate_inbound(db, from_id=from_id, agent_info=target_info, relation_type=ctx_relation_type)
        if gate.action == REJECT_SILENT:
            return {'error': True, 'code': 2002, 'message': '对方已将你屏蔽'}
        if gate.action == SUPPRESS:
            return await _suppress_inbound(
                db,
                from_id=from_id,
                agent_info=target_info,
                content=content,
                content_type=content_type,
                msg_type=msg_type,
                priority=priority,
                reply_to_id=reply_to_id,
                local_id=local_id,
                context=context,
                reason=gate.reason or 'permission_denied',
                snapshot=gate.snapshot,
                origin_node_id=origin_node_id,
                origin_session_id=origin_session_id,
            )

    perm_result = await permission_engine.evaluate(
        db,
        sender={
            'hasn_id': from_id,
            'entity_type': ctx_from_entity_type,
        },
        receiver={
            'hasn_id': to_id,
            'owner_id': target_info.get('owner_id'),
            'entity_type': ctx_to_entity_type,
        },
        envelope={
            'msg_type': msg_type,
            'content': content,
            'relation_type': ctx_relation_type,
            'metadata': ctx_meta,
            'from_entity_type': ctx_from_entity_type,
        },
    )

    if perm_result.decision == DENY:
        # §4.1.3（修 B12 后半）：A2H 出站关系门 DENY（发送方=分身、接收方=人类、无/低关系）→ 不再
        #   裸 2002 让主人看不到；接收侧（人类主人）跑与「发给我的分身」一致的解析——好友直达 / 普通
        #   朋友转请求进箱 / 陌生人门控进箱。出站闸(_a2h_outbound_gate)作为发送方授权面保留（本 DENY
        #   即其判决），这里仅在其判「关系门」时把不可达的裸报错升级为可见的暂存/请求路径。
        if (
            ctx_from_entity_type == 'agent'
            and ctx_to_entity_type == 'human'
            and perm_result.matched_rule == 'matrix_a2h_relation'
            and from_id != to_id
            and msg_type not in ('notification', 'system')
        ):
            a2h_gate = await evaluate_a2h_inbound(
                db, from_agent=from_id, human_id=to_id, relation_type=ctx_relation_type,
            )
            if a2h_gate.action == REJECT_SILENT:
                return {'error': True, 'code': 2002, 'message': '对方已将你屏蔽'}
            if a2h_gate.action == SUPPRESS:
                return await _suppress_inbound(
                    db,
                    from_id=from_id,
                    agent_info=target_info,
                    content=content,
                    content_type=content_type,
                    msg_type=msg_type,
                    priority=priority,
                    reply_to_id=reply_to_id,
                    local_id=local_id,
                    context=context,
                    reason=a2h_gate.reason or 'permission_denied',
                    snapshot=a2h_gate.snapshot,
                    to_entity_type='human',
                    origin_node_id=origin_node_id,
                    origin_session_id=origin_session_id,
                )
            # ALLOW（接收侧视好友/直连≥2）→ 覆盖出站关系门 DENY，放行走正常投递
            from backend.app.hasn.service.iron_laws import DecisionResult

            perm_result = DecisionResult(
                decision=ALLOW,
                reason='a2h receiver approved (friend/direct edge)',
                matched_rule='a2h_receiver_override',
            )
        # 限流（iron_law_6）DENY 对外部→Agent → 入抑制箱(abuse_restricted) 让主人可放行；
        # 其它硬违规（身份未声明/commerce free_chat 等）保持静默拒绝（协议级，不进抑制箱）。
        elif (
            ctx_to_entity_type == 'agent'
            and from_id != target_info.get('owner_id')
            and perm_result.matched_rule == 'iron_law_6'
        ):
            return await _suppress_inbound(
                db,
                from_id=from_id,
                agent_info=target_info,
                content=content,
                content_type=content_type,
                msg_type=msg_type,
                priority=priority,
                reply_to_id=reply_to_id,
                local_id=local_id,
                context=context,
                reason='abuse_restricted',
                snapshot={'limit_kind': 'rate', 'error_code': perm_result.error_code},
                origin_node_id=origin_node_id,
                origin_session_id=origin_session_id,
            )
        else:
            # 未被 A2H 覆盖、也非限流 → 硬拒（协议级），保持静默 2002
            return {
                'error': True,
                'code': perm_result.error_code or 2002,
                'message': perm_result.reason,
            }
    if perm_result.decision == CONFIRM:
        await _stash_pending_commitment(
            db,
            sender_id=from_id,
            receiver_id=to_id,
            payload={'msg_type': msg_type, 'content': content},
            reason=perm_result.reason,
        )
        return {
            'error': False,
            'status': 'pending_confirmation',
            'reason': perm_result.reason,
        }
    if perm_result.decision == SCOPE_LTD and perm_result.allowed_fields is not None:
        allowed = set(perm_result.allowed_fields)
        content = {k: v for k, v in (content or {}).items() if k in allowed}

    # 4. 发送只接受已经 ensure 的权威会话，不在消息事务里隐式创建。
    if not conversation_id:
        return {'error': True, 'code': 2002, 'message': 'R3 协议要求 conversation_id'}
    conv = await db.get(HasnConversations, conversation_id)
    participants = (
        {conv.participant_a_id, conv.participant_b_id}
        if conv is not None and conv.type == 'direct'
        else set()
    )
    if conv is None or participants != {from_id, to_id}:
        return {'error': True, 'code': 2002, 'message': 'conversation_id 与发送双方不匹配'}

    # 5. 持久化
    # doc18 P0：1:1 消息回填 owner_id=收件方 owner（发给分身=该分身 owner，发给人=其本人），
    # 与下方 message.received 事件的 recipient_owner_id 同源，令收件方透明视图/hasn.message.search 可读。
    recipient_owner_for_row = target_info.get('owner_id') if to_id.startswith('a_') else to_id
    idempotent = await _resolve_idempotent_send(
        db,
        local_id=local_id,
        conversation_id=str(conv.id),
        from_id=from_id,
        to_id=to_id,
        content=content,
        content_type=content_type,
        msg_type=msg_type,
        priority=priority,
        reply_to_id=reply_to_id,
        context=context,
        origin_node_id=origin_node_id,
        origin_session_id=origin_session_id,
    )
    if idempotent is not None:
        return idempotent
    msg = await persist_message(
        db=db,
        conversation_id=str(conv.id),
        from_id=from_id,
        to_id=to_id,
        content=content,
        content_type=content_type,
        msg_type=msg_type,
        priority=priority,
        reply_to_id=reply_to_id,
        local_id=local_id,
        context=context,
        origin_node_id=origin_node_id,
        origin_session_id=origin_session_id,
        owner_id=recipient_owner_for_row or None,
    )

    # 6. 会话一等实体·统一受众扇出（doc02 §3.3）：direct 受众 = 两参与者各自解析出的 owner 集合。
    # - A2A：两分身各解析主人 → 两主人都在受众 → **A2AFIRST/Fix#5「补推发送方」补丁天然消失**；
    # - owner↔自有分身 loopback：两参与者同解析到一个 owner → 单条 message.new 推该 owner 全设备，
    #   派发由收到设备按 dispatch_here 判决（§3.8）；
    # - 跨 owner 1:1：sender_owner ∪ recipient_owner。
    # 退役旧 message.sent/received 双写 + entity 直推(_push_message_to) +
    # push_to_owner_excluding_agent_node + A2AFIRST push_to_owner（受众计算统一覆盖）。
    from backend.app.hasn.service import conversation_projection as cp

    audience = await cp.compute_audience_owner_ids(db, conv)
    await _append_message_committed_event(
        db,
        conversation_id=str(conv.id),
        sender_hasn_id=from_id,
        msg=msg,
        origin_node_id=origin_node_id,
        origin_session_id=origin_session_id,
    )

    # 消息与唯一集成事件同一事务提交；Sync、实时和移动推送均由消费者后置处理。
    await db.commit()

    return {
        'error': False,
        'msg_id': msg.id,
        'conversation_id': str(conv.id),
        'status': 'sent',
        'local_id': local_id,
        'delivered_to': audience,
    }


# ─── 已读处理 ───


async def mark_read(
    db: AsyncSession,
    hasn_id: str,
    conversation_id: str,
    last_msg_id: int,
) -> None:
    """按消息序号单调推进活动 membership 的已读游标。"""
    memberships = SCHEMA_NAMES.im_table('hasn_conversation_memberships')
    messages = SCHEMA_NAMES.im_table('hasn_messages')
    await db.execute(
        text(
            f'UPDATE {memberships} m SET '  # noqa: S608 内部常量表名
            'read_seq = GREATEST(m.read_seq, target.conversation_seq), updated_time = now() '
            f'FROM {messages} target '  # noqa: S608 内部常量表名
            'WHERE m.conversation_id = CAST(:conversation_id AS uuid) '
            'AND m.member_hasn_id = :member_hasn_id '
            "AND m.left_seq IS NULL AND m.state = 'active' "
            'AND target.id = :last_msg_id '
            'AND target.conversation_id = m.conversation_id'
        ),
        {
            'conversation_id': conversation_id,
            'member_hasn_id': hasn_id,
            'last_msg_id': last_msg_id,
        },
    )
    await increment_unread_for(db, conversation_id, hasn_id)
    await db.commit()


# ─── 消息撤回 ───


async def recall_message(
    db: AsyncSession,
    hasn_id: str,
    msg_id: int,
) -> dict[str, Any]:
    """撤回消息"""
    result = await db.execute(select(HasnMessages).where(HasnMessages.id == msg_id))
    msg = result.scalar_one_or_none()

    if not msg:
        return {'error': True, 'code': 3001, 'message': '消息不存在'}

    # 只能撤回自己发的消息，或 Owner 撤回 Agent 的消息
    can_recall = False
    if msg.from_id == hasn_id:
        can_recall = True
    elif hasn_id.startswith('h_') and msg.from_id.startswith('a_'):
        agent_result = await db.execute(
            select(HasnAgents).where(
                HasnAgents.hasn_id == msg.from_id,
                HasnAgents.owner_id == hasn_id,
            )
        )
        if agent_result.scalar_one_or_none():
            can_recall = True

    if not can_recall:
        return {'error': True, 'code': 2002, 'message': '无权撤回此消息'}

    if msg.status == 4:  # 已撤回
        return {'error': True, 'code': 3014, 'message': '消息已被撤回'}

    # 执行撤回
    msg.status = 4  # recalled
    msg.recalled_at = timezone.now()
    msg.recalled_by = hasn_id
    await db.commit()

    # 通知对方
    recall_payload = {
        'cmd': 'MESSAGE_RECALLED',
        'msg_id': msg_id,
        'conversation_id': str(msg.conversation_id),
        'recalled_by': hasn_id,
    }
    await _push_message_to(msg.to_id, recall_payload)

    return {'error': False, 'msg_id': msg_id}


# ─── Phase 7 (07-02): A 路线 confirm_required 暂存 helper ───


async def _stash_pending_commitment(
    db: AsyncSession,
    *,
    sender_id: str,
    receiver_id: str,
    payload: dict,
    reason: str,
    ttl_seconds: int = 86400,
) -> None:
    """confirm_required 判决下的中央暂存 (写 hasn_pending_commitments 表)。

    桌面端通过 /hasn-events SSE 通道领取此条记录后由用户人工确认/拒绝。
    SQLAlchemy text() 参数化，避免 SQL 注入 (T-07-02-02)。
    """
    import json

    from datetime import datetime, timedelta
    from datetime import timezone as dt_tz

    from sqlalchemy import text

    commitment_id = uuid.uuid4().hex
    expires_at = datetime.now(dt_tz.utc) + timedelta(seconds=ttl_seconds)
    await db.execute(
        text(
            """
            INSERT INTO hasn_pending_commitments
            (id, action_type, sender_id, receiver_id, payload_json, reason, expires_at)
            VALUES (:id, :atype, :sender, :receiver, CAST(:payload AS JSONB), :reason, :expires)
            """
        ),
        {
            'id': commitment_id,
            'atype': 'message_deliver',
            'sender': sender_id,
            'receiver': receiver_id,
            'payload': json.dumps(payload, sort_keys=True, ensure_ascii=False),
            'reason': reason,
            'expires': expires_at,
        },
    )
    await db.flush()
