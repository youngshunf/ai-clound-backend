from typing import Sequence

from sqlalchemy import Select, update
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy_crud_plus import CRUDPlus

from backend.app.marketplace.model import MarketplaceApp
from backend.app.marketplace.schema.marketplace_app import CreateMarketplaceAppParam, UpdateMarketplaceAppParam


class CRUDMarketplaceApp(CRUDPlus[MarketplaceApp]):
    async def get(self, db: AsyncSession, pk: int) -> MarketplaceApp | None:
        """
        获取技能市场应用

        :param db: 数据库会话
        :param pk: 技能市场应用 ID
        :return:
        """
        return await self.select_model(db, pk)

    async def get_select(self) -> Select:
        """获取技能市场应用列表查询表达式"""
        return await self.select_order('id', 'desc')

    async def get_all(self, db: AsyncSession) -> Sequence[MarketplaceApp]:
        """
        获取所有技能市场应用

        :param db: 数据库会话
        :return:
        """
        return await self.select_models(db)

    async def create(self, db: AsyncSession, obj: CreateMarketplaceAppParam) -> None:
        """
        创建技能市场应用

        :param db: 数据库会话
        :param obj: 创建技能市场应用参数
        :return:
        """
        await self.create_model(db, obj)

    async def update(self, db: AsyncSession, pk: int, obj: UpdateMarketplaceAppParam) -> int:
        """
        更新技能市场应用

        :param db: 数据库会话
        :param pk: 技能市场应用 ID
        :param obj: 更新 技能市场应用参数
        :return:
        """
        return await self.update_model(db, pk, obj)

    async def delete(self, db: AsyncSession, pks: list[int]) -> int:
        """
        批量删除技能市场应用

        :param db: 数据库会话
        :param pks: 技能市场应用 ID 列表
        :return:
        """
        return await self.delete_model_by_column(db, allow_multiple=True, id__in=pks)

    async def get_by_id(self, db: AsyncSession, app_id: str) -> MarketplaceApp | None:
        """
        根据应用ID获取应用

        :param db: 数据库会话
        :param app_id: 应用ID
        :return:
        """
        return await self.select_model_by_column(db, app_id=app_id)

    async def increment_download_count(self, db: AsyncSession, app_id: str) -> None:
        """
        增加应用下载次数

        :param db: 数据库会话
        :param app_id: 应用ID
        """
        stmt = (
            update(MarketplaceApp)
            .where(MarketplaceApp.app_id == app_id)
            .values(download_count=MarketplaceApp.download_count + 1)
        )
        await db.execute(stmt)


marketplace_app_dao: CRUDMarketplaceApp = CRUDMarketplaceApp(MarketplaceApp)
