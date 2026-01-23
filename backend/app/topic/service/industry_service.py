from collections.abc import Sequence
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.app.topic.model.industry import Industry
from backend.app.topic.schema.topic import CreateIndustryParam, UpdateIndustryParam
from backend.common.exception import errors
from backend.utils.build_tree import get_tree_data


class IndustryService:
    @staticmethod
    async def get_industry_tree(db: AsyncSession) -> list[dict[str, Any]]:
        """获取行业树形结构"""
        stmt = select(Industry).order_by(Industry.sort)
        result = await db.execute(stmt)
        return get_tree_data(result.scalars().all())

    @staticmethod
    async def get_all_active_industries(db: AsyncSession) -> Sequence[Industry]:
        """获取所有子行业（用于爬虫任务）"""
        stmt = select(Industry).where(Industry.parent_id.is_not(None))
        result = await db.execute(stmt)
        return result.scalars().all()

    @staticmethod
    async def get(db: AsyncSession, id: int) -> Industry | None:
        """获取单个行业"""
        return await db.get(Industry, id)

    @staticmethod
    async def create(db: AsyncSession, obj: CreateIndustryParam) -> Industry:
        """创建行业"""
        industry = Industry(**obj.model_dump())
        db.add(industry)
        await db.commit()
        await db.refresh(industry)
        return industry

    @staticmethod
    async def update(db: AsyncSession, id: int, obj: UpdateIndustryParam) -> Industry:
        """更新行业"""
        industry = await IndustryService.get(db, id)
        if not industry:
            raise errors.NotFoundError(msg='行业不存在')

        update_data = obj.model_dump(exclude_unset=True)
        for key, value in update_data.items():
            setattr(industry, key, value)

        await db.commit()
        await db.refresh(industry)
        return industry

    @staticmethod
    async def delete(db: AsyncSession, id: int) -> None:
        """删除行业"""
        industry = await IndustryService.get(db, id)
        if not industry:
            raise errors.NotFoundError(msg='行业不存在')

        # 检查是否有子行业
        stmt = select(Industry).where(Industry.parent_id == id)
        result = await db.execute(stmt)
        if result.scalars().first():
            raise errors.ForbiddenError(msg='存在子行业，无法删除')

        await db.delete(industry)
        await db.commit()
