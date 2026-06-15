"""M1 移动端 App - POST /api/v1/app/telemetry/events (B8).

依赖规范: docs/架构设计/移动端/10-观测崩溃与日志详细设计.md §6.1 + §14.4。

客户端 (Android / iOS) 批量上报业务旅程事件; 友盟 U-App 作为并行通道 (客户端
侧 SDK), 后端 /telemetry/events 作为 Grafana 聚合口 (M1 不接 Firebase
Analytics, 见 scripts/ralph-B/CLAUDE.md §M1 技术栈约束)。

实现策略 (对齐 B4 / B7):
- JWT 鉴权 via DependsJwtAuth。
- hasn_id 由服务端 JWT → HasnHumans 反查; 客户端不传。
- event_type 白名单: TELEMETRY_EVENT_TYPE_VALUES (§6.1 枚举, 共 11 个)。
- occurred_at_unix_ms (int, ms since epoch) → 转换为 aware UTC datetime 落库。
- properties 字段为客户端已脱敏 JSON; 服务端不二次解析内容 (§7.1 永不记录原文)。
- 批量上限 100 条 / 请求 (超出 → 422), 避免滥用。

不变式 §4 + §7.1: 本接口 payload 永不含消息正文 / 凭据明文 / 任意 PII 原文字段;
客户端侧脱敏 (hash / 抽样), 服务端仅做 schema + 白名单校验。
"""
from __future__ import annotations

from datetime import datetime
from datetime import timezone as _py_timezone
from typing import Any

from fastapi import APIRouter, Request
from pydantic import Field
from sqlalchemy import select

from backend.app.hasn_client.model.telemetry_event import (
    TELEMETRY_EVENT_TYPE_VALUES,
    TelemetryEvent,
)
from backend.common.response.response_schema import (
    ResponseSchemaModel,
    response_base,
)
from backend.common.schema import SchemaBase
from backend.common.security.jwt import DependsJwtAuth
from backend.database.db import CurrentSessionTransaction  # noqa: TC001 — FastAPI runtime Depends annotation

router = APIRouter()

MAX_BATCH_SIZE: int = 100


class TelemetryEventPayload(SchemaBase):
    """单条埋点事件 payload."""

    event_type: str = Field(
        min_length=1,
        max_length=64,
        description='事件类型 (§6.1 白名单, 非法值会被 business 层拒绝)',
    )
    occurred_at_unix_ms: int = Field(
        gt=0,
        description='客户端触发事件的绝对时间 (ms since epoch, UTC)',
    )
    properties: dict[str, Any] | None = Field(
        default=None,
        description='事件属性 JSON (客户端脱敏, 不含 PII/凭据/正文)',
    )


class ReportTelemetryEventsRequest(SchemaBase):
    """POST /telemetry/events 请求体."""

    events: list[TelemetryEventPayload] = Field(
        min_length=1,
        max_length=MAX_BATCH_SIZE,
        description=f'批量事件列表 (1..{MAX_BATCH_SIZE} 条)',
    )


class TelemetryIngestSummary(SchemaBase):
    """POST /telemetry/events 响应载荷 (只返回汇总, 不回显事件)."""

    ingested: int


async def _resolve_hasn_id(db: Any, user_id: int) -> str:
    """反查当前用户的 hasn_id; 未开通 → NotFoundError."""
    from backend.app.hasn_core import HasnHumans
    from backend.common.exception import errors

    result = await db.execute(
        select(HasnHumans).where(HasnHumans.user_id == user_id).limit(1)
    )
    human = result.scalars().first()
    if human is None:
        raise errors.NotFoundError(msg='当前用户尚未开通 HASN 身份, 请先完成唤星账号初始化')
    return human.hasn_id


def _validate_event_type(event_type: str) -> None:
    """event_type 白名单校验 (§6.1 枚举)."""
    from backend.common.exception import errors

    if event_type not in TELEMETRY_EVENT_TYPE_VALUES:
        raise errors.RequestError(
            msg=(
                f'event_type 不支持: {event_type!r}; '
                f'允许值: {sorted(TELEMETRY_EVENT_TYPE_VALUES)}'
            )
        )


async def record_telemetry_events_for_user(
    db: Any,
    *,
    user_id: int,
    events: list[TelemetryEventPayload],
) -> list[TelemetryEvent]:
    """业务入口: 批量落 telemetry_events; 返回新 ORM 行列表 (id 已 flush).

    - event_type 白名单校验 (§6.1, 非法 → RequestError 400)。
    - occurred_at_unix_ms → UTC aware datetime。
    - hasn_id 由 user_id → HasnHumans 反查 (客户端不传)。
    - 事件顺序保持与请求一致。
    """
    for evt in events:
        _validate_event_type(evt.event_type)

    hasn_id = await _resolve_hasn_id(db, user_id)

    rows: list[TelemetryEvent] = []
    for evt in events:
        occurred_at = datetime.fromtimestamp(
            evt.occurred_at_unix_ms / 1000.0, tz=_py_timezone.utc
        )
        row = TelemetryEvent(
            hasn_id=hasn_id,
            event_type=evt.event_type,
            properties=evt.properties,
            occurred_at=occurred_at,
        )
        db.add(row)
        rows.append(row)

    await db.flush()
    return rows


@router.post(
    '/events',
    summary='批量上报业务埋点事件 (移动端 M1)',
    dependencies=[DependsJwtAuth],
)
async def report_telemetry_events(
    request: Request,
    payload: ReportTelemetryEventsRequest,
    db: CurrentSessionTransaction,
) -> ResponseSchemaModel[TelemetryIngestSummary]:
    """POST /api/v1/app/telemetry/events — 客户端批量上报业务埋点.

    200: {data: {ingested: N}}
    400: event_type 白名单不通过 (RequestError)
    401: 未登录
    404: 当前用户尚未开通 HASN 身份
    422: 批量大小越界 / Pydantic 校验失败
    """
    user_id = request.user.id
    rows = await record_telemetry_events_for_user(
        db,
        user_id=user_id,
        events=payload.events,
    )
    return response_base.success(data=TelemetryIngestSummary(ingested=len(rows)))
