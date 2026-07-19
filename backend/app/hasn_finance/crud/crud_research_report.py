from typing import Sequence

from sqlalchemy import Select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy_crud_plus import CRUDPlus

from backend.app.hasn_finance.model import ResearchReport
from backend.app.hasn_finance.schema.research_report import CreateResearchReportParam, UpdateResearchReportParam


class CRUDResearchReport(CRUDPlus[ResearchReport]):
    async def get(self, db: AsyncSession, pk: int) -> ResearchReport | None:
        """
        获取投研报告（流程 A·产物·同事务登记 hasn_artifacts，05 §3.1.2）

        :param db: 数据库会话
        :param pk: 投研报告（流程 A·产物·同事务登记 hasn_artifacts，05 §3.1.2） ID
        :return:
        """
        return await self.select_model(db, pk)

    async def get_select(self) -> Select:
        """获取投研报告（流程 A·产物·同事务登记 hasn_artifacts，05 §3.1.2）列表查询表达式"""
        return await self.select_order('id', 'desc')

    async def get_all(self, db: AsyncSession) -> Sequence[ResearchReport]:
        """
        获取所有投研报告（流程 A·产物·同事务登记 hasn_artifacts，05 §3.1.2）

        :param db: 数据库会话
        :return:
        """
        return await self.select_models(db)

    async def create(self, db: AsyncSession, obj: CreateResearchReportParam) -> None:
        """
        创建投研报告（流程 A·产物·同事务登记 hasn_artifacts，05 §3.1.2）

        :param db: 数据库会话
        :param obj: 创建投研报告（流程 A·产物·同事务登记 hasn_artifacts，05 §3.1.2）参数
        :return:
        """
        await self.create_model(db, obj)

    async def update(self, db: AsyncSession, pk: int, obj: UpdateResearchReportParam) -> int:
        """
        更新投研报告（流程 A·产物·同事务登记 hasn_artifacts，05 §3.1.2）

        :param db: 数据库会话
        :param pk: 投研报告（流程 A·产物·同事务登记 hasn_artifacts，05 §3.1.2） ID
        :param obj: 更新 投研报告（流程 A·产物·同事务登记 hasn_artifacts，05 §3.1.2）参数
        :return:
        """
        return await self.update_model(db, pk, obj)

    async def delete(self, db: AsyncSession, pks: list[int]) -> int:
        """
        批量删除投研报告（流程 A·产物·同事务登记 hasn_artifacts，05 §3.1.2）

        :param db: 数据库会话
        :param pks: 投研报告（流程 A·产物·同事务登记 hasn_artifacts，05 §3.1.2） ID 列表
        :return:
        """
        return await self.delete_model_by_column(db, allow_multiple=True, id__in=pks)


research_report_dao: CRUDResearchReport = CRUDResearchReport(ResearchReport)
