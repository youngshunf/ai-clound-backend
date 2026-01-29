from typing import Sequence

from sqlalchemy import Select, update
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy_crud_plus import CRUDPlus

from backend.app.marketplace.model import MarketplaceSkill
from backend.app.marketplace.schema.marketplace_skill import CreateMarketplaceSkillParam, UpdateMarketplaceSkillParam


class CRUDMarketplaceSkill(CRUDPlus[MarketplaceSkill]):
    async def get(self, db: AsyncSession, pk: int) -> MarketplaceSkill | None:
        """
        获取技能市场技能

        :param db: 数据库会话
        :param pk: 技能市场技能 ID
        :return:
        """
        return await self.select_model(db, pk)

    async def get_select(self) -> Select:
        """获取技能市场技能列表查询表达式"""
        return await self.select_order('id', 'desc')

    async def get_all(self, db: AsyncSession) -> Sequence[MarketplaceSkill]:
        """
        获取所有技能市场技能

        :param db: 数据库会话
        :return:
        """
        return await self.select_models(db)

    async def create(self, db: AsyncSession, obj: CreateMarketplaceSkillParam) -> None:
        """
        创建技能市场技能

        :param db: 数据库会话
        :param obj: 创建技能市场技能参数
        :return:
        """
        await self.create_model(db, obj)

    async def update(self, db: AsyncSession, pk: int, obj: UpdateMarketplaceSkillParam) -> int:
        """
        更新技能市场技能

        :param db: 数据库会话
        :param pk: 技能市场技能 ID
        :param obj: 更新 技能市场技能参数
        :return:
        """
        return await self.update_model(db, pk, obj)

    async def delete(self, db: AsyncSession, pks: list[int]) -> int:
        """
        批量删除技能市场技能

        :param db: 数据库会话
        :param pks: 技能市场技能 ID 列表
        :return:
        """
        return await self.delete_model_by_column(db, allow_multiple=True, id__in=pks)

    async def get_by_id(self, db: AsyncSession, skill_id: str) -> MarketplaceSkill | None:
        """
        根据技能ID获取技能

        :param db: 数据库会话
        :param skill_id: 技能ID
        :return:
        """
        return await self.select_model_by_column(db, skill_id=skill_id)

    async def increment_download_count(self, db: AsyncSession, skill_id: str) -> None:
        """
        增加技能下载次数

        :param db: 数据库会话
        :param skill_id: 技能ID
        """
        stmt = (
            update(MarketplaceSkill)
            .where(MarketplaceSkill.skill_id == skill_id)
            .values(download_count=MarketplaceSkill.download_count + 1)
        )
        await db.execute(stmt)


marketplace_skill_dao: CRUDMarketplaceSkill = CRUDMarketplaceSkill(MarketplaceSkill)
