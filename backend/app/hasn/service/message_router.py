"""HASN 消息路由核心服务

实现消息路由全流程（对齐协议 02-消息与通信.md §3.1）：
认证 → 目标解析 → 关系查询 → 权限检查（三维矩阵）→ 铁律检查 → 持久化 → 投递
"""

import uuid
from typing import Any

from sqlalchemy import select, and_, or_, func
from sqlalchemy.ext.asyncio import AsyncSession

from backend.database.db import async_db_session
from backend.utils.timezone import timezone

from backend.app.hasn.model import HasnHumans, HasnMessages, HasnConversations, HasnUnreadCounts
from backend.app.hasn.model.hasn_agents import HasnAgents
from backend.app.hasn.model.hasn_contacts import HasnContacts
from backend.app.hasn.model.hasn_group_members import HasnGroupMembers
from backend.app.hasn.constants import (
    check_action_permission,
    compute_effective_permissions,
    ALLOW,
    CONFIRM,
    DENY,
    SCOPE_LTD,
)
# Phase 7 (07-02): A 路线中央判决器；替换 check_relation_permission 在 route_message 中的调用
from backend.app.hasn.service.permission_engine import permission_engine


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
        result = await db.execute(
            select(HasnHumans).where(HasnHumans.hasn_id == target)
        )
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
        result = await db.execute(
            select(HasnAgents).where(HasnAgents.hasn_id == target)
        )
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
        result = await db.execute(
            select(HasnAgents).where(HasnAgents.star_id == target)
        )
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
        result = await db.execute(
            select(HasnHumans).where(HasnHumans.star_id == target)
        )
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
        sender_agent = await db.execute(
            select(HasnAgents.owner_id).where(HasnAgents.hasn_id == sender_id)
        )
        receiver_agent = await db.execute(
            select(HasnAgents.owner_id).where(HasnAgents.hasn_id == receiver_id)
        )
        s_owner = sender_agent.scalar()
        r_owner = receiver_agent.scalar()
        if s_owner and r_owner and s_owner == r_owner:
            return {'allowed': True, 'relation_type': 'social', 'trust_level': 5, 'permission_state': ALLOW}

    # 查询关系记录（双向查找，取发送方视角）
    # 对于 Agent，用其 Owner 的身份查关系
    sender_lookup = sender_id
    receiver_lookup = receiver_id

    if sender_id.startswith('a_'):
        result = await db.execute(
            select(HasnAgents.owner_id).where(HasnAgents.hasn_id == sender_id)
        )
        owner = result.scalar()
        if owner:
            sender_lookup = owner

    if receiver_id.startswith('a_'):
        result = await db.execute(
            select(HasnAgents.owner_id).where(HasnAgents.hasn_id == receiver_id)
        )
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
    _map = {
        'message':            'send_message',
        'text':               'send_message',
        'contact_request':    'send_message',
        'contact_accept':     'send_message',
        'contact_reject':     'send_message',
        'discovery_query':    'view_public_info',
        'schedule_query':     'view_schedule',
        'preference_query':   'view_preferences',
        'location_query':     'view_location',
        'appointment':        'make_appointment',
        'commitment':         'make_commitment',
        'sensitive_query':    'view_sensitive',
        'product_inquiry':    'product_inquiry',
        'trade_comm':         'trade_communication',
        'push_notification':  'send_push',
        'order_comm':         'order_communication',
        'decrypt_address':    'decrypt_address',
        'professional_consult': 'professional_consult',
    }
    return _map.get(msg_type, 'send_message')


# ─── 会话管理 ───

