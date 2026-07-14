"""P0 HASN sync/runtime report endpoints."""
from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Body, Path, Request
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from backend.app.hasn.schema.hasn_sync import (
    MemorySyncPullRequest,
    MemorySyncPullResponse,
    RuntimeReportRequest,
    RuntimeReportResponse,
    SyncPullRequest,
    SyncPullResponse,
    SyncPushRequest,
    SyncPushResponse,
)
from backend.app.hasn.service.conversation_projection import load_conversation_object
from backend.app.hasn.service.hasn_sync_service import hasn_sync_service
from backend.common.exception import errors
from backend.common.response.response_schema import ResponseSchemaModel, response_base
from backend.common.security.jwt import DependsJwtAuth
from backend.database.db import CurrentSession, CurrentSessionTransaction

router = APIRouter()


async def _resolve_owner_human_hasn_id(request: Request, db: AsyncSession) -> str:
    """从登录态解析主人 human hasn_id（缓存缺失回落库查），对齐 app/hasn_conversations。"""
    caller_hasn_id = request.user.hasn_id
    if caller_hasn_id:
        return caller_hasn_id
    from backend.app.hasn.crud.crud_hasn_humans import hasn_humans_dao

    hasn_human = await hasn_humans_dao.get_by_user_id(db, user_id=request.user.id)
    if hasn_human:
        return hasn_human.hasn_id
    raise errors.AuthorizationError(msg='用户未绑定 HASN ID')


@router.post('/sync/pull', summary='Pull HASN sync events after cursor', dependencies=[DependsJwtAuth])
async def pull_sync_events(
    request: Request,
    db: CurrentSession,
    request_body: SyncPullRequest,
) -> SyncPullResponse:
    return await hasn_sync_service.pull(db, request_body, user_id=request.user.id)


@router.post('/sync/push', summary='Push HASN local client events', dependencies=[DependsJwtAuth])
async def push_sync_events(
    request: Request,
    db: CurrentSessionTransaction,
    request_body: SyncPushRequest,
) -> SyncPushResponse:
    return await hasn_sync_service.push(db, request_body, user_id=request.user.id)


@router.post(
    '/memory/sync/pull',
    summary='Pull HASN memory namespace events after namespace cursors',
    dependencies=[DependsJwtAuth],
)
async def pull_memory_sync_events(
    request: Request,
    db: CurrentSession,
    request_body: MemorySyncPullRequest,
) -> MemorySyncPullResponse:
    return await hasn_sync_service.pull_memory(db, request_body, user_id=request.user.id)


@router.post('/runtime/report', summary='Report redacted local Runtime status', dependencies=[DependsJwtAuth])
async def report_runtime(
    request: Request,
    db: CurrentSessionTransaction,
    request_body: RuntimeReportRequest,
) -> RuntimeReportResponse:
    return await hasn_sync_service.report_runtime(db, request_body, user_id=request.user.id)


# ─── 会话对象读端点（会话一等实体重构 C1·doc02 §3.2）───
# daemon 唯一的会话认知来源：按 conversation_id 拉「会话对象投影」，替代从消息 payload 猜会话框定。


class BatchGetConversationsRequest(BaseModel):
    """批量拉会话对象（冷启动补拉场景，doc02 §7）。"""

    conversation_ids: list[str] = Field(..., description='要拉取的云端权威 conversation_id 列表', max_length=200)


@router.get(
    '/sync/conversations/{conversation_id}',
    summary='拉取会话对象投影（daemon 会话认知来源·doc02 §3.2）',
    dependencies=[DependsJwtAuth],
)
async def get_conversation_object(
    request: Request,
    db: CurrentSession,
    conversation_id: Annotated[str, Path(description='云端权威 conversation_id（UUID）')],
) -> ResponseSchemaModel[dict]:
    """按 conversation_id 拉会话对象投影。

    鉴权（doc02 §3.2「主人通道」权限本体）：请求 owner 必须是参与者本人 / 参与分身的主人 / 群成员——
    否则 403。会话不存在 → 404。命中 → 返回 `{conversation_id, type, participants[], group|null,
    revision, created_time, updated_time}`。
    """
    viewer_owner = await _resolve_owner_human_hasn_id(request, db)
    projection = await load_conversation_object(db, conversation_id, viewer_owner_hasn_id=viewer_owner)
    if projection is None:
        # 不区分「不存在」与「无权限」以免泄露存在性——统一 404（对齐会话 ACL 惯例）。
        raise errors.NotFoundError(msg='会话不存在或无权访问')
    return response_base.success(data=projection)


@router.post(
    '/sync/conversations:batch_get',
    summary='批量拉取会话对象投影（冷启动补拉·doc02 §7）',
    dependencies=[DependsJwtAuth],
)
async def batch_get_conversation_objects(
    request: Request,
    db: CurrentSession,
    body: Annotated[BatchGetConversationsRequest, Body()],
) -> ResponseSchemaModel[dict]:
    """批量拉会话对象——冷启动一次补拉多条，避免逐条 read-through 打爆详情端点（doc02 §7 兜底）。

    逐条按同一鉴权判权：无权/不存在的 conversation_id 静默跳过（不出现在返回里），
    有权的收进 `conversations` 数组。
    """
    viewer_owner = await _resolve_owner_human_hasn_id(request, db)
    out: list[dict] = []
    for cid in body.conversation_ids:
        projection = await load_conversation_object(db, cid, viewer_owner_hasn_id=viewer_owner)
        if projection is not None:
            out.append(projection)
    return response_base.success(data={'conversations': out})
