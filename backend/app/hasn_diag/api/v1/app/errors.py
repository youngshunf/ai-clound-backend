"""错误诊断 owner 端 API（Owner JWT）。

- `POST /errors:sync`：daemon 批量上行错误（幂等落库 + issue 聚合），每 node/每 owner 限频；
- `GET  /errors`：owner 只读自己设备的原始 occurrence（owner 隔离，§8.1）。

owner_hasn_id 一律从 JWT 解析、绝不信客户端；node_id 客户端自报仅作设备维度键。
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Annotated

from fastapi import APIRouter, Body, HTTPException, Query, Request, status

from backend.app.hasn_core import hasn_humans_dao
from backend.app.hasn_diag.schema.error_sync import (
    DiagErrorSyncRequest,
    DiagErrorSyncResponse,
    DiagErrorSyncResult,
)
from backend.app.hasn_diag.service import error_issue_service
from backend.app.hasn_diag.service.error_report_service import IngestEvent, ingest_errors
from backend.common.exception import errors
from backend.common.response.response_schema import ResponseSchemaModel, response_base
from backend.common.security.jwt import DependsJwtAuth
from backend.database.redis import redis_client

if TYPE_CHECKING:
    from backend.database.db import CurrentSession, CurrentSessionTransaction

router = APIRouter(prefix='/errors', tags=['错误诊断-用户端'])

# 限频口径（§9）：每 owner 每分钟请求数封顶 + 每 node 每分钟请求数封顶（node 自报，故 owner 兜底）。
_OWNER_LIMIT_PER_MIN = 240
_NODE_LIMIT_PER_MIN = 120


async def _current_owner_id(request: Request, db: CurrentSession) -> str:
    """解析当前登录用户的 owner hasn_id（无 HASN 身份 → 403）。"""
    owner_id = getattr(request.user, 'hasn_id', None)
    if owner_id:
        return owner_id
    hasn_human = await hasn_humans_dao.get_by_user_id(db, user_id=request.user.id)
    if not hasn_human:
        raise errors.ForbiddenError(msg='当前用户未注册 HASN 身份')
    return hasn_human.hasn_id


async def _rate_limit(dimension: str, key: str, limit: int) -> None:
    """Redis 滑窗计数限频；Redis 不可达时 fail-open（不阻断上报）。"""
    if not key:
        return
    bucket = f'diag:ratelimit:{dimension}:{key}'
    try:
        count = await redis_client.incr(bucket)
        if count == 1:
            await redis_client.expire(bucket, 60)
    except Exception:
        return
    if count > limit:
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail=f'diag 上报超过 {dimension} 限频（{limit}/min）',
        )


@router.post(
    ':sync',
    summary='错误批量上行（幂等落库 + issue 聚合）',
    dependencies=[DependsJwtAuth],
)
async def sync_errors(
    request: Request,
    db: CurrentSessionTransaction,
    body: Annotated[DiagErrorSyncRequest, Body()],
) -> ResponseSchemaModel[DiagErrorSyncResponse]:
    owner_hasn_id = await _current_owner_id(request, db)
    await _rate_limit('owner', owner_hasn_id, _OWNER_LIMIT_PER_MIN)
    await _rate_limit('node', body.node_id, _NODE_LIMIT_PER_MIN)

    events = [
        IngestEvent(
            local_event_id=e.local_event_id,
            source=e.source,
            severity=e.severity,
            fingerprint=e.fingerprint,
            dedup_key=e.dedup_key,
            error_class=e.error_class,
            message=e.message,
            location=e.location,
            context=e.context or {},
            occurred_at=e.occurred_at_dt(),
            suppressed_count=e.suppressed_count,
        )
        for e in body.events
    ]
    results = await ingest_errors(
        db,
        owner_hasn_id=owner_hasn_id,
        node_id=body.node_id,
        app_version=body.app_version,
        platform=body.platform,
        events=events,
    )
    return response_base.success(
        data=DiagErrorSyncResponse(
            results=[DiagErrorSyncResult(**r) for r in results]
        )
    )


@router.get(
    '',
    summary='获取我的设备错误 occurrence（owner 隔离）',
    dependencies=[DependsJwtAuth],
)
async def list_my_errors(
    request: Request,
    db: CurrentSession,
    limit: Annotated[int, Query(ge=1, le=100)] = 20,
    cursor: Annotated[str | None, Query()] = None,
) -> ResponseSchemaModel[dict]:
    owner_hasn_id = await _current_owner_id(request, db)
    page = await error_issue_service.list_reports_by_owner(
        db, owner_hasn_id=owner_hasn_id, limit=limit, cursor=cursor
    )
    return response_base.success(data=page)
