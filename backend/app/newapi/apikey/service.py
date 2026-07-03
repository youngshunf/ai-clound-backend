"""用户 API Key Service"""

from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from backend.app.newapi.apikey.crud import user_api_key_dao
from backend.app.newapi.apikey.enums import ApiKeyStatus
from backend.app.newapi.apikey.model import UserApiKey
from backend.app.newapi.apikey.schema import (
    CreateUserApiKeyParam,
    CreateUserApiKeyResponse,
    GetUserApiKeyDetail,
    GetUserApiKeyList,
    UpdateUserApiKeyParam,
)
from backend.common.exception import errors
from backend.common.log import log
from backend.common.pagination import paging_data
from backend.common.security.encryption import key_encryption
from backend.utils.timezone import timezone


class ApiKeyService:
    """用户 API Key 服务"""

    @staticmethod
    async def get(db: AsyncSession, pk: int) -> UserApiKey:
        """获取 API Key"""
        api_key = await user_api_key_dao.get(db, pk)
        if not api_key:
            raise errors.NotFoundError(msg='API Key 不存在')
        return api_key

    @staticmethod
    async def get_detail(db: AsyncSession, pk: int, user_id: int) -> GetUserApiKeyDetail:
        """获取 API Key 详情"""
        api_key = await user_api_key_dao.get(db, pk)
        if not api_key:
            raise errors.NotFoundError(msg='API Key 不存在')
        if api_key.user_id != user_id:
            raise errors.ForbiddenError(msg='无权访问此 API Key')

        return GetUserApiKeyDetail(
            id=api_key.id,
            user_id=api_key.user_id,
            name=api_key.name,
            key_prefix=api_key.key_prefix,
            status=api_key.status,
            expires_at=api_key.expires_at,
            rate_limit_config_id=api_key.rate_limit_config_id,
            custom_daily_tokens=api_key.custom_daily_tokens,
            custom_monthly_tokens=api_key.custom_monthly_tokens,
            custom_rpm_limit=api_key.custom_rpm_limit,
            allowed_models=api_key.allowed_models,
            metadata=api_key.metadata_,
            last_used_at=api_key.last_used_at,
            created_time=api_key.created_time,
        )

    @staticmethod
    async def get_all_keys(
        db: AsyncSession,
        *,
        user_id: int | None = None,
        name: str | None = None,
        status: str | None = None,
        user_keyword: str | None = None,
    ) -> dict[str, Any]:
        """获取所有 API Keys（管理员）"""
        stmt = await user_api_key_dao.get_list(user_id=user_id, name=name, status=status, user_keyword=user_keyword)
        page_data = await paging_data(db, stmt, unique=False)
        return page_data

    @staticmethod
    async def get_user_keys(db: AsyncSession, user_id: int) -> list[GetUserApiKeyList]:
        """获取用户的所有 API Keys"""
        keys = await user_api_key_dao.get_user_keys(db, user_id)
        return [
            GetUserApiKeyList(
                id=k.id,
                name=k.name,
                key_prefix=k.key_prefix,
                status=k.status,
                expires_at=k.expires_at,
                last_used_at=k.last_used_at,
                created_time=k.created_time,
            )
            for k in keys
        ]

    @staticmethod
    async def create(db: AsyncSession, obj: CreateUserApiKeyParam, user_id: int) -> CreateUserApiKeyResponse:
        """创建 API Key"""
        # rate_limit_config_id 为自由配置列（无外键），网关删除后不再做 RateLimitConfig 校验（§7.3）

        # 生成 API Key
        full_key, display_prefix = key_encryption.generate_api_key()
        key_hash = key_encryption.hash_key(full_key)
        log.info(f"[DEBUG] Creating API Key. user_id={user_id}, hash={key_hash}, key_prefix={full_key[:15]}...")
        key_encrypted = key_encryption.encrypt(full_key)

        # 创建记录
        api_key = await user_api_key_dao.create(
            db,
            obj,
            user_id=user_id,
            key_prefix=display_prefix,
            key_hash=key_hash,
            key_encrypted=key_encrypted,
        )

        return CreateUserApiKeyResponse(
            id=api_key.id,
            name=api_key.name,
            key_prefix=display_prefix,
            api_key=full_key,  # 仅在创建时返回完整 Key
            expires_at=api_key.expires_at,
        )

    @staticmethod
    async def update(db: AsyncSession, pk: int, obj: UpdateUserApiKeyParam, user_id: int, is_admin: bool = False) -> int:
        """更新 API Key"""
        api_key = await user_api_key_dao.get(db, pk)
        if not api_key:
            raise errors.NotFoundError(msg='API Key 不存在')
        # 非管理员只能修改自己的 API Key
        if not is_admin and api_key.user_id != user_id:
            raise errors.ForbiddenError(msg='无权修改此 API Key')

        # rate_limit_config_id 为自由配置列（无外键），网关删除后不再做 RateLimitConfig 校验（§7.3）

        return await user_api_key_dao.update(db, pk, obj)

    @staticmethod
    async def delete(db: AsyncSession, pk: int, user_id: int, is_admin: bool = False) -> int:
        """删除 API Key"""
        api_key = await user_api_key_dao.get(db, pk)
        if not api_key:
            raise errors.NotFoundError(msg='API Key 不存在')
        # 非管理员只能删除自己的 API Key
        if not is_admin and api_key.user_id != user_id:
            raise errors.ForbiddenError(msg='无权删除此 API Key')
        return await user_api_key_dao.delete(db, pk)

    @staticmethod
    async def verify_api_key(db: AsyncSession, api_key: str) -> UserApiKey:
        """
        验证 API Key

        :param db: 数据库会话
        :param api_key: API Key
        :return: API Key 记录
        :raises: 验证失败时抛出异常
        """
        # 计算哈希
        key_hash = key_encryption.hash_key(api_key)

        # 查找记录
        record = await user_api_key_dao.get_by_hash(db, key_hash)
        if not record:
            log.warning(f"[DEBUG] verify_api_key failed. hash={key_hash}, key_prefix={api_key[:15]}...")
            raise errors.AuthorizationError(msg='Invalid API Key')

        # 检查状态
        if record.status != ApiKeyStatus.ACTIVE:
            raise errors.AuthorizationError(msg=f'API Key is {record.status.lower()}')

        # 检查过期
        if record.expires_at and record.expires_at < timezone.now():
            # 更新状态为过期
            await user_api_key_dao.update(db, record.id, UpdateUserApiKeyParam(status=ApiKeyStatus.EXPIRED))
            raise errors.AuthorizationError(msg='API Key has expired, please upgrade your subscription to continue using the service / API Key 已过期，请升级订阅以继续使用服务')

        # 更新最后使用时间
        await user_api_key_dao.update_last_used(db, record.id)

        return record

    @staticmethod
    async def create_default_key(db: AsyncSession, user_id: int, *, is_free_user: bool = True) -> UserApiKey:
        """
        为用户创建默认 API Key

        :param db: 数据库会话
        :param user_id: 用户 ID
        :param is_free_user: 是否为免费用户，免费用户的 Key 有效期为 7 天
        :return: 创建的 API Key 记录
        """
        from datetime import timedelta

        # 生成 API Key
        full_key, display_prefix = key_encryption.generate_api_key()
        key_hash = key_encryption.hash_key(full_key)
        key_encrypted = key_encryption.encrypt(full_key)

        # 免费用户设置 7 天过期
        expires_at = timezone.now() + timedelta(days=7) if is_free_user else None

        # 创建默认 Key 参数
        obj = CreateUserApiKeyParam(name='Default Key', expires_at=expires_at)

        # 创建记录
        api_key = await user_api_key_dao.create(
            db,
            obj,
            user_id=user_id,
            key_prefix=display_prefix,
            key_hash=key_hash,
            key_encrypted=key_encrypted,
        )

        # 保存完整 Key 到临时属性（用于返回给用户）
        api_key._decrypted_key = full_key

        return api_key

    @staticmethod
    async def get_default_key(db: AsyncSession, user_id: int) -> UserApiKey | None:
        """
        获取用户的默认 API Key

        :param db: 数据库会话
        :param user_id: 用户 ID
        :return: API Key 记录，如果不存在返回 None
        """
        keys = await user_api_key_dao.get_user_keys(db, user_id)
        if not keys:
            return None

        # 返回第一个有效的 Key
        for key in keys:
            if key.status == ApiKeyStatus.ACTIVE:
                try:
                    key._decrypted_key = key_encryption.decrypt(key.key_encrypted)
                except Exception:
                    # 加密密钥变更导致无法解密，标记为失效
                    log.warning(f'API Key id={key.id} 解密失败，可能是加密密钥变更，跳过该 Key')
                    key.status = ApiKeyStatus.REVOKED
                    continue
                return key

        return None

    @staticmethod
    async def get_or_create_default_key(db: AsyncSession, user_id: int) -> UserApiKey:
        """
        获取或创建用户的默认 API Key

        :param db: 数据库会话
        :param user_id: 用户 ID
        :return: API Key 记录
        """
        # 尝试获取现有 Key
        existing_key = await ApiKeyService.get_default_key(db, user_id)
        if existing_key:
            return existing_key

        # 创建新 Key
        return await ApiKeyService.create_default_key(db, user_id)

    @staticmethod
    async def get_rate_limits(db: AsyncSession, api_key: UserApiKey) -> dict:
        """
        获取 API Key 的速率限制配置

        :param db: 数据库会话
        :param api_key: API Key 记录
        :return: 速率限制配置
        """
        # 默认限制
        default_limits = {
            'rpm_limit': 60,
            'daily_token_limit': 30000000,      # 日限额：3000万 tokens
            'monthly_token_limit': 1000000000,  # 月限额：10亿 tokens
        }

        # 如果有自定义限制，使用自定义限制
        if api_key.custom_rpm_limit:
            default_limits['rpm_limit'] = api_key.custom_rpm_limit
        if api_key.custom_daily_tokens:
            default_limits['daily_token_limit'] = api_key.custom_daily_tokens
        if api_key.custom_monthly_tokens:
            default_limits['monthly_token_limit'] = api_key.custom_monthly_tokens

        # RateLimitConfig 表随自建 LLM 网关删除（2026-06-15，§7.3）：rate_limit_config_id 已是
        # 无外键的自由配置列，不再做配置查找；每 Key 限额由上面的 custom_* 字段直接提供。

        return default_limits


api_key_service = ApiKeyService()
