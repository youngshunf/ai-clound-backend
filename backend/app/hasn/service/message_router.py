"""HASN 消息路由核心服务

实现消息路由全流程（对齐协议 02-消息与通信.md §3.1）：
认证 → 目标解析 → 关系查询 → 权限检查（三维矩阵）→ 铁律检查 → 持久化 → 投递
"""

import uuid

from typing import Any

from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession

from backend.app.hasn.constants import (
    ALLOW,
    CONFIRM,
    DENY,
    SCOPE_LTD,
    check_action_permission,
)
from backend.app.hasn.model import HasnConversations, HasnHumans, HasnMessages, HasnUnreadCounts
from backend.app.hasn.model.hasn_agents import HasnAgents
from backend.app.hasn.model.hasn_contacts import HasnContacts
from backend.app.hasn.model.hasn_group_members import HasnGroupMembers
from backend.app.hasn.service import sync_invalidate_service

# 入站门控（外部→Agent 全门控，设计 05/06）：五闸判定 + 抑制记录
from backend.app.hasn.service.inbound_gatekeeper import (
    REJECT_SILENT,
    SUPPRESS,
    evaluate_a2h_inbound,
    evaluate_inbound,
    record_suppression,
)

# Phase 7 (07-02): A 路线中央判决器；替换 check_relation_permission 在 route_message 中的调用
from backend.app.hasn.service.permission_engine import permission_engine
from backend.common.log import log
from backend.utils.timezone import timezone

# ─── 目标解析 ───


async def _push_message_to(hasn_id: str, payload: dict[str, Any]) -> None:
    """延迟导入 WS 路由器，避免服务模块启动时与 binding_event_service 循环导入。"""
    from backend.app.hasn.service.ws_router import ws_router

    await ws_router.push_message_to(hasn_id, payload)


