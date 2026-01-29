from typing import Any, Sequence

from sqlalchemy.ext.asyncio import AsyncSession

from backend.app.user_tier.crud.crud_user_credit_balance import user_credit_balance_dao
from backend.app.user_tier.model import UserCreditBalance
from backend.app.user_tier.schema.user_credit_balance import (
    CreateUserCreditBalanceParam,
    DeleteUserCreditBalanceParam,
    UpdateUserCreditBalanceParam,
)
from backend.common.exception import errors
from backend.common.pagination import paging_data


class UserCreditBalanceService:
    @staticmethod
    async def get(*, db: AsyncSession, pk: int) -> UserCreditBalance:
        """
        获取用户积分余额

        :param db: 数据库会话
        :param pk: 用户积分余额 ID
        :return:
        """
        user_credit_balance = await user_credit_balance_dao.get(db, pk)
        if not user_credit_balance:
            raise errors.NotFoundError(msg='用户积分余额不存在')
        return user_credit_balance

    @staticmethod
    async def get_list(db: AsyncSession) -> dict[str, Any]:
        """
        获取用户积分余额列表

        :param db: 数据库会话
        :return:
        """
        user_credit_balance_select = await user_credit_balance_dao.get_select()
        return await paging_data(db, user_credit_balance_select)

    @staticmethod
    async def get_all(*, db: AsyncSession) -> Sequence[UserCreditBalance]:
        """
        获取所有用户积分余额

        :param db: 数据库会话
        :return:
        """
        user_credit_balances = await user_credit_balance_dao.get_all(db)
        return user_credit_balances

    @staticmethod
    async def create(*, db: AsyncSession, obj: CreateUserCreditBalanceParam) -> None:
        """
        创建用户积分余额

        :param db: 数据库会话
        :param obj: 创建用户积分余额参数
        :return:
        """
        await user_credit_balance_dao.create(db, obj)

    @staticmethod
    async def update(*, db: AsyncSession, pk: int, obj: UpdateUserCreditBalanceParam) -> int:
        """
        更新用户积分余额

        :param db: 数据库会话
        :param pk: 用户积分余额 ID
        :param obj: 更新用户积分余额参数
        :return:
        """
        count = await user_credit_balance_dao.update(db, pk, obj)
        return count

    @staticmethod
    async def delete(*, db: AsyncSession, obj: DeleteUserCreditBalanceParam) -> int:
        """
        删除用户积分余额

        :param db: 数据库会话
        :param obj: 用户积分余额 ID 列表
        :return:
        """
        count = await user_credit_balance_dao.delete(db, obj.pks)
        return count


user_credit_balance_service: UserCreditBalanceService = UserCreditBalanceService()
