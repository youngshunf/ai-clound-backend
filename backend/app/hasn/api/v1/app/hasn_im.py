"""HASN IM 业务 API——会话列表、消息分页与已读。

所有 IM 读写均经 ``ImGateway`` 和 IM 受限角色执行；普通业务 Session、旧
``hasn_unread_counts`` 不再参与生产路径。
"""
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Path, Query
from pydantic import BaseModel, Field

from backend.app.hasn.service.hasn_auth import hasn_auth
from backend.app.hasn_im.application.errors import (
    ImConversationNotFound,
    ImSenderNotParticipant,
    ImSendRejected,
)
from backend.app.hasn_im.application.provider import get_im_gateway
from backend.app.hasn_im.ports.dto import (
    ActorKind,
    EnsureDirectConversationCommand,
    ListConversationsQuery,
    ListMessagesQuery,
    ReadCursorCommand,
    ServicePrincipal,
)
from backend.app.hasn_im.ports.im_gateway import ImGateway
from backend.common.response.response_schema import ResponseModel, response_base

router = APIRouter()
conversation_contract_router = APIRouter()


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


class EnsureConversationRequest(BaseModel):
    """确保直聊会话存在的请求。"""

    peer_hasn_id: str = Field(..., description='对端 HASN ID')
    relation_type: str | None = Field(default='social', description='关系类型')


class EnsureConversationResponse(BaseModel):
    """daemon 稳定会话 ensure 契约的响应。"""

    conversation_id: str = Field(..., description='云端权威会话 UUID')
    peer_hasn_id: str = Field(..., description='对端 HASN ID')
    kind: str = Field(..., description='会话类型')
    relation_type: str = Field(..., description='生效关系类型')


def _principal_from_auth(auth: dict) -> ServicePrincipal:
    """把服务端认证结果规范化为不可由请求体伪造的调用主体。"""
    canonical_sender = str(auth['hasn_id'])
    effective_sender = str(auth.get('effective_id') or canonical_sender)
    entity_type = auth.get('entity_type')
    actor_kind = (
        ActorKind.AGENT
        if entity_type == 'agent' or canonical_sender.startswith('a_')
        else ActorKind.HUMAN
    )
    return ServicePrincipal(
        canonical_sender=canonical_sender,
        actor_kind=actor_kind,
        send_as=effective_sender if effective_sender != canonical_sender else None,
    )


def _raise_http_error(exc: Exception) -> None:
    """把应用层结构化错误映射为稳定 HTTP 状态。"""
    if isinstance(exc, ImConversationNotFound):
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    if isinstance(exc, ImSenderNotParticipant):
        raise HTTPException(status_code=403, detail=str(exc)) from exc
    if isinstance(exc, ImSendRejected):
        status = 404 if exc.code == 3001 else 400
        raise HTTPException(status_code=status, detail=exc.message) from exc
    raise exc


# ─── daemon 稳定会话契约 ─────────────────────────────

@conversation_contract_router.post('/ensure', summary='确保直聊会话存在（幂等）')
async def ensure_conversation(
    body: EnsureConversationRequest,
    auth: Annotated[dict, Depends(hasn_auth)],
    gateway: Annotated[ImGateway, Depends(get_im_gateway)],
) -> ResponseModel:
    """保持 daemon 云端调用路径稳定，实际写入统一收口到 ``ImGateway``。"""
    relation_type = body.relation_type or 'social'
    try:
        conversation = await gateway.ensure_direct_conversation(
            EnsureDirectConversationCommand(
                peer_hasn_id=body.peer_hasn_id,
                relation_type=relation_type,
            ),
            _principal_from_auth(auth),
        )
    except (ImConversationNotFound, ImSenderNotParticipant, ImSendRejected) as exc:
        _raise_http_error(exc)
        raise AssertionError('不可达')
    return response_base.success(
        data=EnsureConversationResponse(
            conversation_id=conversation.conversation_id,
            peer_hasn_id=body.peer_hasn_id,
            kind=conversation.conversation_type,
            relation_type=conversation.relation_type,
        )
    )


# ─── 会话列表 ───────────────────────────────────

@router.get('/conversations', summary='获取我的会话列表')
async def list_my_conversations(
    auth: Annotated[dict, Depends(hasn_auth)],
    gateway: Annotated[ImGateway, Depends(get_im_gateway)],
    limit: Annotated[int, Query(ge=1, le=200)] = 200,
    cursor: Annotated[str | None, Query(description='稳定分页游标')] = None,
) -> ResponseModel:
    """按认证主体的活动 membership 查询会话。"""
    try:
        page = await gateway.list_conversations(
            ListConversationsQuery(limit=limit, cursor=cursor),
            _principal_from_auth(auth),
        )
    except (ImConversationNotFound, ImSenderNotParticipant, ImSendRejected) as exc:
        _raise_http_error(exc)
        raise AssertionError('不可达')
    return response_base.success(data=page.items)


@router.get('/conversations/{conversation_id}/messages', summary='获取会话消息（分页）')
async def list_conversation_messages(
    conversation_id: Annotated[str, Path(description='会话 ID 或对端的 hasn_id')],
    limit: Annotated[int, Query(ge=1, le=200)] = 50,
    before_id: Annotated[int | None, Query(description='游标: 返回 ID 小于此值的消息')] = None,
    auth: dict = Depends(hasn_auth),
    gateway: ImGateway = Depends(get_im_gateway),
) -> ResponseModel:
    """按 conversation_id 与成员周期分页读取消息。"""
    try:
        page = await gateway.list_messages(
            ListMessagesQuery(
                conversation_id=conversation_id,
                limit=limit,
                before_cursor=str(before_id) if before_id is not None else None,
            ),
            _principal_from_auth(auth),
        )
    except ImConversationNotFound:
        # 兼容未建立会话时用 peer_id 打开页面的旧行为。
        return response_base.success(data=[])
    except (ImSenderNotParticipant, ImSendRejected) as exc:
        _raise_http_error(exc)
        raise AssertionError('不可达')
    return response_base.success(data=page.items)


# ─── 已读标记 ───────────────────────────────────

@router.post('/conversations/{conversation_id}/read', summary='标记会话已读')
async def mark_conversation_read(
    obj: MarkReadReq,
    conversation_id: Annotated[str, Path(description='会话 ID 或对端 hasn_id')],
    auth: Annotated[dict, Depends(hasn_auth)],
    gateway: Annotated[ImGateway, Depends(get_im_gateway)],
) -> ResponseModel:
    """单调推进 membership ``read_seq`` 并重建当前主体未读投影。"""
    try:
        await gateway.advance_read_cursor(
            ReadCursorCommand(
                conversation_id=conversation_id,
                up_to_message_id=obj.last_msg_id if obj.last_msg_id > 0 else None,
            ),
            _principal_from_auth(auth),
        )
    except ImConversationNotFound:
        # 未建立会话或仍使用 peer_id 的旧客户端，保持幂等空成功。
        return response_base.success()
    except (ImSenderNotParticipant, ImSendRejected) as exc:
        _raise_http_error(exc)
        raise AssertionError('不可达')
    return response_base.success()
