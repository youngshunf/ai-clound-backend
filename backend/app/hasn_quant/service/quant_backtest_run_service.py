from typing import Any, Sequence

from sqlalchemy.ext.asyncio import AsyncSession

from backend.app.hasn_quant.crud.crud_quant_backtest_run import quant_backtest_run_dao
from backend.app.hasn_quant.model import QuantBacktestRun
from backend.app.hasn_quant.schema.quant_backtest_run import CreateQuantBacktestRunParam, DeleteQuantBacktestRunParam, UpdateQuantBacktestRunParam
from backend.common.exception import errors
from backend.common.pagination import paging_data


class QuantBacktestRunService:
    @staticmethod
    async def get(*, db: AsyncSession, pk: int) -> QuantBacktestRun:
        """
        获取回测任务 + 绩效（job 式：提交→引擎跑→落 metrics/equity_curve）

        :param db: 数据库会话
        :param pk: 回测任务 + 绩效（job 式：提交→引擎跑→落 metrics/equity_curve） ID
        :return:
        """
        quant_backtest_run = await quant_backtest_run_dao.get(db, pk)
        if not quant_backtest_run:
            raise errors.NotFoundError(msg='回测任务 + 绩效（job 式：提交→引擎跑→落 metrics/equity_curve）不存在')
        return quant_backtest_run

    @staticmethod
    async def get_list(db: AsyncSession) -> dict[str, Any]:
        """
        获取回测任务 + 绩效（job 式：提交→引擎跑→落 metrics/equity_curve）列表

        :param db: 数据库会话
        :return:
        """
        quant_backtest_run_select = await quant_backtest_run_dao.get_select()
        return await paging_data(db, quant_backtest_run_select)

    @staticmethod
    async def get_all(*, db: AsyncSession) -> Sequence[QuantBacktestRun]:
        """
        获取所有回测任务 + 绩效（job 式：提交→引擎跑→落 metrics/equity_curve）

        :param db: 数据库会话
        :return:
        """
        quant_backtest_run_list = await quant_backtest_run_dao.get_all(db)
        return quant_backtest_run_list

    @staticmethod
    async def create(*, db: AsyncSession, obj: CreateQuantBacktestRunParam) -> None:
        """
        创建回测任务 + 绩效（job 式：提交→引擎跑→落 metrics/equity_curve）

        :param db: 数据库会话
        :param obj: 创建回测任务 + 绩效（job 式：提交→引擎跑→落 metrics/equity_curve）参数
        :return:
        """
        await quant_backtest_run_dao.create(db, obj)

    @staticmethod
    async def update(*, db: AsyncSession, pk: int, obj: UpdateQuantBacktestRunParam) -> int:
        """
        更新回测任务 + 绩效（job 式：提交→引擎跑→落 metrics/equity_curve）

        :param db: 数据库会话
        :param pk: 回测任务 + 绩效（job 式：提交→引擎跑→落 metrics/equity_curve） ID
        :param obj: 更新回测任务 + 绩效（job 式：提交→引擎跑→落 metrics/equity_curve）参数
        :return:
        """
        count = await quant_backtest_run_dao.update(db, pk, obj)
        return count

    @staticmethod
    async def delete(*, db: AsyncSession, obj: DeleteQuantBacktestRunParam) -> int:
        """
        删除回测任务 + 绩效（job 式：提交→引擎跑→落 metrics/equity_curve）

        :param db: 数据库会话
        :param obj: 回测任务 + 绩效（job 式：提交→引擎跑→落 metrics/equity_curve） ID 列表
        :return:
        """
        count = await quant_backtest_run_dao.delete(db, obj.pks)
        return count


quant_backtest_run_service: QuantBacktestRunService = QuantBacktestRunService()
