from typing import Annotated

from fastapi import APIRouter, Depends, Request, Response
from fastapi.security import HTTPBasicCredentials
from pyrate_limiter import Duration, Rate
from starlette.background import BackgroundTasks

from backend.app.admin.schema.phone_auth import GetLLMTokenResponse
from backend.app.admin.schema.token import GetLoginToken, GetNewToken, GetSwaggerToken, RefreshTokenParam
from backend.app.admin.schema.user import AuthLoginParam
from backend.app.admin.service.auth_service import auth_service
from backend.app.newapi.service import llm_newapi_user_mapping_service
from backend.common.response.response_schema import ResponseModel, ResponseSchemaModel, response_base
from backend.common.security.jwt import DependsJwtAuth
from backend.core.conf import settings
from backend.database.db import CurrentSession, CurrentSessionTransaction
from backend.utils.limiter import RateLimiter

router = APIRouter()


@router.post('/login/swagger', summary='swagger 调试专用', description='用于快捷获取 token 进行 swagger 认证')
async def login_swagger(
    db: CurrentSessionTransaction, obj: Annotated[HTTPBasicCredentials, Depends()]
) -> GetSwaggerToken:
    token, user = await auth_service.swagger_login(db=db, obj=obj)
    return GetSwaggerToken(access_token=token, user=user)  # type: ignore


@router.post(
    '/login',
    summary='用户登录',
    description='json 格式登录, 仅支持在第三方api工具调试, 例如: postman',
    dependencies=[Depends(RateLimiter(Rate(5, Duration.MINUTE)))],
)
async def login(
    db: CurrentSessionTransaction,
    response: Response,
    obj: AuthLoginParam,
    background_tasks: BackgroundTasks,
) -> ResponseSchemaModel[GetLoginToken]:
    data = await auth_service.login(db=db, response=response, obj=obj, background_tasks=background_tasks)
    return response_base.success(data=data)


@router.get('/codes', summary='获取所有授权码', description='适配 vben admin v5', dependencies=[DependsJwtAuth])
async def get_codes(db: CurrentSession, request: Request) -> ResponseSchemaModel[list[str]]:
    codes = await auth_service.get_codes(db=db, request=request)
    return response_base.success(data=codes)


@router.post('/refresh', summary='刷新 token')
async def refresh_token(
    db: CurrentSession,
    request: Request,
    response: Response,
    obj: RefreshTokenParam | None = None,
) -> ResponseSchemaModel[GetNewToken]:
    data = await auth_service.refresh_token(db=db, request=request, response=response, body_refresh_token=obj.refresh_token if obj else None)
    return response_base.success(data=data)


@router.post('/logout', summary='用户登出')
async def logout(request: Request, response: Response) -> ResponseModel:
    await auth_service.logout(request=request, response=response)
    return response_base.success()


@router.get('/llm-config', summary='获取 LLM 配置', description='获取当前用户的 LLM Token 和 API Base URL', dependencies=[DependsJwtAuth])
async def get_llm_config(db: CurrentSession, request: Request) -> ResponseSchemaModel[GetLLMTokenResponse]:
    """
    获取 LLM 配置

    - 需要 JWT 认证
    - 如果用户没有 API Key，自动创建
    - 返回 LLM Token 和 Base URL 供桌面端使用
    """
    from backend.common.security.jwt import get_token, jwt_decode

    token = get_token(request)
    payload = jwt_decode(token)
    api_key = await llm_newapi_user_mapping_service.get_api_key(db, payload.id)

    return response_base.success(
        data=GetLLMTokenResponse(
            api_token=api_key,
            llm_base_url=settings.LLM_API_BASE_URL,
            expires_at=None,
        )
    )
