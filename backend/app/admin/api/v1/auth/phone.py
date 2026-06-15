"""手机号认证 API
@author Ysf
"""

import random
import string

from fastapi import APIRouter, Depends, Request, Response
from pyrate_limiter import Duration, Rate

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
from backend.app.newapi.service import llm_newapi_user_mapping_service
from backend.app.openclaw.schema import GatewayConfigCreate
from backend.app.openclaw.service import create_gateway_config
from backend.common.exception import errors
from backend.common.response.response_schema import ResponseSchemaModel, response_base
from backend.common.security.jwt import DependsJwtAuth, create_access_token, create_refresh_token
from backend.common.sms import sms_service
from backend.core.conf import settings
from backend.database.db import CurrentSession, CurrentSessionTransaction
from backend.database.redis import redis_client
from backend.utils.limiter import RateLimiter
from backend.utils.timezone import timezone

try:
    from backend.app.hasn.service.hasn_auth import ensure_hasn_owner_key as _ensure_hasn_owner_key
except ImportError:
    _ensure_hasn_owner_key = None

router = APIRouter()

# 验证码 Redis 前缀
SMS_CODE_PREFIX = 'sms_code'
SMS_CODE_EXPIRE = 1800  # 30 分钟
SMS_RATE_PREFIX = 'sms_rate'
SMS_RATE_EXPIRE = 60  # 每个手机号 60 秒限流


def generate_code(length: int = 6) -> str:
    """生成随机验证码"""
    return ''.join(random.choices(string.digits, k=length))


@router.post(
    '/send-code',
    summary='发送验证码',
    description='发送手机验证码，用于登录或注册',
)
async def send_verification_code(obj: SendCodeParam) -> ResponseSchemaModel[SendCodeResponse]:
    """
    发送手机验证码

    - 每个手机号每分钟最多发送 1 次
    - 验证码有效期 30 分钟
    """
    phone = obj.phone

    # 按手机号限流（60 秒内只能发一次）
    rate_key = f'{SMS_RATE_PREFIX}:{phone}'
    if await redis_client.exists(rate_key):
        ttl = await redis_client.ttl(rate_key)
        raise errors.HTTPError(
            code=429,
            msg=f'发送过于频繁，请 {ttl} 秒后重试',
        )

    # 生成验证码
    code = generate_code()

    # 存储到 Redis
    await redis_client.setex(f'{SMS_CODE_PREFIX}:{phone}', SMS_CODE_EXPIRE, code)

    # 开发环境在控制台输出验证码
    if settings.ENVIRONMENT == 'dev':
        print(f'\n{"=" * 40}')
        print(f'📱 验证码 [{phone}]: {code}')
        print(f'{"=" * 40}\n')

    # 发送短信验证码
    success = await sms_service.send_code(phone, code)
    if not success and settings.ENVIRONMENT != 'dev':
        raise errors.RequestError(msg='验证码发送失败，请稍后重试')

    # 发送成功后设置限流 key（60 秒内同一手机号不可再次发送）
    await redis_client.setex(rate_key, SMS_RATE_EXPIRE, '1')

    return response_base.success(data=SendCodeResponse(success=True, message='验证码已发送'))


@router.post(
    '/phone-login',
    summary='手机号登录',
    description='使用手机号和验证码登录，新用户自动注册',
    # 登录限流通过 Redis 在业务层控制
    dependencies=[Depends(RateLimiter(Rate(5, Duration.MINUTE)))],
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
        username = phone  # 使用手机号作为用户名
        nickname = f'{phone[:3]}****{phone[-4:]}'

        # 检查用户名是否已存在
        existing = await user_dao.get_by_username(db, username)
        if existing:
            username = f'{phone}_{generate_code(4)}'

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

        # 自动创建 new-api 用户 + 永不过期的 API Key（新用户赠送积分）
        from backend.app.newapi.service import credits_to_quota
        bonus_quota = credits_to_quota(settings.NEWAPI_REGISTER_BONUS_CREDITS)
        mapping = await llm_newapi_user_mapping_service.ensure_newapi_user(
            db, user.id,
            username=phone,
            nickname=nickname,
            initial_quota=bonus_quota,
        )

        # 初始化订阅和赠送积分
        from backend.app.billing.service.credit_service import credit_service
        await credit_service.get_or_create_subscription(db, user.id)

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

    # 获取 LLM Token（通过 new-api）
    try:
        newapi_username = user.phone or user.username
        mapping = await llm_newapi_user_mapping_service.ensure_newapi_user(
            db, user.id,
            username=newapi_username,
            nickname=user.nickname or '',
        )
        llm_token = f'sk-{mapping.newapi_token_key}'
    except Exception as e:
        from backend.common.log import log
        log.error(f'new-api 用户创建失败: {e}')
        raise errors.ServerError(msg='LLM 服务初始化失败，请稍后重试')

    # 获取或创建 Gateway Token
    gateway_token_response = await create_gateway_config(
        db,
        user_id=user.id,
        data=GatewayConfigCreate(openclaw_config=None),
        auto_commit=False,
    )
    gateway_token = gateway_token_response.gateway_token

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

    # 历史遗留字段，现在 NodeKey 由客户端主动获取
    hasn_node_key = None
    hasn_node_id = None

    # 自动签发 Owner API Key（hasn_ok_xxx）用于文档/云函数等用户级认证
    owner_key = None
    if _ensure_hasn_owner_key is not None:
        owner_key = await _ensure_hasn_owner_key(
            db=db,
            user_id=user.id,
            nickname=user.nickname or '唤星用户',
        )

    return response_base.success(
        data=PhoneLoginResponse(
            access_token=access_token_data.access_token,
            access_token_expire_time=access_token_data.access_token_expire_time,
            refresh_token=refresh_token_data.refresh_token,
            refresh_token_expire_time=refresh_token_data.refresh_token_expire_time,
            llm_token=llm_token,
            llm_base_url=settings.LLM_API_BASE_URL,
            agent_key=settings.AGENT_SECRET_KEY.split(',')[0].strip(),
            gateway_token=gateway_token,
            hasn_node_key=hasn_node_key,
            hasn_node_id=hasn_node_id,
            owner_key=owner_key,
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

    # 获取或创建 API Key（通过 new-api）
    api_key = await llm_newapi_user_mapping_service.get_api_key(db, user_id)

    return response_base.success(
        data=GetLLMTokenResponse(
            api_token=api_key,
            llm_base_url=settings.LLM_API_BASE_URL,
            expires_at=None,  # new-api token 永不过期
        )
    )
