from typing import Any, Sequence

from sqlalchemy.ext.asyncio import AsyncSession

from backend.app.billing.crud.crud_credit_transaction import credit_transaction_dao
from backend.app.billing.model import CreditTransaction
from backend.app.billing.schema.credit_transaction import CreateCreditTransactionParam, DeleteCreditTransactionParam, UpdateCreditTransactionParam
from backend.common.exception import errors
from backend.common.pagination import paging_data


class CreditTransactionService:
    @staticmethod
    async def get(*, db: AsyncSession, pk: int) -> CreditTransaction:
        """
        获取积分交易记录

        :param db: 数据库会话
        :param pk: 积分交易记录 ID
        :return:
        """
        credit_transaction = await credit_transaction_dao.get(db, pk)
        if not credit_transaction:
            raise errors.NotFoundError(msg='积分交易记录不存在')
        return credit_transaction

    @staticmethod
    async def get_list(
        db: AsyncSession,
        *,
        user_keyword: str | None = None,
        transaction_type: str | None = None,
        reference_id: str | None = None,
        reference_type: str | None = None,
    ) -> dict[str, Any]:
        """
        获取积分交易记录列表

        :param db: 数据库会话
        :param user_keyword: 用户昵称/手机号搜索关键词
        :param transaction_type: 交易类型
        :param reference_id: 关联 ID
        :param reference_type: 关联类型
        :return:
        """
        credit_transaction_select = await credit_transaction_dao.get_select(
            user_keyword=user_keyword,
            transaction_type=transaction_type,
            reference_id=reference_id,
            reference_type=reference_type,
        )
        return await paging_data(db, credit_transaction_select, unique=False)

    @staticmethod
    async def get_all(*, db: AsyncSession) -> Sequence[CreditTransaction]:
        """
        获取所有积分交易记录

        :param db: 数据库会话
        :return:
        """
        credit_transactions = await credit_transaction_dao.get_all(db)
        return credit_transactions

    @staticmethod
    async def create(*, db: AsyncSession, obj: CreateCreditTransactionParam) -> None:
        """
        创建积分交易记录

        :param db: 数据库会话
        :param obj: 创建积分交易记录参数
        :return:
        """
        await credit_transaction_dao.create(db, obj)

    @staticmethod
    async def update(*, db: AsyncSession, pk: int, obj: UpdateCreditTransactionParam) -> int:
        """
        更新积分交易记录

        :param db: 数据库会话
        :param pk: 积分交易记录 ID
        :param obj: 更新积分交易记录参数
        :return:
        """
        count = await credit_transaction_dao.update(db, pk, obj)
        return count

    @staticmethod
    async def delete(*, db: AsyncSession, obj: DeleteCreditTransactionParam) -> int:
        """
        删除积分交易记录

        :param db: 数据库会话
        :param obj: 积分交易记录 ID 列表
        :return:
        """
        count = await credit_transaction_dao.delete(db, obj.pks)
        return count


credit_transaction_service: CreditTransactionService = CreditTransactionService()
