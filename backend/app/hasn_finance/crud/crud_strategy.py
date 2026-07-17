from typing import Sequence

from sqlalchemy import Select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy_crud_plus import CRUDPlus

from backend.app.hasn_finance.model import Strategy
from backend.app.hasn_finance.schema.strategy import CreateStrategyParam, UpdateStrategyParam


class CRUDStrategy(CRUDPlus[Strategy]):
    async def get(self, db: AsyncSession, pk: int) -> Strategy | None:
        """
        获取策略（流程 B·产物+容器·可挂平台项目，05 §3.1.3）

        :param db: 数据库会话
        :param pk: 策略（流程 B·产物+容器·可挂平台项目，05 §3.1.3） ID
        :return:
        """
        return await self.select_model(db, pk)

    async def get_select(self) -> Select:
        """获取策略（流程 B·产物+容器·可挂平台项目，05 §3.1.3）列表查询表达式"""
        return await self.select_order('id', 'desc')

    async def get_all(self, db: AsyncSession) -> Sequence[Strategy]:
        """
        获取所有策略（流程 B·产物+容器·可挂平台项目，05 §3.1.3）

        :param db: 数据库会话
        :return:
        """
        return await self.select_models(db)

    async def create(self, db: AsyncSession, obj: CreateStrategyParam) -> None:
        """
        创建策略（流程 B·产物+容器·可挂平台项目，05 §3.1.3）

        :param db: 数据库会话
        :param obj: 创建策略（流程 B·产物+容器·可挂平台项目，05 §3.1.3）参数
        :return:
        """
        await self.create_model(db, obj)

    async def update(self, db: AsyncSession, pk: int, obj: UpdateStrategyParam) -> int:
        """
        更新策略（流程 B·产物+容器·可挂平台项目，05 §3.1.3）

        :param db: 数据库会话
        :param pk: 策略（流程 B·产物+容器·可挂平台项目，05 §3.1.3） ID
        :param obj: 更新 策略（流程 B·产物+容器·可挂平台项目，05 §3.1.3）参数
        :return:
        """
        return await self.update_model(db, pk, obj)

    async def delete(self, db: AsyncSession, pks: list[int]) -> int:
        """
        批量删除策略（流程 B·产物+容器·可挂平台项目，05 §3.1.3）

        :param db: 数据库会话
        :param pks: 策略（流程 B·产物+容器·可挂平台项目，05 §3.1.3） ID 列表
        :return:
        """
        return await self.delete_model_by_column(db, allow_multiple=True, id__in=pks)


strategy_dao: CRUDStrategy = CRUDStrategy(Strategy)
