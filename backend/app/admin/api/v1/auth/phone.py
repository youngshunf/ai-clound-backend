"""手机号认证 API
@author Ysf
"""

import random
import string

from fastapi import APIRouter, Depends, Request, Response
from fastapi_limiter.depends import RateLimiter

from backend.app.admin.crud.crud_user import user_dao
from backend.app.admin.model import User
from backend.app.admin.schema.phone_auth import (
    GetLLMTokenResponse,
    PhoneLoginParam,
    PhoneLoginResponse,
    PhoneLoginUserInfo,
    SendCodeParam,
    SendCodeResponse,
)
from backend.app.llm.service.api_key_service import api_key_service
from backend.common.exception import errors
from backend.common.response.response_schema import ResponseSchemaModel, response_base
from backend.common.security.jwt import DependsJwtAuth, create_access_token, create_refresh_token
from backend.common.sms import sms_service
from backend.core.conf import settings
from backend.database.db import CurrentSession, CurrentSessionTransaction
from backend.database.redis import redis_client
from backend.utils.timezone import timezone

router = APIRouter()

# 验证码 Redis 前缀
SMS_CODE_PREFIX = 'sms_code'
SMS_CODE_EXPIRE = 300  # 5 分钟


def generate_code(length: int = 6) -> str:
    """生成随机验证码"""
    return ''.join(random.choices(string.digits, k=length))


@router.post(
    '/send-code',
    summary='发送验证码',
    description='发送手机验证码，用于登录或注册',
    dependencies=[Depends(RateLimiter(times=1, minutes=1))],  # 每分钟最多发送 1 次
)
async def send_verification_code(obj: SendCodeParam) -> ResponseSchemaModel[SendCodeResponse]:
    """
    发送手机验证码

    - 每分钟最多发送 1 次
    - 验证码有效期 5 分钟
    """
    phone = obj.phone

    # 生成验证码
    code = generate_code()

    # 存储到 Redis
    await redis_client.setex(f'{SMS_CODE_PREFIX}:{phone}', SMS_CODE_EXPIRE, code)

    # 发送短信验证码
    success = await sms_service.send_code(phone, code)
    if not success and settings.ENVIRONMENT != 'dev':
        raise errors.RequestError(msg='验证码发送失败，请稍后重试')

    return response_base.success(data=SendCodeResponse(success=True, message='验证码已发送'))


@router.post(
    '/phone-login',
    summary='手机号登录',
    description='使用手机号和验证码登录，新用户自动注册',
    dependencies=[Depends(RateLimiter(times=5, minutes=1))],  # 每分钟最多尝试 5 次
)
async def phone_login(
    db: CurrentSessionTransaction,
    response: Response,
    obj: PhoneLoginParam,
) -> ResponseSchemaModel[PhoneLoginResponse]:
    """
    手机号登录

    - 验证验证码
    - 如果用户不存在，自动注册
    - 自动创建 API Key
    - 返回 JWT Token + LLM Token
    """
    phone = obj.phone
    code = obj.code

    # 验证验证码
    stored_code = await redis_client.get(f'{SMS_CODE_PREFIX}:{phone}')
    if not stored_code:
        raise errors.RequestError(msg='验证码已过期，请重新获取')
    if stored_code != code:
        raise errors.RequestError(msg='验证码错误')

    # 删除已使用的验证码
    await redis_client.delete(f'{SMS_CODE_PREFIX}:{phone}')

    # 查找或创建用户
    is_new_user = False
    user = await user_dao.select_model_by_column(db, phone=phone)

    if not user:
        # 自动注册新用户
        is_new_user = True
        username = f'user_{phone[-4:]}'  # 使用手机号后4位作为用户名
        nickname = f'用户{phone[-4:]}'

        # 检查用户名是否已存在，如果存在则添加随机后缀
        existing = await user_dao.get_by_username(db, username)
        if existing:
            username = f'{username}_{generate_code(4)}'
            nickname = f'用户{phone[-4:]}_{generate_code(4)}'

        # 创建用户
        user = User(
            username=username,
            nickname=nickname,
            phone=phone,
            password=None,  # 手机号登录用户暂无密码
            salt=None,
        )
        db.add(user)
        await db.flush()
        await db.refresh(user)

        # 自动创建 API Key
        await api_key_service.create_default_key(db, user.id)

    # 更新最后登录时间
    user.last_login_time = timezone.now()
    await db.flush()

    # 生成 JWT Token
    access_token_data = await create_access_token(
        user.id,
        multi_login=user.is_multi_login,
        username=user.username,
        nickname=user.nickname,
        phone=user.phone,
    )

    # 生成 Refresh Token
    refresh_token_data = await create_refresh_token(
        access_token_data.session_uuid,
        user.id,
        multi_login=user.is_multi_login,
    )

    # 设置 Refresh Token Cookie
    response.set_cookie(
        key=settings.COOKIE_REFRESH_TOKEN_KEY,
        value=refresh_token_data.refresh_token,
        max_age=settings.COOKIE_REFRESH_TOKEN_EXPIRE_SECONDS,
        expires=timezone.to_utc(refresh_token_data.refresh_token_expire_time),
        httponly=True,
    )

    # 获取 LLM Token
    api_key = await api_key_service.get_or_create_default_key(db, user.id)
    llm_token = api_key._decrypted_key

    # 构建用户信息
    user_info = PhoneLoginUserInfo(
        uuid=user.uuid,
        username=user.username,
        nickname=user.nickname,
        phone=user.phone,
        email=user.email,
        avatar=user.avatar,
        is_new_user=is_new_user,
    )

    return response_base.success(
        data=PhoneLoginResponse(
            access_token=access_token_data.access_token,
            access_token_expire_time=access_token_data.access_token_expire_time,
            refresh_token=refresh_token_data.refresh_token,
            refresh_token_expire_time=refresh_token_data.refresh_token_expire_time,
            llm_token=llm_token,
            is_new_user=is_new_user,
            user=user_info,
        )
    )


@router.post(
    '/llm-token',
    summary='获取 LLM Token',
    description='获取用户的 LLM API Token，用于调用云端 LLM 网关',
    dependencies=[DependsJwtAuth],
)
async def get_llm_token(
    db: CurrentSession,
    request: Request,
) -> ResponseSchemaModel[GetLLMTokenResponse]:
    """
    获取 LLM API Token

    - 需要 JWT 认证
    - 如果用户没有 API Key，自动创建
    - 返回 API Token 供桌面端使用
    """
    from backend.common.security.jwt import get_token, jwt_decode

    # 获取当前用户 ID
    token = get_token(request)
    payload = jwt_decode(token)
    user_id = payload.id

    # 获取或创建 API Key
    api_key = await api_key_service.get_or_create_default_key(db, user_id)

    return response_base.success(
        data=GetLLMTokenResponse(
            api_token=api_key._decrypted_key,
            expires_at=api_key.expires_at,
        )
    )
