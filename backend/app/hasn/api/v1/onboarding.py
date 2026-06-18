"""P0 HASN S2 onboarding endpoints."""
from __future__ import annotations

from fastapi import APIRouter, Request

from backend.app.hasn.schema.hasn_onboarding import (
    HasnTokenRefreshRequest,
    HasnTokenRefreshResponse,
    OnboardingEnsureRequest,
    OnboardingEnsureResponse,
    PhoneSendCodeRequest,
    PhoneSendCodeResponse,
    PhoneVerifyRequest,
    PhoneVerifyResponse,
)
from backend.app.hasn.service.hasn_onboarding_service import hasn_onboarding_service, hasn_phone_auth_service
from backend.common.security.jwt import DependsJwtAuth, create_new_token, get_token, jwt_decode
from backend.core.conf import settings
from backend.database.db import CurrentSessionTransaction

router = APIRouter()


@router.post('/auth/phone/send_code', summary='Send phone login verification code')
async def send_phone_code(request: PhoneSendCodeRequest) -> PhoneSendCodeResponse:
    return await hasn_phone_auth_service.send_code(request)


@router.post('/auth/phone/verify', summary='Verify phone login code and issue HASN session token')
async def verify_phone_code(
    db: CurrentSessionTransaction,
    request: PhoneVerifyRequest,
) -> PhoneVerifyResponse:
    return await hasn_phone_auth_service.verify(db, request)


@router.post('/auth/token/refresh', summary='Refresh HASN access token using refresh_token')
async def refresh_hasn_token(
    db: CurrentSessionTransaction,
    body: HasnTokenRefreshRequest,
) -> HasnTokenRefreshResponse:
    token_payload = jwt_decode(body.refresh_token)
    from backend.app.admin.crud.crud_user import user_dao
    user = await user_dao.get(db, token_payload.id)
    if not user:
        from backend.common.exception import errors
        raise errors.TokenError(msg='Refresh Token 已过期，请重新登录')
    if not user.status:
        from backend.common.exception import errors
        raise errors.AuthorizationError(msg='用户已被锁定')

    new_token = await create_new_token(
        body.refresh_token,
        token_payload.session_uuid,
        user.id,
        multi_login=user.is_multi_login,
        username=user.username,
        nickname=user.nickname,
    )
    return HasnTokenRefreshResponse(
        access_token=new_token.new_access_token,
        expires_in_sec=settings.TOKEN_EXPIRE_SECONDS,
        refresh_token=new_token.new_refresh_token,
        refresh_token_expire_sec=settings.HASN_REFRESH_TOKEN_EXPIRE_SECONDS,
    )


@router.post(
    '/onboarding/ensure',
    summary='Ensure Human, Node, OwnerBinding, default Agent, and bootstrap sync state',
    dependencies=[DependsJwtAuth],
)
async def ensure_onboarding(
    db: CurrentSessionTransaction,
    request_body: OnboardingEnsureRequest,
    request: Request,
) -> OnboardingEnsureResponse:
    token_payload = jwt_decode(get_token(request))
    return await hasn_onboarding_service.ensure(db, token_payload.id, request_body)
