from typing import Sequence

from sqlalchemy import Select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy_crud_plus import CRUDPlus

from backend.app.hasn_finance.model import TradeReview
from backend.app.hasn_finance.schema.trade_review import CreateTradeReviewParam, UpdateTradeReviewParam


class CRUDTradeReview(CRUDPlus[TradeReview]):
    async def get(self, db: AsyncSession, pk: int) -> TradeReview | None:
        """
        获取交易复盘报告（流程 C·杀手锏·产物·同事务登记 hasn_artifacts，05 §3.1.6）

        :param db: 数据库会话
        :param pk: 交易复盘报告（流程 C·杀手锏·产物·同事务登记 hasn_artifacts，05 §3.1.6） ID
        :return:
        """
        return await self.select_model(db, pk)

    async def get_select(self) -> Select:
        """获取交易复盘报告（流程 C·杀手锏·产物·同事务登记 hasn_artifacts，05 §3.1.6）列表查询表达式"""
        return await self.select_order('id', 'desc')

    async def get_all(self, db: AsyncSession) -> Sequence[TradeReview]:
        """
        获取所有交易复盘报告（流程 C·杀手锏·产物·同事务登记 hasn_artifacts，05 §3.1.6）

        :param db: 数据库会话
        :return:
        """
        return await self.select_models(db)

    async def create(self, db: AsyncSession, obj: CreateTradeReviewParam) -> None:
        """
        创建交易复盘报告（流程 C·杀手锏·产物·同事务登记 hasn_artifacts，05 §3.1.6）

        :param db: 数据库会话
        :param obj: 创建交易复盘报告（流程 C·杀手锏·产物·同事务登记 hasn_artifacts，05 §3.1.6）参数
        :return:
        """
        await self.create_model(db, obj)

    async def update(self, db: AsyncSession, pk: int, obj: UpdateTradeReviewParam) -> int:
        """
        更新交易复盘报告（流程 C·杀手锏·产物·同事务登记 hasn_artifacts，05 §3.1.6）

        :param db: 数据库会话
        :param pk: 交易复盘报告（流程 C·杀手锏·产物·同事务登记 hasn_artifacts，05 §3.1.6） ID
        :param obj: 更新 交易复盘报告（流程 C·杀手锏·产物·同事务登记 hasn_artifacts，05 §3.1.6）参数
        :return:
        """
        return await self.update_model(db, pk, obj)

    async def delete(self, db: AsyncSession, pks: list[int]) -> int:
        """
        批量删除交易复盘报告（流程 C·杀手锏·产物·同事务登记 hasn_artifacts，05 §3.1.6）

        :param db: 数据库会话
        :param pks: 交易复盘报告（流程 C·杀手锏·产物·同事务登记 hasn_artifacts，05 §3.1.6） ID 列表
        :return:
        """
        return await self.delete_model_by_column(db, allow_multiple=True, id__in=pks)


trade_review_dao: CRUDTradeReview = CRUDTradeReview(TradeReview)
