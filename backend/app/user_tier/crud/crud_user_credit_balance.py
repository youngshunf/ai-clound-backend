from typing import Sequence

from sqlalchemy import Select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy_crud_plus import CRUDPlus

from backend.app.user_tier.model import UserCreditBalance
from backend.app.user_tier.schema.user_credit_balance import CreateUserCreditBalanceParam, UpdateUserCreditBalanceParam


class CRUDUserCreditBalance(CRUDPlus[UserCreditBalance]):
    async def get(self, db: AsyncSession, pk: int) -> UserCreditBalance | None:
        """
        获取用户积分余额

        :param db: 数据库会话
        :param pk: 用户积分余额 ID
        :return:
        """
        return await self.select_model(db, pk)

    async def get_select(self) -> Select:
        """获取用户积分余额列表查询表达式"""
        return await self.select_order('id', 'desc')

    async def get_all(self, db: AsyncSession) -> Sequence[UserCreditBalance]:
        """
        获取所有用户积分余额

        :param db: 数据库会话
        :return:
        """
        return await self.select_models(db)

    async def create(self, db: AsyncSession, obj: CreateUserCreditBalanceParam) -> None:
        """
        创建用户积分余额

        :param db: 数据库会话
        :param obj: 创建用户积分余额参数
        :return:
        """
        await self.create_model(db, obj)

    async def update(self, db: AsyncSession, pk: int, obj: UpdateUserCreditBalanceParam) -> int:
        """
        更新用户积分余额

        :param db: 数据库会话
        :param pk: 用户积分余额 ID
        :param obj: 更新 用户积分余额参数
        :return:
        """
        return await self.update_model(db, pk, obj)

    async def delete(self, db: AsyncSession, pks: list[int]) -> int:
        """
        批量删除用户积分余额

        :param db: 数据库会话
        :param pks: 用户积分余额 ID 列表
        :return:
        """
        return await self.delete_model_by_column(db, allow_multiple=True, id__in=pks)


user_credit_balance_dao: CRUDUserCreditBalance = CRUDUserCreditBalance(UserCreditBalance)
