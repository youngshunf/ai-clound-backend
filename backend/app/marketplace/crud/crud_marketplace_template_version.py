from collections.abc import Sequence

from sqlalchemy import Select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy_crud_plus import CRUDPlus

from backend.app.marketplace.model import MarketplaceTemplateVersion
from backend.app.marketplace.schema.marketplace_template_version import (
    CreateMarketplaceTemplateVersionParam,
    UpdateMarketplaceTemplateVersionParam,
)


class CRUDMarketplaceTemplateVersion(CRUDPlus[MarketplaceTemplateVersion]):
    async def get(self, db: AsyncSession, pk: int) -> MarketplaceTemplateVersion | None:
        """
        获取模板版本

        :param db: 数据库会话
        :param pk: 模板版本 ID
        :return:
        """
        return await self.select_model(db, pk)

    async def get_select(self) -> Select:
        """获取模板版本列表查询表达式"""
        return await self.select_order('id', 'desc')

    async def get_all(self, db: AsyncSession) -> Sequence[MarketplaceTemplateVersion]:
        """
        获取所有模板版本

        :param db: 数据库会话
        :return:
        """
        return await self.select_models(db)

    async def create(self, db: AsyncSession, obj: CreateMarketplaceTemplateVersionParam) -> None:
        """
        创建模板版本

        :param db: 数据库会话
        :param obj: 创建模板版本参数
        :return:
        """
        await self.create_model(db, obj)

    async def update(self, db: AsyncSession, pk: int, obj: UpdateMarketplaceTemplateVersionParam) -> int:
        """
        更新模板版本

        :param db: 数据库会话
        :param pk: 模板版本 ID
        :param obj: 更新 模板版本参数
        :return:
        """
        return await self.update_model(db, pk, obj)

    async def delete(self, db: AsyncSession, pks: list[int]) -> int:
        """
        批量删除模板版本

        :param db: 数据库会话
        :param pks: 模板版本 ID 列表
        :return:
        """
        return await self.delete_model_by_column(db, allow_multiple=True, id__in=pks)

    async def get_by_template_and_version(
        self, db: AsyncSession, template_id: str, version: str
    ) -> MarketplaceTemplateVersion | None:
        """
        根据模板 ID 和版本号获取版本

        :param db: 数据库会话
        :param template_id: 模板 ID
        :param version: 版本号
        :return:
        """
        return await self.select_model_by_column(db, template_id=template_id, version=version)

    async def get_latest_by_template(
        self, db: AsyncSession, template_id: str
    ) -> MarketplaceTemplateVersion | None:
        """
        获取模板的最新版本

        :param db: 数据库会话
        :param template_id: 模板 ID
        :return:
        """
        return await self.select_model_by_column(db, template_id=template_id, is_latest=True)

    async def get_by_template(
        self, db: AsyncSession, template_id: str
    ) -> Sequence[MarketplaceTemplateVersion]:
        """
        获取模板的所有版本

        :param db: 数据库会话
        :param template_id: 模板 ID
        :return:
        """
        return await self.select_models(db, template_id=template_id)

    async def mark_all_not_latest(self, db: AsyncSession, template_id: str) -> int:
        """
        将模板的所有版本标记为非最新

        :param db: 数据库会话
        :param template_id: 模板 ID
        :return: 更新的行数
        """
        return await self.update_model_by_column(db, {'is_latest': False}, template_id=template_id)


marketplace_template_version_dao: CRUDMarketplaceTemplateVersion = CRUDMarketplaceTemplateVersion(
    MarketplaceTemplateVersion,
)
