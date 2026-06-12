from typing import Sequence

from sqlalchemy import Select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy_crud_plus import CRUDPlus

from backend.app.marketplace.model import MarketplacePersonalSkill
from backend.app.marketplace.schema.marketplace_personal_skill import CreateMarketplacePersonalSkillParam, UpdateMarketplacePersonalSkillParam


class CRUDMarketplacePersonalSkill(CRUDPlus[MarketplacePersonalSkill]):
    async def get(self, db: AsyncSession, pk: int) -> MarketplacePersonalSkill | None:
        """
        获取个人技能同步表（个人技能库 SSOT）

        :param db: 数据库会话
        :param pk: 个人技能同步表（个人技能库 SSOT） ID
        :return:
        """
        return await self.select_model(db, pk)

    async def get_select(self) -> Select:
        """获取个人技能同步表（个人技能库 SSOT）列表查询表达式"""
        return await self.select_order('id', 'desc')

    async def get_all(self, db: AsyncSession) -> Sequence[MarketplacePersonalSkill]:
        """
        获取所有个人技能同步表（个人技能库 SSOT）

        :param db: 数据库会话
        :return:
        """
        return await self.select_models(db)

    async def create(self, db: AsyncSession, obj: CreateMarketplacePersonalSkillParam) -> None:
        """
        创建个人技能同步表（个人技能库 SSOT）

        :param db: 数据库会话
        :param obj: 创建个人技能同步表（个人技能库 SSOT）参数
        :return:
        """
        await self.create_model(db, obj)

    async def update(self, db: AsyncSession, pk: int, obj: UpdateMarketplacePersonalSkillParam) -> int:
        """
        更新个人技能同步表（个人技能库 SSOT）

        :param db: 数据库会话
        :param pk: 个人技能同步表（个人技能库 SSOT） ID
        :param obj: 更新 个人技能同步表（个人技能库 SSOT）参数
        :return:
        """
        return await self.update_model(db, pk, obj)

    async def delete(self, db: AsyncSession, pks: list[int]) -> int:
        """
        批量删除个人技能同步表（个人技能库 SSOT）

        :param db: 数据库会话
        :param pks: 个人技能同步表（个人技能库 SSOT） ID 列表
        :return:
        """
        return await self.delete_model_by_column(db, allow_multiple=True, id__in=pks)


marketplace_personal_skill_dao: CRUDMarketplacePersonalSkill = CRUDMarketplacePersonalSkill(MarketplacePersonalSkill)
