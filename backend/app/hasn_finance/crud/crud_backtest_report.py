from typing import Sequence

from sqlalchemy import Select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy_crud_plus import CRUDPlus

from backend.app.hasn_finance.model import BacktestReport
from backend.app.hasn_finance.schema.backtest_report import CreateBacktestReportParam, UpdateBacktestReportParam


class CRUDBacktestReport(CRUDPlus[BacktestReport]):
    async def get(self, db: AsyncSession, pk: int) -> BacktestReport | None:
        """
        获取回测报告（流程 B·产物·同事务登记 hasn_artifacts，05 §3.1.4）

        :param db: 数据库会话
        :param pk: 回测报告（流程 B·产物·同事务登记 hasn_artifacts，05 §3.1.4） ID
        :return:
        """
        return await self.select_model(db, pk)

    async def get_select(self) -> Select:
        """获取回测报告（流程 B·产物·同事务登记 hasn_artifacts，05 §3.1.4）列表查询表达式"""
        return await self.select_order('id', 'desc')

    async def get_all(self, db: AsyncSession) -> Sequence[BacktestReport]:
        """
        获取所有回测报告（流程 B·产物·同事务登记 hasn_artifacts，05 §3.1.4）

        :param db: 数据库会话
        :return:
        """
        return await self.select_models(db)

    async def create(self, db: AsyncSession, obj: CreateBacktestReportParam) -> None:
        """
        创建回测报告（流程 B·产物·同事务登记 hasn_artifacts，05 §3.1.4）

        :param db: 数据库会话
        :param obj: 创建回测报告（流程 B·产物·同事务登记 hasn_artifacts，05 §3.1.4）参数
        :return:
        """
        await self.create_model(db, obj)

    async def update(self, db: AsyncSession, pk: int, obj: UpdateBacktestReportParam) -> int:
        """
        更新回测报告（流程 B·产物·同事务登记 hasn_artifacts，05 §3.1.4）

        :param db: 数据库会话
        :param pk: 回测报告（流程 B·产物·同事务登记 hasn_artifacts，05 §3.1.4） ID
        :param obj: 更新 回测报告（流程 B·产物·同事务登记 hasn_artifacts，05 §3.1.4）参数
        :return:
        """
        return await self.update_model(db, pk, obj)

    async def delete(self, db: AsyncSession, pks: list[int]) -> int:
        """
        批量删除回测报告（流程 B·产物·同事务登记 hasn_artifacts，05 §3.1.4）

        :param db: 数据库会话
        :param pks: 回测报告（流程 B·产物·同事务登记 hasn_artifacts，05 §3.1.4） ID 列表
        :return:
        """
        return await self.delete_model_by_column(db, allow_multiple=True, id__in=pks)


backtest_report_dao: CRUDBacktestReport = CRUDBacktestReport(BacktestReport)