async def get_or_create_conversation(
    db: AsyncSession,
    participant_a_id: str,
    participant_a_type: str,
    participant_b_id: str,
    participant_b_type: str,
    relation_type: str = 'social',
) -> HasnConversations:
    """获取或创建单聊会话（约定 a < b 字典序保证唯一性）"""
    # 排序保证唯一性
    if participant_a_id > participant_b_id:
        participant_a_id, participant_b_id = participant_b_id, participant_a_id
        participant_a_type, participant_b_type = participant_b_type, participant_a_type

    # 查找已有会话
    result = await db.execute(
        select(HasnConversations).where(
            HasnConversations.type == 'direct',
            HasnConversations.participant_a_id == participant_a_id,
            HasnConversations.participant_b_id == participant_b_id,
            HasnConversations.relation_type == relation_type,
        )
    )
    conv = result.scalar_one_or_none()

    if conv:
        return conv

    # 创建新会话
    conv = HasnConversations(
        type='direct',
        relation_type=relation_type,
        participant_a_id=participant_a_id,
        participant_b_id=participant_b_id,
        participant_a_type=participant_a_type,
        participant_b_type=participant_b_type,
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
    result = await db.execute(
        select(HasnGroupMembers).where(HasnGroupMembers.conversation_id == conversation_id)
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
        db.add(HasnUnreadCounts(
            hasn_id=hasn_id,
            conversation_id=conversation_id,
            unread_count=1,
            last_read_msg_id=0,
        ))


# ─── 消息持久化 ───

def _entity_type_int(hasn_id: str) -> int:
    """hasn_id → from_type/to_type 数字"""
    if hasn_id.startswith('h_'):
        return 1  # human
    elif hasn_id.startswith('a_'):
        return 2  # agent
    elif hasn_id.startswith('g:'):
        return 4  # group
    elif hasn_id.startswith('sv_'):
        return 5  # service（服务号，统一通知服务 D8；不复用 3=system）
    return 3  # system


def _asset_id_from_uri(uri: Any) -> str | None:
    """hasn://asset/{asset_id} → asset_id。"""
    if isinstance(uri, str) and uri.startswith('hasn://asset/'):
        candidate = uri[len('hasn://asset/'):].strip('/')
        return candidate or None
    return None


async def _grant_private_attachments(db: AsyncSession, conversation_id: str, content: dict | None) -> None:
    """为消息内私有附件按会话写读权 grant（1f）。public 跳过，零越权。"""
    if not isinstance(content, dict):
        return
    attachments = content.get('attachments')
    if not isinstance(attachments, list) or not attachments:
        return
    asset_ids = [
        aid
        for a in attachments
        if isinstance(a, dict) and (aid := _asset_id_from_uri(a.get('uri')))
    ]
    if not asset_ids:
        return
    # 延迟 import 避免潜在循环依赖
    from backend.app.hasn.service.hasn_asset_service import hasn_asset_service

    assets = await hasn_asset_service.get_many(db, asset_ids)
    for asset in assets.values():
        if asset.access == 'private':
            await hasn_asset_service.grant_to_conversation(
                db, asset_id=asset.asset_id, conversation_id=conversation_id
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
) -> HasnMessages:
    """持久化消息并更新会话"""
    now = timezone.now()

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
        priority=priority,
        reply_to_id=reply_to_id,
        local_id=local_id,
        context=context,
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
            context={**(context or {}), 'conversation_type': 'group', 'group_id': to_id},
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

        await db.commit()

        from_entity_type = 'human' if from_id.startswith('h_') else ('agent' if from_id.startswith('a_') else 'system')
        hasn_envelope = {
            'id': msg.id,
            'conversation_id': group_conv_id,
            'from_id': from_id,
            'from_type': msg.from_type,
            'from_entity_type': from_entity_type,
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
            'group': {
                'group_id': to_id,
                'name': group.group_name,
                'owner_id': group.group_owner_id,
            },
        }
        payload = {
            'hasn': 'hasn/0.2',
            'method': 'hasn.message.received',
            'params': {
                'to_id': to_id,
                'message': hasn_envelope,
            }
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
    _ctx_meta = (context or {}) if context else {}
    _ctx_relation_type = _ctx_meta.get('relation_type') or (
        'social' if to_id.startswith('h_') or to_id.startswith('a_') else 'social'
    )
    _ctx_from_entity_type = (
        'human' if from_id.startswith('h_') else ('agent' if from_id.startswith('a_') else 'system')
    )
    _ctx_to_entity_type = target_info.get('entity_type', 'agent')
    perm_result = await permission_engine.evaluate(
        db,
        sender={
            'hasn_id': from_id,
            'entity_type': _ctx_from_entity_type,
        },
        receiver={
            'hasn_id': to_id,
            'owner_id': target_info.get('owner_id'),
            'entity_type': _ctx_to_entity_type,
        },
        envelope={
            'msg_type': msg_type,
            'content': content,
            'relation_type': _ctx_relation_type,
            'metadata': _ctx_meta,
            'from_entity_type': _ctx_from_entity_type,
        },
    )

    if perm_result.decision == DENY:
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
    from_type = 'human' if from_id.startswith('h_') else 'agent'
    relation_type = _ctx_relation_type or 'social'

    conv = await get_or_create_conversation(
        db, from_id, from_type, to_id, to_type, relation_type
    )

    # 5. 持久化
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
    )

    await db.commit()

    # 写入同步事件，供登录时通过 sync/pull 恢复历史消息
    from backend.app.hasn.service.hasn_sync_service import SqlAlchemySyncGateway
    _sync_gw = SqlAlchemySyncGateway()
    content_type_str = {1: 'text', 2: 'image/*', 3: 'application/octet-stream', 4: 'audio/*', 5: 'application/x.card+json'}.get(content_type, 'text')

    # 为发送方写入 message.sent 事件
    if from_id.startswith('a_'):
        # Agent 发送的消息，查 agent 的 owner_id。直接查表，避免依赖
        # hasn_agents_service 里并不存在的 get_agent_by_hasn_id —— 该错误导入
        # 会在 commit 之后、投递之前抛 ImportError，导致 Agent 回复持久化成功
        # 却从不推送给收件人（跨 owner「人收 Agent 回复」永远收不到）。
        _sender_owner_row = await db.execute(
            select(HasnAgents.owner_id).where(HasnAgents.hasn_id == from_id)
        )
        sender_owner_id = _sender_owner_row.scalar_one_or_none()
    else:
        # Human 发送的消息，from_id 就是 owner_id
        sender_owner_id = from_id

    if sender_owner_id:
        await _sync_gw._append_sync_event(
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
        await _sync_gw._append_sync_event(
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
        }
    }

    # 7. 投递
    await _push_message_to(to_id, payload)
    # Runtime 缺失/离线时，Human Owner 在线节点仍要能作为纯 IM 客户端收到发给自己 Agent 的消息。
    # 但必须排除 Agent 实体所在节点（已通过上面的 entity 投递收到），否则 Agent 跑在
    # 主人 daemon 上时同一节点会收两遍 → 镜像两次 + 派发 runtime 两次（发一条收两条回复）。
    if to_entity_type == 'agent' and target_info.get('owner_id') and target_info.get('owner_id') != to_id:
        from backend.app.hasn.service.ws_router import ws_router
        await ws_router.push_to_owner_excluding_agent_node(
            target_info['owner_id'], to_id, payload
        )

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
    result = await db.execute(
        select(HasnMessages).where(HasnMessages.id == msg_id)
    )
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
    import uuid
    from datetime import datetime, timedelta, timezone as dt_tz

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
