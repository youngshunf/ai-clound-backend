"""用户 API Key Service"""

from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from backend.app.llm.core.encryption import key_encryption
from backend.app.llm.crud.crud_rate_limit import rate_limit_dao
from backend.app.llm.crud.crud_user_api_key import user_api_key_dao
from backend.app.llm.enums import ApiKeyStatus
from backend.app.llm.model.user_api_key import UserApiKey
from backend.app.llm.schema.user_api_key import (
    CreateUserApiKeyParam,
    CreateUserApiKeyResponse,
    GetUserApiKeyDetail,
    GetUserApiKeyList,
    UpdateUserApiKeyParam,
)
from backend.common.exception import errors
from backend.common.pagination import paging_data
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
    ) -> dict[str, Any]:
        """获取所有 API Keys（管理员）"""
        stmt = await user_api_key_dao.get_list(user_id=user_id, name=name, status=status)
        page_data = await paging_data(db, stmt)
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
        # 检查速率限制配置是否存在
        if obj.rate_limit_config_id:
            config = await rate_limit_dao.get(db, obj.rate_limit_config_id)
            if not config:
                raise errors.NotFoundError(msg='速率限制配置不存在')

        # 生成 API Key
        full_key, display_prefix = key_encryption.generate_api_key()
        key_hash = key_encryption.hash_key(full_key)
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
    async def update(db: AsyncSession, pk: int, obj: UpdateUserApiKeyParam, user_id: int) -> int:
        """更新 API Key"""
        api_key = await user_api_key_dao.get(db, pk)
        if not api_key:
            raise errors.NotFoundError(msg='API Key 不存在')
        if api_key.user_id != user_id:
            raise errors.ForbiddenError(msg='无权修改此 API Key')

        # 检查速率限制配置是否存在
        if obj.rate_limit_config_id:
            config = await rate_limit_dao.get(db, obj.rate_limit_config_id)
            if not config:
                raise errors.NotFoundError(msg='速率限制配置不存在')

        return await user_api_key_dao.update(db, pk, obj)

    @staticmethod
    async def delete(db: AsyncSession, pk: int, user_id: int) -> int:
        """删除 API Key"""
        api_key = await user_api_key_dao.get(db, pk)
        if not api_key:
            raise errors.NotFoundError(msg='API Key 不存在')
        if api_key.user_id != user_id:
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
            raise errors.AuthorizationError(msg='Invalid API Key')

        # 检查状态
        if record.status != ApiKeyStatus.ACTIVE:
            raise errors.AuthorizationError(msg=f'API Key is {record.status.lower()}')

        # 检查过期
        if record.expires_at and record.expires_at < timezone.now():
            # 更新状态为过期
            await user_api_key_dao.update(db, record.id, UpdateUserApiKeyParam(status=ApiKeyStatus.EXPIRED))
            raise errors.AuthorizationError(msg='API Key has expired')

        # 更新最后使用时间
        await user_api_key_dao.update_last_used(db, record.id)

        return record

    @staticmethod
    async def create_default_key(db: AsyncSession, user_id: int) -> UserApiKey:
        """
        为用户创建默认 API Key

        :param db: 数据库会话
        :param user_id: 用户 ID
        :return: 创建的 API Key 记录
        """
        # 生成 API Key
        full_key, display_prefix = key_encryption.generate_api_key()
        key_hash = key_encryption.hash_key(full_key)
        key_encrypted = key_encryption.encrypt(full_key)

        # 创建默认 Key 参数
        obj = CreateUserApiKeyParam(name='Default Key')

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
                # 解密 Key
                key._decrypted_key = key_encryption.decrypt(key.key_encrypted)
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
            'daily_token_limit': 1000000,
            'monthly_token_limit': 10000000,
        }

        # 如果有自定义限制，使用自定义限制
        if api_key.custom_rpm_limit:
            default_limits['rpm_limit'] = api_key.custom_rpm_limit
        if api_key.custom_daily_tokens:
            default_limits['daily_token_limit'] = api_key.custom_daily_tokens
        if api_key.custom_monthly_tokens:
            default_limits['monthly_token_limit'] = api_key.custom_monthly_tokens

        # 如果有速率限制配置，使用配置
        if api_key.rate_limit_config_id:
            config = await rate_limit_dao.get(db, api_key.rate_limit_config_id)
            if config and config.enabled:
                default_limits['rpm_limit'] = config.rpm_limit
                default_limits['daily_token_limit'] = config.daily_token_limit
                default_limits['monthly_token_limit'] = config.monthly_token_limit

        return default_limits


api_key_service = ApiKeyService()
