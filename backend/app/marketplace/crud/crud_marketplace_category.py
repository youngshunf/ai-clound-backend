from typing import Sequence

from sqlalchemy import Select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy_crud_plus import CRUDPlus

from backend.app.marketplace.model import MarketplaceCategory
from backend.app.marketplace.schema.marketplace_category import CreateMarketplaceCategoryParam, UpdateMarketplaceCategoryParam


class CRUDMarketplaceCategory(CRUDPlus[MarketplaceCategory]):
    async def get(self, db: AsyncSession, pk: int) -> MarketplaceCategory | None:
        """
        获取技能市场分类

        :param db: 数据库会话
        :param pk: 技能市场分类 ID
        :return:
        """
        return await self.select_model(db, pk)

    async def get_select(self) -> Select:
        """获取技能市场分类列表查询表达式"""
        return await self.select_order('id', 'desc')

    async def get_all(self, db: AsyncSession) -> Sequence[MarketplaceCategory]:
        """
        获取所有技能市场分类

        :param db: 数据库会话
        :return:
        """
        return await self.select_models(db)

    async def create(self, db: AsyncSession, obj: CreateMarketplaceCategoryParam) -> None:
        """
        创建技能市场分类

        :param db: 数据库会话
        :param obj: 创建技能市场分类参数
        :return:
        """
        await self.create_model(db, obj)

    async def update(self, db: AsyncSession, pk: int, obj: UpdateMarketplaceCategoryParam) -> int:
        """
        更新技能市场分类

        :param db: 数据库会话
        :param pk: 技能市场分类 ID
        :param obj: 更新 技能市场分类参数
        :return:
        """
        return await self.update_model(db, pk, obj)

    async def delete(self, db: AsyncSession, pks: list[int]) -> int:
        """
        批量删除技能市场分类

        :param db: 数据库会话
        :param pks: 技能市场分类 ID 列表
        :return:
        """
        return await self.delete_model_by_column(db, allow_multiple=True, id__in=pks)


marketplace_category_dao: CRUDMarketplaceCategory = CRUDMarketplaceCategory(MarketplaceCategory)
