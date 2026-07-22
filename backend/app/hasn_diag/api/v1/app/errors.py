"""错误诊断 owner 端 API（Owner JWT）。

- `POST /errors:sync`：daemon 批量上行错误（幂等落库 + issue 聚合），每 node/每 owner 限频；
- `GET  /errors`：owner 只读自己设备的原始 occurrence（owner 隔离，§8.1）。

owner_hasn_id 一律从 JWT 解析、绝不信客户端；node_id 客户端自报仅作设备维度键。
"""

from __future__ import annotations

from typing import Annotated

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
from backend.database.db import CurrentSession, CurrentSessionTransaction
from backend.database.redis import redis_client

router = APIRouter(prefix='/errors', tags=['错误诊断-用户端'])

# 限频口径（§9）：每 owner 每分钟请求数封顶 + 每 node 每分钟请求数封顶（node 自报，故 owner 兜底）。
_OWNER_LIMIT_PER_MIN = 240
_NODE_LIMIT_PER_MIN = 120
# 固定窗口长度（秒）。计数桶必须带此 TTL，过期即重置窗口。
_RATE_WINDOW_SECONDS = 60


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
    """Redis 固定窗口计数限频；Redis 不可达时 fail-open（不阻断上报）。

    ⚠️ TTL 必须自愈——这是本函数的核心不变量。历史写法「仅在 count==1 时 expire」有
    致命竞态：`incr` 建桶后紧跟的 `expire` 若没落上（进程重启/异常/并发抢先），桶就
    **永久无 TTL（ttl=-1）**，count 只增不减，一旦越过 limit 对该 owner/node **永久 429**
    ——与真实请求速率彻底脱钩（实测有 owner 桶卡在 6798、node 桶卡在 1277，把每分钟仅
    1 次的上报也全部打成 429，且冷却/自反馈守卫等客户端修复根本无法解毒）。

    改为「INCR 后只要 ttl<0 就重新武装窗口」：
    - `ttl == -1`（键存在但无过期）——刚被 INCR 建出的新桶，或历史毒桶，都在这里自愈；
    - `ttl == -2`（键在 INCR 与 TTL 之间恰好过期，极罕见）——EXPIRE 对不存在的键 no-op，
      本次少设一个窗口、下次请求重建，无害。
    这样新桶必设窗口、存量毒桶下次请求即自愈，杜绝「永久 429」再次形成。
    """
    if not key:
        return
    bucket = f'diag:ratelimit:{dimension}:{key}'
    try:
        count = await redis_client.incr(bucket)
        # ttl<0 覆盖「新建无过期」与「毒桶无过期」两种情况，一律重新武装固定窗口。
        if await redis_client.ttl(bucket) < 0:
            await redis_client.expire(bucket, _RATE_WINDOW_SECONDS)
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