async def resolve_target(db: AsyncSession, target: str) -> dict[str, Any] | None:
    """
    解析目标地址（Star ID 或 HASN ID）→ 实体信息

    返回: {hasn_id, star_id, entity_type, name} 或 None
    """
    # 直接是 HASN ID
    if target.startswith('h_'):
        result = await db.execute(select(HasnHumans).where(HasnHumans.hasn_id == target))
        human = result.scalar_one_or_none()
        if human:
            return {
                'hasn_id': human.hasn_id,
                'star_id': human.star_id,
                'entity_type': 'human',
                'name': human.nickname,  # HasnHumans 使用 nickname 字段
            }
        return None

    if target.startswith('a_'):
        result = await db.execute(select(HasnAgents).where(HasnAgents.hasn_id == target))
        agent = result.scalar_one_or_none()
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
        result = await db.execute(
            select(HasnConversations).where(
                HasnConversations.type == 'group',
                HasnConversations.group_id == target,
                HasnConversations.status == 'active',
            )
        )
        group = result.scalar_one_or_none()
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
        result = await db.execute(select(HasnAgents).where(HasnAgents.star_id == target))
        agent = result.scalar_one_or_none()
        if agent:
            return {
                'hasn_id': agent.hasn_id,
                'star_id': agent.star_id,
                'entity_type': 'agent',
                'name': agent.display_name,  # HasnAgents 使用 display_name 字段
                'owner_id': agent.owner_id,
            }
    else:
        # Human Star ID: 100001 或 fuzi
        result = await db.execute(select(HasnHumans).where(HasnHumans.star_id == target))
        human = result.scalar_one_or_none()
        if human:
            return {
                'hasn_id': human.hasn_id,
                'star_id': human.star_id,
                'entity_type': 'human',
                'name': human.nickname,  # HasnHumans 使用 nickname 字段
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
) -> HasnConversations:
    """获取或创建单聊会话。

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
    )
    db.add(conv)
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
    """列出群活跃成员。当前模型无 removed_at/status 字段，存在即视为成员。"""
    result = await db.execute(select(HasnGroupMembers).where(HasnGroupMembers.conversation_id == conversation_id))
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
    unread_result = await db.execute(
        select(HasnUnreadCounts).where(
            HasnUnreadCounts.hasn_id == hasn_id,
            HasnUnreadCounts.conversation_id == conversation_id,
        )
    )
    unread = unread_result.scalar_one_or_none()
    if unread:
        unread.unread_count = (unread.unread_count or 0) + 1
    else:
        db.add(
            HasnUnreadCounts(
                hasn_id=hasn_id,
                conversation_id=conversation_id,
                unread_count=1,
                last_read_msg_id=0,
            )
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


async def _grant_private_attachments(db: AsyncSession, conversation_id: str, content: dict | None) -> None:
    """为消息内私有附件按会话写读权 grant（1f）。public 跳过，零越权。"""
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
    owner_id: str | None = None,
) -> HasnMessages:
    """持久化消息并更新会话。

    owner_id（doc18 P0）：1:1 消息落库时回填「收件方 owner」，令该 owner 的透明视图与
    `hasn.message.search`（硬过滤 `WHERE owner_id`）能读到 route 落库的消息——此前 route 路径
    从不填 owner_id，收件方分身检索不到「对方告诉过我的事」，doc18 L3「聊天记录兜底」失效。
    群消息传空（None）：群读走 `list_group_messages` 按 conversation_id 归属，不看 owner_id。
    发送方的可见性另由 `message.sent` sync_event / owner_copy 旁观补投覆盖（A2AFIRST），不靠本列。
    """
    now = timezone.now()

    msg = HasnMessages(
        conversation_id=conversation_id,
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
    )
    db.add(msg)
    await db.flush()

    # 私有附件按会话授权（1f）：落消息即为 content.attachments 内的私有 asset 写 grant，
    # 关闭跨 owner 越权洞（08 §1.6）。public 附件无需 grant（resolve 直读）。
    await _grant_private_attachments(db, conversation_id, content)

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


# ─── 消息路由主入口 ───


async def _find_message_by_local_id(db: AsyncSession, local_id: str) -> HasnMessages | None:
    """按客户端 local_id 查既有消息，用于出站投递重发的幂等去重。

    hasn_messages 上 local_id 全局唯一（partial unique index，NULL 不约束），故全表
    精确匹配即可命中唯一行；返回 None 表示从未落库（首次投递，正常路由）。
    """
    result = await db.execute(select(HasnMessages).where(HasnMessages.local_id == local_id).limit(1))
    return result.scalar_one_or_none()


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
) -> dict[str, Any]:
    """入站门控未过：落库消息为受抑制态 + 写抑制箱 + WSPUSH，不投递不唤醒 runtime。

    被门控的外部→Agent 消息保留 message_id 与正文、visible_to_owner=true，主人在抑制箱可见、
    放行后才真正投递+唤醒（设计 06：记录不丢弃）。不写 message.received sync_event，故不污染
    主人正常会话列表，仅经抑制箱镜像（WSPUSH suppressed kind）呈现。

    to_entity_type='human' 支持 A2H（分身→人类）接收侧暂存（§4.1.3）：此时收件主体即主人本人
    （hasn_id == owner_id），会话 to_type 记 'human'。返回结构化关系反馈（供消息工具诚实回传，
    修 B12：暂存时 reachable=false + 带 pending_request_id/relation）。
    """
    to_id = agent_info['hasn_id']
    # A2H 收件主体即主人本人；A2A 取分身 owner
    owner_id = agent_info.get('owner_id') or (to_id if to_entity_type == 'human' else '')
    from_type = _entity_type_str(from_id)
    conv = await get_or_create_conversation(db, from_id, from_type, to_id, to_entity_type, 'social')
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
        # doc18 P0：被抑制的入站消息也回填收件方 owner（=上面已算的 owner_id），
        # 令主人在抑制箱/透明视图可检索（放行后正常投递）。
        owner_id=owner_id or None,
    )
    await record_suppression(
        db,
        message_id=msg.id,
        owner_id=owner_id,
        hasn_id=to_id,
        conversation_id=str(conv.id),
        reason=reason,
        policy_snapshot=snapshot,
    )
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
        'msg_id': msg.id,
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
    content_type: int = 1,
    msg_type: str = 'message',
    priority: str = 'normal',
    reply_to_id: int | None = None,
    local_id: str | None = None,
    context: dict | None = None,
) -> dict[str, Any]:
    """
    消息路由主入口

    流程：目标解析 → 关系检查 → 权限检查 → 获取/创建会话 → 持久化 → 投递

    返回: {msg_id, conversation_id, status, local_id}
    """
    # 0. 幂等去重（local_id）：daemon 出站投递队列在断连/重连后会重发同一帧（带相同
    #    local_id），用于补达「发出去丢在断连窗口」的消息。若该 local_id 已落库（hasn_messages
    #    上有唯一索引 idx_hasn_msg_local_id），直接回原 msg_id+conversation_id，**不二次落库、
    #    不二次投递**——发送端据此补到 ack（标记已达），对方绝不会收到重复消息。
    if local_id:
        existing = await _find_message_by_local_id(db, local_id)
        if existing is not None:
            return {
                'error': False,
                'msg_id': existing.id,
                'conversation_id': str(existing.conversation_id),
                'status': 'sent',
                'local_id': local_id,
                'deduped': True,
            }

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
        group_perm = await check_group_send_permission(db, group_conv_id, from_id, group)
        if not group_perm.get('allowed'):
            return {'error': True, 'code': 2002, 'message': group_perm.get('reason', '无权发送群消息')}

        # @提及（mention_only 策略 + daemon 派发闸的数据载体）：从 context 取出，持久化并随
        # envelope 下发。daemon(G4) 据 group.agent_policy + 这些 mentions 决定唤醒哪些分身。
        grp_ctx = context or {}
        grp_mentions = grp_ctx.get('mentions') if isinstance(grp_ctx.get('mentions'), list) else None
        grp_mention_all = bool(grp_ctx.get('mention_all'))

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
            context={**grp_ctx, 'conversation_type': 'group', 'group_id': to_id},
            mentions=grp_mentions,
            mention_all=grp_mention_all,
        )

        members = await list_group_members(db, group_conv_id)
        recipient_ids: set[str] = set()
        for member in members:
            if member.member_id == from_id:
                continue
            await increment_unread_for(db, group_conv_id, member.member_id)
            for delivery_id in await _delivery_targets_for_member(db, member):
                if delivery_id != from_id:
                    recipient_ids.add(delivery_id)

        # G2-b 群离线回放：为发送方 owner 写 message.sent、为每个其他成员 owner 写
        # message.received，使离线成员登录 sync/pull 能补回群历史（对齐单聊语义；群分支
        # 历史上漏写 sync_event → 离线回放断裂）。owner 维度去重，避免同 owner 既 sent 又 received。
        from backend.app.hasn.service.hasn_sync_service import SqlAlchemySyncGateway

        grp_sync = SqlAlchemySyncGateway()
        grp_ct_str = {
            1: 'text',
            2: 'image/*',
            3: 'application/octet-stream',
            4: 'audio/*',
            5: 'application/x.card+json',
        }.get(content_type, 'text')
        grp_created_ts = int(msg.created_time.timestamp()) if msg.created_time else 0

        async def _grp_sync_event(owner_id_: str, hasn_id_: str, event_type: str, direction: str) -> None:
            await grp_sync._append_sync_event(
                db,
                owner_id=owner_id_,
                hasn_id=hasn_id_,
                event_type=event_type,
                aggregate_type='message',
                aggregate_id=str(msg.id),
                payload={
                    'message_id': str(msg.id),
                    'conversation_id': group_conv_id,
                    'owner_id': owner_id_,
                    'hasn_id': hasn_id_,
                    'sender_hasn_id': from_id,
                    'recipient_hasn_id': to_id,
                    'group_id': to_id,
                    'group_name': group.group_name,
                    'conversation_type': 'group',
                    'direction': direction,
                    'content_type': grp_ct_str,
                    'content_body': content,
                    'local_id': local_id,
                    'created_at': grp_created_ts,
                },
            )

        grp_sender_owner = from_id if from_id.startswith('h_') else await _agent_owner_id(db, from_id)
        grp_seen_owner: set[str] = set()
        if grp_sender_owner:
            grp_seen_owner.add(grp_sender_owner)
            await _grp_sync_event(grp_sender_owner, from_id, 'message.sent', 'outbound')
        for member in members:
            if member.member_id == from_id:
                continue
            m_owner = (
                member.member_id if member.member_id.startswith('h_') else await _agent_owner_id(db, member.member_id)
            )
            if not m_owner or m_owner in grp_seen_owner:
                continue
            grp_seen_owner.add(m_owner)
            await _grp_sync_event(m_owner, member.member_id, 'message.received', 'inbound')

        await db.commit()

        from_entity_type = 'human' if from_id.startswith('h_') else ('agent' if from_id.startswith('a_') else 'system')
        # 发言人展示信息（接收侧名册/"本条来自 X"标签用，省去 daemon 反查）。从已载名册取。
        sender_member = next((m for m in members if m.member_id == from_id), None)
        from_display_name = getattr(sender_member, 'member_name', None) if sender_member else None
        from_star_id = getattr(sender_member, 'member_star_id', None) if sender_member else None
        # doc10：随 envelope 下发「生效发言策略」+ 分身成员数——daemon 群派发闸据 effective 判定
        # （多分身群 free 已被降级为 mention_only），不必自己再数分身、再派生（云端权威一处算）。
        from backend.app.hasn.service.hasn_group_service import effective_agent_policy

        grp_agent_member_count = sum(1 for m in members if getattr(m, 'member_type', None) == 'agent')
        grp_stored_policy = getattr(group, 'agent_policy', None) or 'free'
        grp_effective_policy = effective_agent_policy(grp_stored_policy, grp_agent_member_count)
        hasn_envelope = {
            'id': msg.id,
            'conversation_id': group_conv_id,
            'from_id': from_id,
            'from_type': msg.from_type,
            'from_entity_type': from_entity_type,
            'from_display_name': from_display_name,
            'from_star_id': from_star_id,
            'to_id': to_id,
            'to_type': 4,
            'to_entity_type': 'group',
            'content_type': content_type,
            'content': content,
            'msg_type': msg_type,
            'status': 1,
            'priority': priority,
            'reply_to_id': reply_to_id,
            'local_id': local_id,
            'created_time': msg.created_time.isoformat() if msg.created_time else None,
            # 群级 agent 发言策略 + @提及：daemon(G4) group_participation_gate 的权威数据，
            # 决定 no_agent/silent 不唤醒、mention_only 仅命中才唤醒、free 唤醒(受配额退避)。
            'agent_policy': grp_stored_policy,
            'agent_policy_effective': grp_effective_policy,
            'agent_member_count': grp_agent_member_count,
            'mentions': grp_mentions,
            'mention_all': grp_mention_all,
            'group': {
                'group_id': to_id,
                'name': group.group_name,
                'owner_id': group.group_owner_id,
                'agent_policy': grp_stored_policy,
                'agent_policy_effective': grp_effective_policy,
                'agent_member_count': grp_agent_member_count,
            },
        }
        payload = {
            'hasn': 'hasn/0.2',
            'method': 'hasn.message.received',
            'params': {
                'to_id': to_id,
                'message': hasn_envelope,
            },
        }
        for recipient_id in sorted(recipient_ids):
            await _push_message_to(recipient_id, payload)

        return {
            'error': False,
            'msg_id': msg.id,
            'conversation_id': group_conv_id,
            'status': 'sent',
            'local_id': local_id,
            'delivered_to': sorted(recipient_ids),
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

    # 4. 获取/创建会话
    from_type = _entity_type_str(from_id)
    relation_type = ctx_relation_type or 'social'

    conv = await get_or_create_conversation(db, from_id, from_type, to_id, to_type, relation_type)

    # 5. 持久化
    # doc18 P0：1:1 消息回填 owner_id=收件方 owner（发给分身=该分身 owner，发给人=其本人），
    # 与下方 message.received 事件的 recipient_owner_id 同源，令收件方透明视图/hasn.message.search 可读。
    recipient_owner_for_row = target_info.get('owner_id') if to_id.startswith('a_') else to_id
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
        owner_id=recipient_owner_for_row or None,
    )

    await db.commit()

    # 写入同步事件，供登录时通过 sync/pull 恢复历史消息
    from backend.app.hasn.service.hasn_sync_service import SqlAlchemySyncGateway

    sync_gw = SqlAlchemySyncGateway()
    content_type_str = {
        1: 'text',
        2: 'image/*',
        3: 'application/octet-stream',
        4: 'audio/*',
        5: 'application/x.card+json',
    }.get(content_type, 'text')

    # 为发送方写入 message.sent 事件
    if from_id.startswith('a_'):
        # Agent 发送的消息，查 agent 的 owner_id。直接查表，避免依赖
        # hasn_agents_service 里并不存在的 get_agent_by_hasn_id —— 该错误导入
        # 会在 commit 之后、投递之前抛 ImportError，导致 Agent 回复持久化成功
        # 却从不推送给收件人（跨 owner「人收 Agent 回复」永远收不到）。
        sender_owner_row = await db.execute(select(HasnAgents.owner_id).where(HasnAgents.hasn_id == from_id))
        sender_owner_id = sender_owner_row.scalar_one_or_none()
    else:
        # Human 发送的消息，from_id 就是 owner_id
        sender_owner_id = from_id

    if sender_owner_id:
        await sync_gw._append_sync_event(
            db,
            owner_id=sender_owner_id,
            hasn_id=from_id,
            event_type='message.sent',
            aggregate_type='message',
            aggregate_id=str(msg.id),
            payload={
                'message_id': str(msg.id),
                'conversation_id': str(conv.id),
                'owner_id': sender_owner_id,
                'hasn_id': from_id,
                'sender_hasn_id': from_id,
                'recipient_hasn_id': to_id,
                'direction': 'outbound',
                'content_type': content_type_str,
                'content_body': content,
                'local_id': local_id,
                'created_at': int(msg.created_time.timestamp()) if msg.created_time else 0,
            },
        )

    # 为接收方写入 message.received 事件
    recipient_owner_id = target_info.get('owner_id') if to_id.startswith('a_') else to_id
    if recipient_owner_id:
        await sync_gw._append_sync_event(
            db,
            owner_id=recipient_owner_id,
            hasn_id=to_id,
            event_type='message.received',
            aggregate_type='message',
            aggregate_id=str(msg.id),
            payload={
                'message_id': str(msg.id),
                'conversation_id': str(conv.id),
                'owner_id': recipient_owner_id,
                'hasn_id': to_id,
                'sender_hasn_id': from_id,
                'recipient_hasn_id': to_id,
                'direction': 'inbound',
                'content_type': content_type_str,
                'content_body': content,
                'local_id': local_id,
                'created_at': int(msg.created_time.timestamp()) if msg.created_time else 0,
            },
        )

    await db.commit()

    # 6. 构建推送 payload（对齐协议 01-传输层 §3.6 hasn.message.received 事件帧）
    from_entity_type = 'human' if from_id.startswith('h_') else ('agent' if from_id.startswith('a_') else 'system')
    to_entity_type = 'human' if to_id.startswith('h_') else ('agent' if to_id.startswith('a_') else 'system')

    hasn_envelope = {
        'id': msg.id,
        'conversation_id': str(conv.id),
        'from_id': from_id,
        'from_type': msg.from_type,
        'to_id': to_id,
        'to_type': msg.to_type,
        'content_type': content_type,
        'content': content,
        'msg_type': msg_type,
        'status': 1,
        'priority': priority,
        'reply_to_id': reply_to_id,
        'local_id': local_id,
        'created_time': msg.created_time.isoformat() if msg.created_time else None,
        'from_owner_id': from_id if from_id.startswith('h_') else None,
        'to_owner_id': target_info.get('owner_id') if to_entity_type == 'agent' else to_id,
        # Phase 7 (07-02): A 路线 envelope.permission 子对象 (与 07-01 Rust PermissionEnvelope 字节对齐)
        'permission': {
            'decision': perm_result.decision,
            'reason': perm_result.reason,
            'allowed_fields': perm_result.allowed_fields,
        },
    }

    payload = {
        'hasn': 'hasn/0.2',
        'method': 'hasn.message.received',
        'params': {
            'to_id': to_id,
            'message': hasn_envelope,
        },
    }

    # 7. 投递
    await _push_message_to(to_id, payload)
    # Runtime 缺失/离线时，Human Owner 在线节点仍要能作为纯 IM 客户端收到发给自己 Agent 的消息。
    # 但必须排除 Agent 实体所在节点（已通过上面的 entity 投递收到），否则 Agent 跑在
    # 主人 daemon 上时同一节点会收两遍 → 镜像两次 + 派发 runtime 两次（发一条收两条回复）。
    if to_entity_type == 'agent' and target_info.get('owner_id') and target_info.get('owner_id') != to_id:
        from backend.app.hasn.service.ws_router import ws_router

        await ws_router.push_to_owner_excluding_agent_node(target_info['owner_id'], to_id, payload)

    # owner_copy 旁观出站补投（发起方主动发首条修复）：当发送方是某主人的自有分身、且
    # 收件方 owner ≠ 发送方 owner（即分身发给「外部方」，而非 owner↔自有分身 loopback /
    # 自有分身互发——内部场景已由上面的 recipient 投递到达主人设备），且这条消息**没有**
    # local_id（= 分身经云端 `message.send` 工具主动直发、发送方 daemon 无本地 echo）时，
    # 把消息也实时投给发送方 owner 的节点，让主人在 owner_copy 旁观线程里立刻看到自家
    # 分身发出的这条。有 local_id 的（daemon 中转的回复，本地已 echo）**不推**，避免旁观
    # 线程重复。daemon 侧 `handle_message_frame` 认「from_id 是本主人分身」框定为旁观出站，
    # 用 cloud message_id 与 sync_pull 出站同键，两序都幂等不重复。
    if (
        from_id.startswith('a_')
        and sender_owner_id
        and recipient_owner_id
        and recipient_owner_id != sender_owner_id
        and local_id is None
    ):
        from backend.app.hasn.service.ws_router import ws_router

        await ws_router.push_to_owner(sender_owner_id, payload)

    return {
        'error': False,
        'msg_id': msg.id,
        'conversation_id': str(conv.id),
        'status': 'sent',
        'local_id': local_id,
    }


# ─── 已读处理 ───


async def mark_read(
    db: AsyncSession,
    hasn_id: str,
    conversation_id: str,
    last_msg_id: int,
) -> None:
    """标记会话已读"""
    result = await db.execute(
        select(HasnUnreadCounts).where(
            HasnUnreadCounts.hasn_id == hasn_id,
            HasnUnreadCounts.conversation_id == conversation_id,
        )
    )
    unread = result.scalar_one_or_none()

    if unread:
        unread.unread_count = 0
        unread.last_read_msg_id = last_msg_id
    else:
        unread = HasnUnreadCounts(
            hasn_id=hasn_id,
            conversation_id=conversation_id,
            unread_count=0,
            last_read_msg_id=last_msg_id,
        )
        db.add(unread)

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
