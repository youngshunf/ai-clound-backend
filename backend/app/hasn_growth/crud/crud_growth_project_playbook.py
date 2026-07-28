from collections.abc import Sequence

from sqlalchemy import Select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy_crud_plus import CRUDPlus

from backend.app.hasn_growth.model import GrowthProjectPlaybook
from backend.app.hasn_growth.schema.growth_project_playbook import (
    CreateGrowthProjectPlaybookParam,
    UpdateGrowthProjectPlaybookParam,
)


class CRUDGrowthProjectPlaybook(CRUDPlus[GrowthProjectPlaybook]):
    async def get(self, db: AsyncSession, pk: int) -> GrowthProjectPlaybook | None:
        """
        获取获客漏斗采用的打法版本与项目级配置快照

        :param db: 数据库会话
        :param pk: 获客漏斗采用的打法版本与项目级配置快照 ID
        :return:
        """
        return await self.select_model(db, pk)

    async def get_select(self) -> Select:
        """获取获客漏斗采用的打法版本与项目级配置快照列表查询表达式"""
        return await self.select_order('id', 'desc')

    async def get_all(self, db: AsyncSession) -> Sequence[GrowthProjectPlaybook]:
        """
        获取所有获客漏斗采用的打法版本与项目级配置快照

        :param db: 数据库会话
        :return:
        """
        return await self.select_models(db)

    async def create(self, db: AsyncSession, obj: CreateGrowthProjectPlaybookParam) -> None:
        """
        创建获客漏斗采用的打法版本与项目级配置快照

        :param db: 数据库会话
        :param obj: 创建获客漏斗采用的打法版本与项目级配置快照参数
        :return:
        """
        await self.create_model(db, obj)

    async def update(self, db: AsyncSession, pk: int, obj: UpdateGrowthProjectPlaybookParam) -> int:
        """
        更新获客漏斗采用的打法版本与项目级配置快照

        :param db: 数据库会话
        :param pk: 获客漏斗采用的打法版本与项目级配置快照 ID
        :param obj: 更新 获客漏斗采用的打法版本与项目级配置快照参数
        :return:
        """
        return await self.update_model(db, pk, obj)

    async def delete(self, db: AsyncSession, pks: list[int]) -> int:
        """
        批量删除获客漏斗采用的打法版本与项目级配置快照

        :param db: 数据库会话
        :param pks: 获客漏斗采用的打法版本与项目级配置快照 ID 列表
        :return:
        """
        return await self.delete_model_by_column(db, allow_multiple=True, id__in=pks)


growth_project_playbook_dao: CRUDGrowthProjectPlaybook = CRUDGrowthProjectPlaybook(GrowthProjectPlaybook)
