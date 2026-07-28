from collections.abc import Sequence

from sqlalchemy import Select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy_crud_plus import CRUDPlus

from backend.app.hasn_growth.model import GrowthProjectLead
from backend.app.hasn_growth.schema.growth_project_lead import (
    CreateGrowthProjectLeadParam,
    UpdateGrowthProjectLeadParam,
)


class CRUDGrowthProjectLead(CRUDPlus[GrowthProjectLead]):
    async def get(self, db: AsyncSession, pk: int) -> GrowthProjectLead | None:
        """
        获取获客漏斗对全局联系人事实的项目级引用

        :param db: 数据库会话
        :param pk: 获客漏斗对全局联系人事实的项目级引用 ID
        :return:
        """
        return await self.select_model(db, pk)

    async def get_select(self) -> Select:
        """获取获客漏斗对全局联系人事实的项目级引用列表查询表达式"""
        return await self.select_order('id', 'desc')

    async def get_all(self, db: AsyncSession) -> Sequence[GrowthProjectLead]:
        """
        获取所有获客漏斗对全局联系人事实的项目级引用

        :param db: 数据库会话
        :return:
        """
        return await self.select_models(db)

    async def create(self, db: AsyncSession, obj: CreateGrowthProjectLeadParam) -> None:
        """
        创建获客漏斗对全局联系人事实的项目级引用

        :param db: 数据库会话
        :param obj: 创建获客漏斗对全局联系人事实的项目级引用参数
        :return:
        """
        await self.create_model(db, obj)

    async def update(self, db: AsyncSession, pk: int, obj: UpdateGrowthProjectLeadParam) -> int:
        """
        更新获客漏斗对全局联系人事实的项目级引用

        :param db: 数据库会话
        :param pk: 获客漏斗对全局联系人事实的项目级引用 ID
        :param obj: 更新 获客漏斗对全局联系人事实的项目级引用参数
        :return:
        """
        return await self.update_model(db, pk, obj)

    async def delete(self, db: AsyncSession, pks: list[int]) -> int:
        """
        批量删除获客漏斗对全局联系人事实的项目级引用

        :param db: 数据库会话
        :param pks: 获客漏斗对全局联系人事实的项目级引用 ID 列表
        :return:
        """
        return await self.delete_model_by_column(db, allow_multiple=True, id__in=pks)


growth_project_lead_dao: CRUDGrowthProjectLead = CRUDGrowthProjectLead(GrowthProjectLead)
