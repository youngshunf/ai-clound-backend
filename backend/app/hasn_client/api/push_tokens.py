"""M1 移动端 App - POST / DELETE /api/v1/app/push_tokens.

依赖规范: docs/架构设计/移动端/04-推送触达与后台运行模型详细设计.md §5.5 / §8.2。

- POST   /api/v1/app/push_tokens              注册/更新 (upsert 同一 (hasn_id, device_id, channel))
- DELETE /api/v1/app/push_tokens/{device_id}  登出清理 (级联该 device_id 下全部 channel)

实现策略 (M1 最小可用):
- JWT 依赖: `DependsJwtAuth` (由中间件 + 依赖共同放行)。
- hasn_id 解析: 通过 `HasnHumans.user_id == request.user.id` 反查 (owner_api_keys 同一模式)。
- 无 hasn_humans 记录 → 404 (尚未开通 HASN 身份)。
- channel 入参白名单: 只允许 `PUSH_CHANNEL_VALUES` (M1 固定 'umeng_push', 见 B3)。
- 存在同键记录 → 更新 token + last_seen_at; 否则 INSERT。
- DELETE 按 (hasn_id, device_id) 级联清空所有 channel (规范 §5.5: 登出清理整台设备)。

测试解耦: 端点里的业务函数 (`upsert_push_token_for_user` /
`delete_push_tokens_by_device_for_user`) 可通过 monkeypatch 替换, 与
test_owner_api_keys.py / test_logout.py 的解耦模式一致, 避免引入 aiosqlite。
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime  # noqa: TC003 — Pydantic runtime field annotation
from typing import Annotated, Any

from fastapi import APIRouter, Path, Request
from pydantic import Field
from sqlalchemy import delete, select

from backend.app.hasn_client.model.push_token import PUSH_CHANNEL_VALUES, PushChannel, PushToken
from backend.common.response.response_schema import (
    ResponseModel,
    ResponseSchemaModel,
    response_base,
)
from backend.common.schema import SchemaBase
from backend.common.security.jwt import DependsJwtAuth
from backend.database.db import CurrentSessionTransaction  # noqa: TC001 — FastAPI runtime Depends annotation

router = APIRouter()


class RegisterPushTokenRequest(SchemaBase):
    """POST /push_tokens 请求体."""

    device_id: str = Field(min_length=1, max_length=64, description='唯一设备标识')
    channel: str = Field(
        default=PushChannel.UMENG_PUSH.value,
        max_length=16,
        description="推送通道 (M1 固定 'umeng_push')",
    )
    token: str = Field(min_length=1, max_length=512, description='通道 push token')


class PushTokenDetail(SchemaBase):
    """POST /push_tokens 响应载荷."""

    device_id: str
    channel: str
    registered_at: datetime
    last_seen_at: datetime


@dataclass(frozen=True)
class _UpsertResult:
    device_id: str
    channel: str
    registered_at: datetime
    last_seen_at: datetime


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


async def upsert_push_token_for_user(
    db: Any,
    user_id: int,
    device_id: str,
    channel: str,
    token: str,
) -> _UpsertResult:
    """业务入口: upsert (hasn_id, device_id, channel) → token."""
    from backend.common.exception import errors
    from backend.utils.timezone import timezone as _tz

    if channel not in PUSH_CHANNEL_VALUES:
        raise errors.RequestError(
            msg=f'channel 不支持: {channel!r}; 允许值: {sorted(PUSH_CHANNEL_VALUES)}'
        )

    hasn_id = await _resolve_hasn_id(db, user_id)

    result = await db.execute(
        select(PushToken)
        .where(PushToken.hasn_id == hasn_id)
        .where(PushToken.device_id == device_id)
        .where(PushToken.channel == channel)
        .limit(1)
    )
    existing = result.scalars().first()
    now = _tz.now()

    if existing is not None:
        existing.token = token
        existing.last_seen_at = now
        await db.flush()
        return _UpsertResult(
            device_id=existing.device_id,
            channel=existing.channel,
            registered_at=existing.registered_at,
            last_seen_at=existing.last_seen_at,
        )

    new_row = PushToken(
        hasn_id=hasn_id,
        device_id=device_id,
        channel=channel,
        token=token,
        registered_at=now,
        last_seen_at=now,
    )
    db.add(new_row)
    await db.flush()
    return _UpsertResult(
        device_id=new_row.device_id,
        channel=new_row.channel,
        registered_at=new_row.registered_at,
        last_seen_at=new_row.last_seen_at,
    )


async def delete_push_tokens_by_device_for_user(
    db: Any, user_id: int, device_id: str,
) -> int:
    """业务入口: 删除当前用户 hasn_id 下该 device_id 的全部 channel token."""
    hasn_id = await _resolve_hasn_id(db, user_id)
    result = await db.execute(
        delete(PushToken)
        .where(PushToken.hasn_id == hasn_id)
        .where(PushToken.device_id == device_id)
    )
    return result.rowcount or 0


@router.post(
    '',
    summary='注册/更新推送 Token (移动端 M1)',
    dependencies=[DependsJwtAuth],
)
async def register_push_token(
    request: Request,
    payload: RegisterPushTokenRequest,
    db: CurrentSessionTransaction,
) -> ResponseSchemaModel[PushTokenDetail]:
    """POST /api/v1/app/push_tokens — upsert 推送 Token.

    200: {data: {device_id, channel, registered_at, last_seen_at}}
    400: channel 不支持 (RequestError)
    401: 未登录
    404: 当前用户尚未开通 HASN 身份
    """
    user_id = request.user.id
    result = await upsert_push_token_for_user(
        db,
        user_id=user_id,
        device_id=payload.device_id,
        channel=payload.channel,
        token=payload.token,
    )
    return response_base.success(
        data=PushTokenDetail(
            device_id=result.device_id,
            channel=result.channel,
            registered_at=result.registered_at,
            last_seen_at=result.last_seen_at,
        )
    )


@router.delete(
    '/{device_id}',
    summary='登出: 移除该设备全部 channel 的推送 Token (移动端 M1)',
    dependencies=[DependsJwtAuth],
)
async def delete_push_tokens(
    request: Request,
    db: CurrentSessionTransaction,
    device_id: Annotated[str, Path(min_length=1, max_length=64, description='设备 device_id')],
) -> ResponseModel:
    """DELETE /api/v1/app/push_tokens/{device_id} — 登出清理.

    200: {code: 200} (幂等; 未命中也返回 200)
    401: 未登录
    404: 当前用户尚未开通 HASN 身份
    """
    user_id = request.user.id
    await delete_push_tokens_by_device_for_user(
        db, user_id=user_id, device_id=device_id
    )
    return response_base.success()
