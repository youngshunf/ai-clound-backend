from typing import Sequence

from sqlalchemy import Select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy_crud_plus import CRUDPlus

from backend.app.llm.model import CreditTransaction
from backend.app.llm.schema.credit_transaction import CreateCreditTransactionParam, UpdateCreditTransactionParam


class CRUDCreditTransaction(CRUDPlus[CreditTransaction]):
    async def get(self, db: AsyncSession, pk: int) -> CreditTransaction | None:
        """
        获取积分交易记录表 - 审计所有积分变动

        :param db: 数据库会话
        :param pk: 积分交易记录表 - 审计所有积分变动 ID
        :return:
        """
        return await self.select_model(db, pk)

    async def get_select(self) -> Select:
        """获取积分交易记录表 - 审计所有积分变动列表查询表达式"""
        return await self.select_order('id', 'desc')

    async def get_all(self, db: AsyncSession) -> Sequence[CreditTransaction]:
        """
        获取所有积分交易记录表 - 审计所有积分变动

        :param db: 数据库会话
        :return:
        """
        return await self.select_models(db)

    async def create(self, db: AsyncSession, obj: CreateCreditTransactionParam) -> None:
        """
        创建积分交易记录表 - 审计所有积分变动

        :param db: 数据库会话
        :param obj: 创建积分交易记录表 - 审计所有积分变动参数
        :return:
        """
        await self.create_model(db, obj)

    async def update(self, db: AsyncSession, pk: int, obj: UpdateCreditTransactionParam) -> int:
        """
        更新积分交易记录表 - 审计所有积分变动

        :param db: 数据库会话
        :param pk: 积分交易记录表 - 审计所有积分变动 ID
        :param obj: 更新 积分交易记录表 - 审计所有积分变动参数
        :return:
        """
        return await self.update_model(db, pk, obj)

    async def delete(self, db: AsyncSession, pks: list[int]) -> int:
        """
        批量删除积分交易记录表 - 审计所有积分变动

        :param db: 数据库会话
        :param pks: 积分交易记录表 - 审计所有积分变动 ID 列表
        :return:
        """
        return await self.delete_model_by_column(db, allow_multiple=True, id__in=pks)


credit_transaction_dao: CRUDCreditTransaction = CRUDCreditTransaction(CreditTransaction)
