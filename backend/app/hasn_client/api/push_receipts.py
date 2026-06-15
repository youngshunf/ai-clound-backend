"""M1 移动端 App - POST /api/v1/app/push_receipts (B7).

依赖规范: docs/架构设计/移动端/04-推送触达与后台运行模型详细设计.md §12.3。

客户端收到 U-Push 推送后回传 {trace_id, received_at_unix_ms, channel} →
服务端落 push_receipts 表, 用于到达率指标 (B9 push_received_total /
push_dispatched_total 比率)。

实现策略 (对齐 B4 push_tokens):
- JWT 鉴权 via DependsJwtAuth。
- hasn_id 由服务端 JWT→HasnHumans 反查; 客户端不传。
- received_at_unix_ms (int, ms since epoch) → 转换为 aware UTC datetime 落库。
- channel 白名单: 仅 PUSH_CHANNEL_VALUES (M1 固定 'umeng_push')。

不变式 §4: 推送 payload 不带消息正文, 回执也不含正文字段。
"""
from __future__ import annotations

from datetime import datetime
from datetime import timezone as _py_timezone
from typing import Any

from fastapi import APIRouter, Request
from pydantic import Field
from sqlalchemy import select

from backend.app.hasn_client.model.push_receipt import PushReceipt
from backend.app.hasn_client.model.push_token import PUSH_CHANNEL_VALUES, PushChannel
from backend.common.response.response_schema import (
    ResponseSchemaModel,
    response_base,
)
from backend.common.schema import SchemaBase
from backend.common.security.jwt import DependsJwtAuth
from backend.database.db import CurrentSessionTransaction  # noqa: TC001 — FastAPI runtime Depends annotation

router = APIRouter()


class ReportPushReceiptRequest(SchemaBase):
    """POST /push_receipts 请求体."""

    trace_id: str = Field(
        min_length=1,
        max_length=128,
        description='推送 trace (B6 生成, 例: conv:{conversation_id})',
    )
    received_at_unix_ms: int = Field(
        gt=0,
        description='客户端收到推送的绝对时间 (ms since epoch, UTC)',
    )
    channel: str = Field(
        default=PushChannel.UMENG_PUSH.value,
        max_length=16,
        description="推送通道 (M1 固定 'umeng_push')",
    )


class PushReceiptDetail(SchemaBase):
    """POST /push_receipts 响应载荷."""

    trace_id: str
    channel: str
    received_at: datetime


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


async def record_push_receipt_for_user(
    db: Any,
    *,
    user_id: int,
    trace_id: str,
    received_at_unix_ms: int,
    channel: str,
) -> PushReceipt:
    """业务入口: 落一行 push_receipts; 返回新 ORM 行 (id 已 flush).

    - channel 白名单校验 (M1 只允许 umeng_push)。
    - received_at_unix_ms → UTC aware datetime。
    - hasn_id 由 user_id → HasnHumans 反查 (客户端不传)。
    """
    from backend.common.exception import errors

    if channel not in PUSH_CHANNEL_VALUES:
        raise errors.RequestError(
            msg=f'channel 不支持: {channel!r}; 允许值: {sorted(PUSH_CHANNEL_VALUES)}'
        )

    hasn_id = await _resolve_hasn_id(db, user_id)
    received_at = datetime.fromtimestamp(
        received_at_unix_ms / 1000.0, tz=_py_timezone.utc
    )

    row = PushReceipt(
        trace_id=trace_id,
        hasn_id=hasn_id,
        channel=channel,
        received_at=received_at,
    )
    db.add(row)
    await db.flush()
    return row


@router.post(
    '',
    summary='上报推送到达回执 (移动端 M1)',
    dependencies=[DependsJwtAuth],
)
async def report_push_receipt(
    request: Request,
    payload: ReportPushReceiptRequest,
    db: CurrentSessionTransaction,
) -> ResponseSchemaModel[PushReceiptDetail]:
    """POST /api/v1/app/push_receipts — 客户端回传推送到达回执.

    200: {data: {trace_id, channel, received_at}}
    400: channel 不支持 / received_at_unix_ms 非法 (RequestError)
    401: 未登录
    404: 当前用户尚未开通 HASN 身份
    """
    user_id = request.user.id
    row = await record_push_receipt_for_user(
        db,
        user_id=user_id,
        trace_id=payload.trace_id,
        received_at_unix_ms=payload.received_at_unix_ms,
        channel=payload.channel,
    )
    return response_base.success(
        data=PushReceiptDetail(
            trace_id=row.trace_id,
            channel=row.channel,
            received_at=row.received_at,
        )
    )
