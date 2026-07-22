"""HASN IM 业务 API — 会话列表 + 消息分页 + 已读

对齐协议: Core/03-消息与通信.md §4 会话管理
认证方式: hasn_auth (JWT / Owner API Key / Agent Key)
"""
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Path, Query
from pydantic import BaseModel, Field
from sqlalchemy import and_, desc, or_, select

from backend.app.hasn.model import HasnConversations, HasnHumans, HasnMessages, HasnUnreadCounts
from backend.app.hasn.model.hasn_agents import HasnAgents
from backend.app.hasn.service.hasn_auth import hasn_auth
from backend.app.hasn_im.domain.read_cursor import monotonic_read_cursor
from backend.common.response.response_schema import ResponseModel, response_base
from backend.database.db import CurrentSession

router = APIRouter()


# ─── 响应结构 ───────────────────────────────────

class ConversationOut(BaseModel):
    """会话列表项"""
    id: str
    type: str  # direct / group
    peer_id: str  # 对端 hasn_id
    peer_name: str
    peer_type: str  # human / agent
    peer_avatar: str | None = None
    relation_type: str | None = None
    last_message_preview: str | None = None
    last_message_at: str | None = None
    last_message_from: str | None = None
    unread_count: int = 0
    message_count: int = 0


class HasnEnvelopeOut(BaseModel):
    """符合协议 §2.1 的 HasnEnvelope（简化版）"""
    id: str
    version: str = '1.0'
    type: str = 'message'
    from_ref: dict = Field(serialization_alias='from')  # {hasn_id, entity_type, owner_id}
    to_ref: dict = Field(serialization_alias='to')       # {hasn_id, entity_type, owner_id}
    content: dict   # {content_type, body}
    context: dict   # {conversation_id, relation_type, ...}
    metadata: dict  # {priority, created_at, server_received_at}
    local_id: str | None = None


class MarkReadReq(BaseModel):
    last_msg_id: int = 0


# ─── 会话列表 ───────────────────────────────────

@router.get('/conversations', summary='获取我的会话列表')
async def list_my_conversations(
    db: CurrentSession,
    auth: Annotated[dict, Depends(hasn_auth)],
) -> ResponseModel:
    """按当前用户 hasn_id 查询所参与的所有活跃会话"""
    hasn_id = auth.get('effective_id', auth['hasn_id'])

    # 查询我作为 participant_a 或 participant_b 的所有会话
    result = await db.execute(
        select(HasnConversations)
        .where(
            or_(
                HasnConversations.participant_a_id == hasn_id,
                HasnConversations.participant_b_id == hasn_id,
            )
        )
        .order_by(desc(HasnConversations.last_message_at))
    )
    conversations = result.scalars().all()

    # 批量查未读
    unread_map: dict[str, int] = {}
    if conversations:
        conv_ids = [str(c.id) for c in conversations]
        unread_result = await db.execute(
            select(HasnUnreadCounts)
            .where(
                HasnUnreadCounts.hasn_id == hasn_id,
                HasnUnreadCounts.conversation_id.in_(conv_ids),
            )
        )
        for u in unread_result.scalars().all():
            unread_map[str(u.conversation_id)] = u.unread_count or 0

    items = []
    for conv in conversations:
        # 确定对端
        if conv.participant_a_id == hasn_id:
            peer_id = conv.participant_b_id or ''
            peer_type = conv.participant_b_type or 'human'
        else:
            peer_id = conv.participant_a_id or ''
            peer_type = conv.participant_a_type or 'human'

        # 查对端名称和头像
        peer_name = peer_id
        peer_avatar = None
        if peer_id.startswith('h_'):
            human_result = await db.execute(
                select(HasnHumans).where(HasnHumans.hasn_id == peer_id)
            )
            human = human_result.scalar_one_or_none()
            if human:
                peer_name = human.nickname or peer_id
                peer_avatar = getattr(human, 'avatar', None)
        elif peer_id.startswith('a_'):
            agent_result = await db.execute(
                select(HasnAgents).where(HasnAgents.hasn_id == peer_id)
            )
            agent = agent_result.scalar_one_or_none()
            if agent:
                peer_name = agent.display_name or peer_id
                peer_avatar = getattr(agent, 'avatar', None)

        items.append(ConversationOut(
            id=str(conv.id),
            type=conv.type or 'direct',
            peer_id=peer_id,
            peer_name=peer_name,
            peer_type=peer_type,
            peer_avatar=peer_avatar,
            relation_type=conv.relation_type,
            last_message_preview=conv.last_message_preview,
            last_message_at=str(conv.last_message_at) if conv.last_message_at else None,
            last_message_from=conv.last_message_from,
            unread_count=unread_map.get(str(conv.id), 0),
            message_count=conv.message_count or 0,
        ).model_dump())

    return response_base.success(data=items)


# ─── 消息分页 ───────────────────────────────────

def _entity_type_str(hasn_id: str) -> str:
    if hasn_id.startswith('h_'):
        return 'human'
    if hasn_id.startswith('a_'):
        return 'agent'
    return 'system'


def _content_type_str(ct: int) -> str:
    return {1: 'text', 2: 'image', 3: 'file', 4: 'voice', 5: 'card',
            6: 'capability_request', 7: 'capability_response'}.get(ct, 'text')


