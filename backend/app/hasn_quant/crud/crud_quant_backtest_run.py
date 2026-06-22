from typing import Sequence

from sqlalchemy import Select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy_crud_plus import CRUDPlus

from backend.app.hasn_quant.model import QuantBacktestRun
from backend.app.hasn_quant.schema.quant_backtest_run import CreateQuantBacktestRunParam, UpdateQuantBacktestRunParam


class CRUDQuantBacktestRun(CRUDPlus[QuantBacktestRun]):
    async def get(self, db: AsyncSession, pk: int) -> QuantBacktestRun | None:
        """
        获取回测任务 + 绩效（job 式：提交→引擎跑→落 metrics/equity_curve）

        :param db: 数据库会话
        :param pk: 回测任务 + 绩效（job 式：提交→引擎跑→落 metrics/equity_curve） ID
        :return:
        """
        return await self.select_model(db, pk)

    async def get_select(self) -> Select:
        """获取回测任务 + 绩效（job 式：提交→引擎跑→落 metrics/equity_curve）列表查询表达式"""
        return await self.select_order('id', 'desc')

    async def get_all(self, db: AsyncSession) -> Sequence[QuantBacktestRun]:
        """
        获取所有回测任务 + 绩效（job 式：提交→引擎跑→落 metrics/equity_curve）

        :param db: 数据库会话
        :return:
        """
        return await self.select_models(db)

    async def create(self, db: AsyncSession, obj: CreateQuantBacktestRunParam) -> None:
        """
        创建回测任务 + 绩效（job 式：提交→引擎跑→落 metrics/equity_curve）

        :param db: 数据库会话
        :param obj: 创建回测任务 + 绩效（job 式：提交→引擎跑→落 metrics/equity_curve）参数
        :return:
        """
        await self.create_model(db, obj)

    async def update(self, db: AsyncSession, pk: int, obj: UpdateQuantBacktestRunParam) -> int:
        """
        更新回测任务 + 绩效（job 式：提交→引擎跑→落 metrics/equity_curve）

        :param db: 数据库会话
        :param pk: 回测任务 + 绩效（job 式：提交→引擎跑→落 metrics/equity_curve） ID
        :param obj: 更新 回测任务 + 绩效（job 式：提交→引擎跑→落 metrics/equity_curve）参数
        :return:
        """
        return await self.update_model(db, pk, obj)

    async def delete(self, db: AsyncSession, pks: list[int]) -> int:
        """
        批量删除回测任务 + 绩效（job 式：提交→引擎跑→落 metrics/equity_curve）

        :param db: 数据库会话
        :param pks: 回测任务 + 绩效（job 式：提交→引擎跑→落 metrics/equity_curve） ID 列表
        :return:
        """
        return await self.delete_model_by_column(db, allow_multiple=True, id__in=pks)


quant_backtest_run_dao: CRUDQuantBacktestRun = CRUDQuantBacktestRun(QuantBacktestRun)
