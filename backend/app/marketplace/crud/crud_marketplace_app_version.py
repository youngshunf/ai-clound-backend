from typing import Sequence

from sqlalchemy import Select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy_crud_plus import CRUDPlus

from backend.app.marketplace.model import MarketplaceAppVersion
from backend.app.marketplace.schema.marketplace_app_version import CreateMarketplaceAppVersionParam, UpdateMarketplaceAppVersionParam


class CRUDMarketplaceAppVersion(CRUDPlus[MarketplaceAppVersion]):
    async def get(self, db: AsyncSession, pk: int) -> MarketplaceAppVersion | None:
        """
        获取应用版本

        :param db: 数据库会话
        :param pk: 应用版本 ID
        :return:
        """
        return await self.select_model(db, pk)

    async def get_select(self) -> Select:
        """获取应用版本列表查询表达式"""
        return await self.select_order('id', 'desc')

    async def get_all(self, db: AsyncSession) -> Sequence[MarketplaceAppVersion]:
        """
        获取所有应用版本

        :param db: 数据库会话
        :return:
        """
        return await self.select_models(db)

    async def create(self, db: AsyncSession, obj: CreateMarketplaceAppVersionParam) -> None:
        """
        创建应用版本

        :param db: 数据库会话
        :param obj: 创建应用版本参数
        :return:
        """
        await self.create_model(db, obj)

    async def update(self, db: AsyncSession, pk: int, obj: UpdateMarketplaceAppVersionParam) -> int:
        """
        更新应用版本

        :param db: 数据库会话
        :param pk: 应用版本 ID
        :param obj: 更新 应用版本参数
        :return:
        """
        return await self.update_model(db, pk, obj)

    async def delete(self, db: AsyncSession, pks: list[int]) -> int:
        """
        批量删除应用版本

        :param db: 数据库会话
        :param pks: 应用版本 ID 列表
        :return:
        """
        return await self.delete_model_by_column(db, allow_multiple=True, id__in=pks)

    async def get_by_app_and_version(
        self, db: AsyncSession, app_id: str, version: str
    ) -> MarketplaceAppVersion | None:
        """
        根据应用ID和版本号获取版本

        :param db: 数据库会话
        :param app_id: 应用ID
        :param version: 版本号
        :return:
        """
        return await self.select_model_by_column(db, app_id=app_id, version=version)

    async def get_latest_by_app(
        self, db: AsyncSession, app_id: str
    ) -> MarketplaceAppVersion | None:
        """
        获取应用的最新版本

        :param db: 数据库会话
        :param app_id: 应用ID
        :return:
        """
        return await self.select_model_by_column(db, app_id=app_id, is_latest=True)

    async def get_versions_by_app(
        self, db: AsyncSession, app_id: str
    ) -> Sequence[MarketplaceAppVersion]:
        """
        获取应用的所有版本

        :param db: 数据库会话
        :param app_id: 应用ID
        :return:
        """
        return await self.select_models_by_column(db, app_id=app_id)


marketplace_app_version_dao: CRUDMarketplaceAppVersion = CRUDMarketplaceAppVersion(MarketplaceAppVersion)
