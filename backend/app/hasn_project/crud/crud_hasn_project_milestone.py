from typing import Sequence

from sqlalchemy import Select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy_crud_plus import CRUDPlus

from backend.app.hasn_project.model import HasnProjectMilestone
from backend.app.hasn_project.schema.hasn_project_milestone import CreateHasnProjectMilestoneParam, UpdateHasnProjectMilestoneParam


class CRUDHasnProjectMilestone(CRUDPlus[HasnProjectMilestone]):
    async def get(self, db: AsyncSession, pk: int) -> HasnProjectMilestone | None:
        """
        获取平台项目里程碑（v2·业务状态标记·无依赖无门控，doc38 §12.3）

        :param db: 数据库会话
        :param pk: 平台项目里程碑（v2·业务状态标记·无依赖无门控，doc38 §12.3） ID
        :return:
        """
        return await self.select_model(db, pk)

    async def get_select(self) -> Select:
        """获取平台项目里程碑（v2·业务状态标记·无依赖无门控，doc38 §12.3）列表查询表达式"""
        return await self.select_order('id', 'desc')

    async def get_all(self, db: AsyncSession) -> Sequence[HasnProjectMilestone]:
        """
        获取所有平台项目里程碑（v2·业务状态标记·无依赖无门控，doc38 §12.3）

        :param db: 数据库会话
        :return:
        """
        return await self.select_models(db)

    async def create(self, db: AsyncSession, obj: CreateHasnProjectMilestoneParam) -> None:
        """
        创建平台项目里程碑（v2·业务状态标记·无依赖无门控，doc38 §12.3）

        :param db: 数据库会话
        :param obj: 创建平台项目里程碑（v2·业务状态标记·无依赖无门控，doc38 §12.3）参数
        :return:
        """
        await self.create_model(db, obj)

    async def update(self, db: AsyncSession, pk: int, obj: UpdateHasnProjectMilestoneParam) -> int:
        """
        更新平台项目里程碑（v2·业务状态标记·无依赖无门控，doc38 §12.3）

        :param db: 数据库会话
        :param pk: 平台项目里程碑（v2·业务状态标记·无依赖无门控，doc38 §12.3） ID
        :param obj: 更新 平台项目里程碑（v2·业务状态标记·无依赖无门控，doc38 §12.3）参数
        :return:
        """
        return await self.update_model(db, pk, obj)

    async def delete(self, db: AsyncSession, pks: list[int]) -> int:
        """
        批量删除平台项目里程碑（v2·业务状态标记·无依赖无门控，doc38 §12.3）

        :param db: 数据库会话
        :param pks: 平台项目里程碑（v2·业务状态标记·无依赖无门控，doc38 §12.3） ID 列表
        :return:
        """
        return await self.delete_model_by_column(db, allow_multiple=True, id__in=pks)


hasn_project_milestone_dao: CRUDHasnProjectMilestone = CRUDHasnProjectMilestone(HasnProjectMilestone)