@router.get('/conversations/{conversation_id}/messages', summary='获取会话消息（分页）')
async def list_conversation_messages(
    db: CurrentSession,
    conversation_id: Annotated[str, Path(description='会话 ID 或对端的 hasn_id')],
    limit: Annotated[int, Query(ge=1, le=200)] = 50,
    before_id: Annotated[int | None, Query(description='游标: 返回 ID 小于此值的消息')] = None,
    auth: dict = Depends(hasn_auth),
) -> ResponseModel:
    """按 conversation_id 分页查消息，支持未建会话前使用 peer_id 作为临时 id 查询"""
    hasn_id = auth.get('effective_id', auth['hasn_id'])

    # 验证用户是否是该会话的参与者
    try:
        from uuid import UUID
        conv_uuid = UUID(conversation_id)
        conv = await db.get(HasnConversations, conv_uuid)
    except ValueError:
        # 用 peer_id 查两人是否有单聊会话
        peer_id = conversation_id
        conversation_result = await db.execute(
            select(HasnConversations).where(
                or_(
                    and_(HasnConversations.participant_a_id == hasn_id, HasnConversations.participant_b_id == peer_id),
                    and_(HasnConversations.participant_b_id == hasn_id, HasnConversations.participant_a_id == peer_id)
                )
            )
        )
        conv = conversation_result.scalar_one_or_none()

    if not conv:
        # 如果既不是正确数字ID，也没找到双边单聊（如真的未创建），返回空列表
        return response_base.success(data=[])

    if hasn_id not in (conv.participant_a_id, conv.participant_b_id):
        raise HTTPException(status_code=403, detail='无权访问该会话')

    actual_conv_id = conv.id

    # 查消息
    query = (
        select(HasnMessages)
        .where(HasnMessages.conversation_id == str(actual_conv_id))
        .order_by(desc(HasnMessages.id))
        .limit(limit)
    )
    if before_id:
        query = query.where(HasnMessages.id < before_id)

    message_result = await db.execute(query)
    messages = message_result.scalars().all()

    # 构造 HasnEnvelope
    envelopes = []
    for msg in reversed(messages):  # 恢复时间正序
        envelope = {
            'id': f'msg_{msg.id}',
            'version': '1.0',
            'type': msg.msg_type or 'message',
            'from': {
                'hasn_id': msg.from_id,
                'entity_type': _entity_type_str(msg.from_id),
                'owner_id': msg.from_id,
            },
            'to': {
                'hasn_id': msg.to_id,
                'entity_type': _entity_type_str(msg.to_id),
                'owner_id': msg.to_id,
            },
            'content': {
                'content_type': _content_type_str(msg.content_type),
                'body': msg.content if isinstance(msg.content, dict) else {'text': str(msg.content)},
            },
            'context': {
                'conversation_id': str(msg.conversation_id),
                'relation_type': conv.relation_type,
                'reply_to': str(msg.reply_to_id) if msg.reply_to_id else None,
            },
            'metadata': {
                'priority': msg.priority or 'normal',
                'created_at': msg.created_time.isoformat() if msg.created_time else None,
                'server_received_at': msg.server_received_at.isoformat() if msg.server_received_at else None,
            },
            'local_id': str(msg.local_id) if msg.local_id else None,
        }
        envelopes.append(envelope)

    return response_base.success(data=envelopes)


# ─── 已读标记 ───────────────────────────────────

@router.post('/conversations/{conversation_id}/read', summary='标记会话已读')
async def mark_conversation_read(
    db: CurrentSession,
    obj: MarkReadReq,
    conversation_id: Annotated[str, Path(description='会话 ID 或对端 hasn_id')],
    auth: Annotated[dict, Depends(hasn_auth)],
) -> ResponseModel:
    """标记会话已读（清零未读计数）"""
    hasn_id = auth.get('effective_id', auth['hasn_id'])

    try:
        from uuid import UUID
        conv = await db.get(HasnConversations, UUID(conversation_id))
    except ValueError:
        peer_id = conversation_id
        conversation_result = await db.execute(
            select(HasnConversations).where(
                or_(
                    and_(HasnConversations.participant_a_id == hasn_id, HasnConversations.participant_b_id == peer_id),
                    and_(HasnConversations.participant_b_id == hasn_id, HasnConversations.participant_a_id == peer_id)
                )
            )
        )
        conv = conversation_result.scalar_one_or_none()

    if not conv:
        return response_base.success()

    if hasn_id not in (conv.participant_a_id, conv.participant_b_id):
        raise HTTPException(status_code=403, detail='无权访问该会话')

    conv_id_str = str(conv.id)

    unread_result = await db.execute(
        select(HasnUnreadCounts).where(
            HasnUnreadCounts.hasn_id == hasn_id,
            HasnUnreadCounts.conversation_id == conv_id_str,
        )
    )
    unread = unread_result.scalar_one_or_none()

    if unread:
        unread.unread_count = 0
        # 已读游标只进不退（§4.3）：陈旧/乱序低值请求不得把游标拨回去（旧代码无条件覆写会倒退）。
        unread.last_read_msg_id = monotonic_read_cursor(unread.last_read_msg_id, obj.last_msg_id)
    else:
        unread = HasnUnreadCounts(
            hasn_id=hasn_id,
            conversation_id=conv_id_str,
            unread_count=0,
            last_read_msg_id=monotonic_read_cursor(None, obj.last_msg_id),
        )
        db.add(unread)

    await db.commit()
    return response_base.success()
