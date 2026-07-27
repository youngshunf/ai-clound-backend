"""P0 HASN sync/runtime report endpoints."""
from __future__ import annotations

from dataclasses import asdict
from typing import Annotated

from fastapi import APIRouter, Body, Path, Request
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from backend.app.hasn.schema.hasn_sync import (
    MemorySyncPullRequest,
    MemorySyncPullResponse,
    FullRefreshDirective,
    RuntimeReportRequest,
    RuntimeReportResponse,
    SyncEventRecord,
    SyncPullRequest,
    SyncPullResponse,
    SyncPushRequest,
    SyncPushResponse,
)
from backend.app.hasn.schema.hasn_message_hub import ErrorObject
from backend.app.hasn.service._sync_codec import _contains_private_runtime_key
from backend.app.hasn.service.conversation_projection import load_conversation_object
from backend.app.hasn.service.hasn_sync_service import hasn_sync_service
from backend.app.hasn.service.sync_business_handlers import (
    MEMORY_SYNC_EVENTS,
    SESSION_SYNC_EVENT,
)
from backend.app.hasn.service.sync_entry_auth import (
    require_node_identity,
    require_owner_identity,
)
from backend.app.hasn_sync.adapters.sqlalchemy_store import SQLAlchemySyncStore
from backend.app.hasn_sync.application.pull import pull_events
from backend.app.hasn_sync.application.push import accept_envelopes
from backend.app.hasn_sync.domain.cursor import owner_cursor
from backend.app.hasn_sync.ports.dto import InboxEnvelope
from backend.common.exception import errors
from backend.common.response.response_schema import ResponseSchemaModel, response_base
from backend.common.security.jwt import DependsJwtAuth
from backend.database.db import (
    CurrentImSession,
    CurrentSessionTransaction,
    CurrentSyncSession,
    CurrentSyncSessionTransaction,
)

router = APIRouter()
_GENERAL_PUSH_EVENTS = MEMORY_SYNC_EVENTS | {SESSION_SYNC_EVENT}
_PRIVATE_METADATA_ERROR = ErrorObject(
    code=8034,
    name='ERR_RUNTIME_PRIVATE_METADATA_REJECTED',
    message='同步载荷包含本地私有元数据',
)
_UNSUPPORTED_EVENT_ERROR = ErrorObject(
    code=8040,
    name='ERR_SYNC_EVENT_UNSUPPORTED',
    message='当前版本不支持该同步事件类型',
)
_CONFLICT_ERROR = ErrorObject(
    code=8041,
    name='ERR_SYNC_EVENT_CONFLICT',
    message='同一客户端事件 ID 的载荷与已接收事件不一致',
)


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
    db: CurrentSyncSession,
    request_body: SyncPullRequest,
) -> SyncPullResponse:
    owner_id = require_owner_identity(request, request_body.owner_id)
    result = await pull_events(
        db,
        owner_id=owner_id,
        cursor=request_body.cursor,
        limit=request_body.limit,
    )
    return SyncPullResponse(
        events=[
            SyncEventRecord(
                event_id=event.event_id,
                event_type=event.event_type,
                revision=event.revision,
                created_at=event.occurred_at,
                payload=event.payload,
            )
            for event in result.events
        ],
        next_cursor=result.next_cursor,
        has_more=result.has_more,
        full_refresh=(
            FullRefreshDirective(**asdict(result.full_refresh))
            if result.full_refresh is not None
            else None
        ),
    )


@router.post('/sync/push', summary='Push HASN local client events', dependencies=[DependsJwtAuth])
async def push_sync_events(
    request: Request,
    db: CurrentSyncSessionTransaction,
    request_body: SyncPushRequest,
) -> SyncPushResponse:
    owner_id = require_owner_identity(request, request_body.owner_id)
    node_id = require_node_identity(request, request_body.node_id)
    envelopes: list[InboxEnvelope] = []
    rejected: list[ErrorObject] = []
    for event in request_body.events:
        if _contains_private_runtime_key(event.payload):
            rejected.append(_PRIVATE_METADATA_ERROR)
            continue
        if event.event_type not in _GENERAL_PUSH_EVENTS:
            rejected.append(_UNSUPPORTED_EVENT_ERROR)
            continue
        subject_hasn_id = event.hasn_id
        if not subject_hasn_id and event.event_type == SESSION_SYNC_EVENT:
            payload_hasn_id = event.payload.get('hasn_id')
            if isinstance(payload_hasn_id, str):
                subject_hasn_id = payload_hasn_id
        envelopes.append(
            InboxEnvelope(
                owner_id=owner_id,
                node_id=node_id,
                client_event_id=event.client_event_id,
                hasn_id=subject_hasn_id or owner_id,
                event_type=event.event_type,
                payload=event.payload,
                dedupe_key=event.dedupe_key,
            )
        )
    result = await accept_envelopes(db, envelopes)
    for item in result.items:
        if item.status == 'conflict':
            rejected.append(
                _CONFLICT_ERROR.model_copy(
                    update={'detail': {'client_event_id': item.client_event_id}}
                )
            )
    bounds = await SQLAlchemySyncStore().stream_bounds(db, owner_id=owner_id)
    return SyncPushResponse(
        accepted=result.accepted,
        rejected=rejected,
        next_cursor=owner_cursor(owner_id, bounds.head_revision),
    )


@router.post(
    '/memory/sync/pull',
    summary='Pull HASN memory namespace events after namespace cursors',
    dependencies=[DependsJwtAuth],
)
async def pull_memory_sync_events(
    request: Request,
    db: CurrentSyncSession,
    request_body: MemorySyncPullRequest,
) -> MemorySyncPullResponse:
    require_owner_identity(request, request_body.owner_id)
    return await hasn_sync_service.pull_memory(db, request_body, user_id=None)


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
    db: CurrentImSession,
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
    db: CurrentImSession,
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
