"""模型供应商 Service"""

from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from backend.app.llm.core.encryption import key_encryption
from backend.app.llm.crud.crud_provider import provider_dao
from backend.app.llm.model.provider import ModelProvider
from backend.app.llm.schema.provider import (
    CreateProviderParam,
    GetProviderDetail,
    GetProviderList,
    UpdateProviderParam,
)
from backend.common.exception import errors
from backend.common.pagination import paging_data


class ProviderService:
    """模型供应商服务"""

    @staticmethod
    async def get(db: AsyncSession, pk: int) -> ModelProvider:
        """获取供应商详情"""
        provider = await provider_dao.get(db, pk)
        if not provider:
            raise errors.NotFoundError(msg='供应商不存在')
        return provider

    @staticmethod
    async def get_detail(db: AsyncSession, pk: int) -> GetProviderDetail:
        """获取供应商详情（带 API Key 状态）"""
        provider = await provider_dao.get(db, pk)
        if not provider:
            raise errors.NotFoundError(msg='供应商不存在')
        return GetProviderDetail.from_orm_with_key_check(provider)

    @staticmethod
    async def get_list(
        db: AsyncSession,
        *,
        name: str | None = None,
        enabled: bool | None = None,
    ) -> dict[str, Any]:
        """获取供应商列表（分页）"""
        stmt = await provider_dao.get_list(name=name, enabled=enabled)
        page_data = await paging_data(db, stmt)
        return page_data

    @staticmethod
    async def get_all_enabled(db: AsyncSession) -> list[ModelProvider]:
        """获取所有启用的供应商"""
        return await provider_dao.get_all_enabled(db)

    @staticmethod
    async def create(db: AsyncSession, obj: CreateProviderParam) -> None:
        """创建供应商"""
        # 检查名称是否已存在
        existing = await provider_dao.get_by_name(db, obj.name)
        if existing:
            raise errors.ForbiddenError(msg='供应商名称已存在')

        # 加密 API Key
        api_key_encrypted = None
        if obj.api_key:
            api_key_encrypted = key_encryption.encrypt(obj.api_key)

        await provider_dao.create(db, obj, api_key_encrypted)

    @staticmethod
    async def update(db: AsyncSession, pk: int, obj: UpdateProviderParam) -> int:
        """更新供应商"""
        provider = await provider_dao.get(db, pk)
        if not provider:
            raise errors.NotFoundError(msg='供应商不存在')

        # 检查名称是否重复
        if obj.name and obj.name != provider.name:
            existing = await provider_dao.get_by_name(db, obj.name)
            if existing:
                raise errors.ForbiddenError(msg='供应商名称已存在')

        # 加密 API Key
        api_key_encrypted = None
        if obj.api_key:
            api_key_encrypted = key_encryption.encrypt(obj.api_key)

        return await provider_dao.update(db, pk, obj, api_key_encrypted)

    @staticmethod
    async def delete(db: AsyncSession, pk: int) -> int:
        """删除供应商"""
        provider = await provider_dao.get(db, pk)
        if not provider:
            raise errors.NotFoundError(msg='供应商不存在')
        return await provider_dao.delete(db, pk)


provider_service = ProviderService()
