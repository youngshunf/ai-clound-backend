"""速率限制配置 Service

@author Ysf
@date 2025-12-28
"""

from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from backend.app.llm.crud.crud_rate_limit import rate_limit_dao
from backend.app.llm.model.rate_limit import RateLimitConfig
from backend.app.llm.schema.rate_limit import (
    CreateRateLimitConfigParam,
    GetRateLimitConfigDetail,
    GetRateLimitConfigList,
    UpdateRateLimitConfigParam,
)
from backend.common.exception import errors
from backend.common.pagination import paging_data


class RateLimitService:
    """速率限制配置服务"""

    @staticmethod
    async def get(db: AsyncSession, pk: int) -> RateLimitConfig:
        """获取速率限制配置"""
        config = await rate_limit_dao.get(db, pk)
        if not config:
            raise errors.NotFoundError(msg='速率限制配置不存在')
        return config

    @staticmethod
    async def get_detail(db: AsyncSession, pk: int) -> GetRateLimitConfigDetail:
        """获取速率限制配置详情"""
        config = await rate_limit_dao.get(db, pk)
        if not config:
            raise errors.NotFoundError(msg='速率限制配置不存在')
        return GetRateLimitConfigDetail.model_validate(config)

    @staticmethod
    async def get_list(
        db: AsyncSession,
        *,
        name: str | None = None,
        enabled: bool | None = None,
    ) -> dict[str, Any]:
        """获取速率限制配置列表（分页）"""
        stmt = await rate_limit_dao.get_list(name=name, enabled=enabled)
        page_data = await paging_data(db, stmt)
        return page_data

    @staticmethod
    async def create(db: AsyncSession, obj: CreateRateLimitConfigParam) -> None:
        """创建速率限制配置"""
        existing = await rate_limit_dao.get_by_name(db, obj.name)
        if existing:
            raise errors.ForbiddenError(msg='配置名称已存在')
        await rate_limit_dao.create(db, obj)

    @staticmethod
    async def update(db: AsyncSession, pk: int, obj: UpdateRateLimitConfigParam) -> int:
        """更新速率限制配置"""
        config = await rate_limit_dao.get(db, pk)
        if not config:
            raise errors.NotFoundError(msg='速率限制配置不存在')
        if obj.name and obj.name != config.name:
            existing = await rate_limit_dao.get_by_name(db, obj.name)
            if existing:
                raise errors.ForbiddenError(msg='配置名称已存在')
        return await rate_limit_dao.update(db, pk, obj)

    @staticmethod
    async def delete(db: AsyncSession, pk: int) -> int:
        """删除速率限制配置"""
        config = await rate_limit_dao.get(db, pk)
        if not config:
            raise errors.NotFoundError(msg='速率限制配置不存在')
        return await rate_limit_dao.delete(db, pk)


rate_limit_service = RateLimitService()
