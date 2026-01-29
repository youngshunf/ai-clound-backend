from typing import Sequence

from sqlalchemy import Select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy_crud_plus import CRUDPlus

from backend.app.marketplace.model import MarketplaceSkillVersion
from backend.app.marketplace.schema.marketplace_skill_version import CreateMarketplaceSkillVersionParam, UpdateMarketplaceSkillVersionParam


class CRUDMarketplaceSkillVersion(CRUDPlus[MarketplaceSkillVersion]):
    async def get(self, db: AsyncSession, pk: int) -> MarketplaceSkillVersion | None:
        """
        获取技能版本

        :param db: 数据库会话
        :param pk: 技能版本 ID
        :return:
        """
        return await self.select_model(db, pk)

    async def get_select(self) -> Select:
        """获取技能版本列表查询表达式"""
        return await self.select_order('id', 'desc')

    async def get_all(self, db: AsyncSession) -> Sequence[MarketplaceSkillVersion]:
        """
        获取所有技能版本

        :param db: 数据库会话
        :return:
        """
        return await self.select_models(db)

    async def create(self, db: AsyncSession, obj: CreateMarketplaceSkillVersionParam) -> None:
        """
        创建技能版本

        :param db: 数据库会话
        :param obj: 创建技能版本参数
        :return:
        """
        await self.create_model(db, obj)

    async def update(self, db: AsyncSession, pk: int, obj: UpdateMarketplaceSkillVersionParam) -> int:
        """
        更新技能版本

        :param db: 数据库会话
        :param pk: 技能版本 ID
        :param obj: 更新 技能版本参数
        :return:
        """
        return await self.update_model(db, pk, obj)

    async def delete(self, db: AsyncSession, pks: list[int]) -> int:
        """
        批量删除技能版本

        :param db: 数据库会话
        :param pks: 技能版本 ID 列表
        :return:
        """
        return await self.delete_model_by_column(db, allow_multiple=True, id__in=pks)

    async def get_by_skill_and_version(
        self, db: AsyncSession, skill_id: str, version: str
    ) -> MarketplaceSkillVersion | None:
        """
        根据技能ID和版本号获取版本

        :param db: 数据库会话
        :param skill_id: 技能ID
        :param version: 版本号
        :return:
        """
        return await self.select_model_by_column(db, skill_id=skill_id, version=version)

    async def get_latest_by_skill(
        self, db: AsyncSession, skill_id: str
    ) -> MarketplaceSkillVersion | None:
        """
        获取技能的最新版本

        :param db: 数据库会话
        :param skill_id: 技能ID
        :return:
        """
        return await self.select_model_by_column(db, skill_id=skill_id, is_latest=True)

    async def get_versions_by_skill(
        self, db: AsyncSession, skill_id: str
    ) -> Sequence[MarketplaceSkillVersion]:
        """
        获取技能的所有版本

        :param db: 数据库会话
        :param skill_id: 技能ID
        :return:
        """
        return await self.select_models_by_column(db, skill_id=skill_id)


marketplace_skill_version_dao: CRUDMarketplaceSkillVersion = CRUDMarketplaceSkillVersion(MarketplaceSkillVersion)
