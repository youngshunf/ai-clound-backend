"""M1 移动端 App — POST /api/v1/auth/logout: JWT 吊销端点.

依赖规范: docs/架构设计/移动端/05-凭据与安全详细设计.md §16.1

实现策略 (M1 最小可用):
- 经 JWT 中间件放行, 用 `backend.common.security.jwt.jwt_decode` 解析 Bearer token。
- 将 session_uuid 作为 jti 写入 `jwt_revocations` 表 (幂等)。
- 同时清理 Redis 中的 access / refresh / extra_info (沿用 admin 登出语义, best-effort)。
- 提供 `DependsMobileJwtAuth` 依赖: 解码 + 走吊销表再查一次, 命中 → 401。
  任何需要"登出即失效"语义的移动端端点可在 `dependencies=[DependsMobileJwtAuth]`
  中使用。
"""
from __future__ import annotations

from datetime import datetime
from datetime import timezone as dt_timezone

from fastapi import APIRouter, Depends, HTTPException, Request
from starlette.status import HTTP_401_UNAUTHORIZED

from backend.app.api.v1.auth import jwt_revocation as jwt_revocation_module
from backend.common.exception import errors
from backend.common.response.response_schema import ResponseModel, response_base
from backend.common.security.jwt import DependsJwtAuth, get_token, jwt_decode
from backend.core.conf import settings
from backend.database.db import CurrentSessionTransaction, get_db_transaction

router = APIRouter()


def _coerce_expires_at(token_expire) -> datetime | None:
    """TokenPayload.expire_time 已是 aware datetime; 兜底其他形式返回 None."""
    if token_expire is None:
        return None
    if isinstance(token_expire, datetime):
        return token_expire
    try:
        return datetime.fromtimestamp(float(token_expire), tz=dt_timezone.utc)
    except (TypeError, ValueError):
        return None


@router.post(
    '/logout',
    summary='JWT 吊销 (移动端 M1)',
    name='app_logout',
    dependencies=[DependsJwtAuth],
)
async def logout(
    request: Request,
    db: CurrentSessionTransaction,
) -> ResponseModel:
    """POST /api/v1/auth/logout.

    - 从请求头 Bearer token → `jwt_decode` → 取 session_uuid (作为 jti) + user_id + exp。
    - 调 `revoke_jwt` 写 `jwt_revocations` (幂等)。
    - 清理 Redis access / refresh / extra_info (best-effort, 失败不影响 200)。
    """
    token = get_token(request)
    try:
        payload = jwt_decode(token)
    except errors.TokenError as exc:
        raise HTTPException(status_code=HTTP_401_UNAUTHORIZED, detail=str(exc.msg))

    jti = payload.session_uuid
    user_id = payload.id
    expires_at = _coerce_expires_at(payload.expire_time)

    await jwt_revocation_module.revoke_jwt(
        db,
        jti=jti,
        user_id=user_id,
        expires_at=expires_at,
    )

    # best-effort Redis 清理 (与 admin 登出语义对齐)
    try:
        from backend.database.redis import redis_client

        await redis_client.delete(f'{settings.TOKEN_REDIS_PREFIX}:{user_id}:{jti}')
        await redis_client.delete(f'{settings.TOKEN_EXTRA_INFO_REDIS_PREFIX}:{user_id}:{jti}')
        await redis_client.delete(f'{settings.TOKEN_REFRESH_REDIS_PREFIX}:{user_id}:{jti}')
    except Exception:
        # Redis 不可用不应阻塞 logout (吊销记录已落库)
        pass

    return response_base.success()


async def mobile_jwt_auth_with_revocation(
    request: Request,
    db=Depends(get_db_transaction),
    _: str = DependsJwtAuth,
) -> None:
    """JWT 认证 + 吊销表二次核对.

    放在受保护端点 `dependencies=[Depends(mobile_jwt_auth_with_revocation)]` 中:
    - 无 / 无效 Bearer → 401 (DependsJwtAuth 先兜)
    - Bearer 本身合法但 jti 已吊销 → 401
    """
    try:
        token = get_token(request)
        payload = jwt_decode(token)
    except errors.TokenError as exc:
        raise HTTPException(status_code=HTTP_401_UNAUTHORIZED, detail=str(exc.msg))

    if await jwt_revocation_module.is_jwt_revoked(db, jti=payload.session_uuid):
        raise HTTPException(status_code=HTTP_401_UNAUTHORIZED, detail='Token 已吊销')


DependsMobileJwtAuth = Depends(mobile_jwt_auth_with_revocation)
