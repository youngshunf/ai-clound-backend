from typing import Sequence

from sqlalchemy import Select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy_crud_plus import CRUDPlus

from backend.app.hasn.model import HasnWorkbenchBriefingFeedback
from backend.app.hasn.schema.hasn_workbench_briefing_feedback import CreateHasnWorkbenchBriefingFeedbackParam, UpdateHasnWorkbenchBriefingFeedbackParam


class CRUDHasnWorkbenchBriefingFeedback(CRUDPlus[HasnWorkbenchBriefingFeedback]):
    async def get(self, db: AsyncSession, pk: int) -> HasnWorkbenchBriefingFeedback | None:
        """
        获取HASN 工作台简报反馈（云端权威）

        :param db: 数据库会话
        :param pk: HASN 工作台简报反馈（云端权威） ID
        :return:
        """
        return await self.select_model(db, pk)

    async def get_select(self) -> Select:
        """获取HASN 工作台简报反馈（云端权威）列表查询表达式"""
        return await self.select_order('id', 'desc')

    async def get_all(self, db: AsyncSession) -> Sequence[HasnWorkbenchBriefingFeedback]:
        """
        获取所有HASN 工作台简报反馈（云端权威）

        :param db: 数据库会话
        :return:
        """
        return await self.select_models(db)

    async def create(self, db: AsyncSession, obj: CreateHasnWorkbenchBriefingFeedbackParam) -> None:
        """
        创建HASN 工作台简报反馈（云端权威）

        :param db: 数据库会话
        :param obj: 创建HASN 工作台简报反馈（云端权威）参数
        :return:
        """
        await self.create_model(db, obj)

    async def update(self, db: AsyncSession, pk: int, obj: UpdateHasnWorkbenchBriefingFeedbackParam) -> int:
        """
        更新HASN 工作台简报反馈（云端权威）

        :param db: 数据库会话
        :param pk: HASN 工作台简报反馈（云端权威） ID
        :param obj: 更新 HASN 工作台简报反馈（云端权威）参数
        :return:
        """
        return await self.update_model(db, pk, obj)

    async def delete(self, db: AsyncSession, pks: list[int]) -> int:
        """
        批量删除HASN 工作台简报反馈（云端权威）

        :param db: 数据库会话
        :param pks: HASN 工作台简报反馈（云端权威） ID 列表
        :return:
        """
        return await self.delete_model_by_column(db, allow_multiple=True, id__in=pks)


hasn_workbench_briefing_feedback_dao: CRUDHasnWorkbenchBriefingFeedback = CRUDHasnWorkbenchBriefingFeedback(HasnWorkbenchBriefingFeedback)
