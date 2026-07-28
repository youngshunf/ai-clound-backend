from collections.abc import Sequence

from sqlalchemy import Select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy_crud_plus import CRUDPlus

from backend.app.hasn_growth.model import GrowthProjectProvision
from backend.app.hasn_growth.schema.growth_project_provision import (
    CreateGrowthProjectProvisionParam,
    UpdateGrowthProjectProvisionParam,
)


class CRUDGrowthProjectProvision(CRUDPlus[GrowthProjectProvision]):
    async def get(self, db: AsyncSession, pk: int) -> GrowthProjectProvision | None:
        """
        获取建漏斗、建库、挂靠和建站步骤的可靠编排状态

        :param db: 数据库会话
        :param pk: 建漏斗、建库、挂靠和建站步骤的可靠编排状态 ID
        :return:
        """
        return await self.select_model(db, pk)

    async def get_select(self) -> Select:
        """获取建漏斗、建库、挂靠和建站步骤的可靠编排状态列表查询表达式"""
        return await self.select_order('id', 'desc')

    async def get_all(self, db: AsyncSession) -> Sequence[GrowthProjectProvision]:
        """
        获取所有建漏斗、建库、挂靠和建站步骤的可靠编排状态

        :param db: 数据库会话
        :return:
        """
        return await self.select_models(db)

    async def create(self, db: AsyncSession, obj: CreateGrowthProjectProvisionParam) -> None:
        """
        创建建漏斗、建库、挂靠和建站步骤的可靠编排状态

        :param db: 数据库会话
        :param obj: 创建建漏斗、建库、挂靠和建站步骤的可靠编排状态参数
        :return:
        """
        await self.create_model(db, obj)

    async def update(self, db: AsyncSession, pk: int, obj: UpdateGrowthProjectProvisionParam) -> int:
        """
        更新建漏斗、建库、挂靠和建站步骤的可靠编排状态

        :param db: 数据库会话
        :param pk: 建漏斗、建库、挂靠和建站步骤的可靠编排状态 ID
        :param obj: 更新 建漏斗、建库、挂靠和建站步骤的可靠编排状态参数
        :return:
        """
        return await self.update_model(db, pk, obj)

    async def delete(self, db: AsyncSession, pks: list[int]) -> int:
        """
        批量删除建漏斗、建库、挂靠和建站步骤的可靠编排状态

        :param db: 数据库会话
        :param pks: 建漏斗、建库、挂靠和建站步骤的可靠编排状态 ID 列表
        :return:
        """
        return await self.delete_model_by_column(db, allow_multiple=True, id__in=pks)


growth_project_provision_dao: CRUDGrowthProjectProvision = CRUDGrowthProjectProvision(GrowthProjectProvision)
