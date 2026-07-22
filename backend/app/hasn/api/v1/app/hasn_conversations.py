"""HASN 会话 - 用户端 API

认证方式: DependsJwtAuth（仅当前登录用户）
数据隔离: 通过 request.user.id 限制为用户自己的数据
"""
from typing import Annotated

from fastapi import APIRouter, Body, Request
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from backend.app.hasn.service.hasn_conversations_service import hasn_conversations_service
from backend.common.exception import errors
from backend.common.response.response_schema import ResponseSchemaModel, response_base
from backend.common.security.jwt import DependsJwtAuth
from backend.database.db import CurrentSessionTransaction


class EnsureConversationRequest(BaseModel):
    """确保会话存在的请求参数"""
    peer_hasn_id: str = Field(..., description='对方的 HASN ID')
    relation_type: str | None = Field(default='social', description='关系类型')


class EnsureConversationResponse(BaseModel):
    """确保会话存在的响应"""
    conversation_id: str = Field(..., description='会话 UUID')
    peer_hasn_id: str = Field(..., description='对方的 HASN ID')
    kind: str = Field(..., description='会话类型（direct 表示 1:1）')
    relation_type: str = Field(..., description='关系类型')


class SyncOwnerMessageRequest(BaseModel):
    """owner↔自有分身 loopback 消息上行（doc16 Phase A1）。"""
    agent_hasn_id: str = Field(..., description='本主人名下分身的 HASN ID（a_*）')
    direction: str = Field(..., description='方向：outbound=主人→分身 / inbound=分身→主人')
    content: dict = Field(..., description='消息内容（JSONB，文本为 {"text": ...}）')
    local_id: str = Field(..., description='客户端本地 ID（幂等去重键，daemon 生成的全局唯一 uuid）')
    content_type: int = Field(default=1, description='内容类型 (1:文本/2:图片/3:文件/4:语音/5:卡片)')
    msg_type: str = Field(default='message', description='消息类型')
    created_at: int | None = Field(default=None, description='客户端发送时间（unix 秒，保留真实时序）')
    process_blocks: list[dict] | None = Field(default=None, description='消息生成过程块')


class SyncOwnerMessageResponse(BaseModel):
    """消息上行结果：云端权威 id + 是否命中去重。"""
    message_id: str = Field(..., description='云端权威 message id')
    conversation_id: str = Field(..., description='云端权威 conversation id')
    deduped: bool = Field(..., description='是否命中 local_id 去重（已上云过）')


router = APIRouter()


async def _resolve_owner_human_hasn_id(request: Request, db: AsyncSession) -> str:
    """从登录态解析主人 human hasn_id（缓存缺失时回落库查），对齐 ensure_conversation。"""
    caller_hasn_id = request.user.hasn_id
    if caller_hasn_id:
        return caller_hasn_id
    from backend.app.hasn.crud.crud_hasn_humans import hasn_humans_dao

    hasn_human = await hasn_humans_dao.get_by_user_id(db, user_id=request.user.id)
    if hasn_human:
        return hasn_human.hasn_id
    raise errors.AuthorizationError(msg='用户未绑定 HASN ID')


@router.post(
    '/messages:sync',
    summary='owner↔自有分身消息上行（幂等，doc16 Phase A1）',
    dependencies=[DependsJwtAuth],
)
async def sync_owner_conversation_message_endpoint(
    request: Request,
    db: CurrentSessionTransaction,
    body: Annotated[SyncOwnerMessageRequest, Body()],
) -> ResponseSchemaModel[SyncOwnerMessageResponse]:
    """把主人与自己分身的一条 loopback 消息异步 upsert 进云端会话表。

    纯持久化：以 ``local_id`` 去重、复用 CONV-C1 原子会话、不重投递（无 WS/dispatch/未读）。
    返回云端权威 message/conversation id（铁律：跨设备/分享一律云端权威 id）。
    """
    from backend.app.hasn.service.owner_message_sync_service import sync_owner_conversation_message

    owner_human_hasn_id = await _resolve_owner_human_hasn_id(request, db)
    result = await sync_owner_conversation_message(
        db,
        owner_human_hasn_id=owner_human_hasn_id,
        agent_hasn_id=body.agent_hasn_id,
        direction=body.direction,
        content=body.content,
        content_type=body.content_type,
        msg_type=body.msg_type,
        local_id=body.local_id,
        created_at_unix=body.created_at,
        process_blocks=body.process_blocks,
    )
    return response_base.success(
        data=SyncOwnerMessageResponse(
            message_id=result.message_id,
            conversation_id=result.conversation_id,
            deduped=result.deduped,
        )
    )


@router.post(
    '/ensure',
    summary='确保会话存在（幂等创建）',
    dependencies=[DependsJwtAuth],
)
async def ensure_conversation(
    request: Request,
    db: CurrentSessionTransaction,
    body: Annotated[EnsureConversationRequest, Body()],
) -> ResponseSchemaModel[EnsureConversationResponse]:
    """
    确保 1:1 会话存在。

    根据调用者和对方的 HASN ID 查找或创建会话。
    同一对参与者总是返回相同的 conversation_id（基于排序后的参与者对）。
    """
    # 获取当前用户的 HASN ID
    caller_hasn_id = request.user.hasn_id
    if not caller_hasn_id:
        # 临时调试：如果缓存中没有 hasn_id，尝试从数据库查询
        from backend.app.hasn.crud.crud_hasn_humans import hasn_humans_dao
        hasn_human = await hasn_humans_dao.get_by_user_id(db, user_id=request.user.id)
        if hasn_human:
            caller_hasn_id = hasn_human.hasn_id
        else:
            raise errors.AuthorizationError(msg='用户未绑定 HASN ID')

    relation_type = body.relation_type or 'social'

    conversation = await hasn_conversations_service.ensure_conversation(
        db=db,
        caller_hasn_id=caller_hasn_id,
        peer_hasn_id=body.peer_hasn_id,
        relation_type=relation_type,
    )

    response_data = EnsureConversationResponse(
        conversation_id=str(conversation.id),
        peer_hasn_id=body.peer_hasn_id,
        kind='direct',
        relation_type=conversation.relation_type or relation_type,
    )

    return response_base.success(data=response_data)
