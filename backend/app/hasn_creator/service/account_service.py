from typing import Any, Sequence

from sqlalchemy.ext.asyncio import AsyncSession

from backend.app.hasn_creator.crud.crud_account import account_dao
from backend.app.hasn_creator.model import Account
from backend.app.hasn_creator.schema.account import CreateAccountParam, DeleteAccountParam, UpdateAccountParam
from backend.common.exception import errors
from backend.common.pagination import paging_data


class AccountService:
    @staticmethod
    async def get(*, db: AsyncSession, pk: int) -> Account:
        """
        获取平台账号（1:N project）；同一项目多平台真实账号

        :param db: 数据库会话
        :param pk: 平台账号（1:N project）；同一项目多平台真实账号 ID
        :return:
        """
        account = await account_dao.get(db, pk)
        if not account:
            raise errors.NotFoundError(msg='平台账号（1:N project）；同一项目多平台真实账号不存在')
        return account

    @staticmethod
    async def get_list(db: AsyncSession) -> dict[str, Any]:
        """
        获取平台账号（1:N project）；同一项目多平台真实账号列表

        :param db: 数据库会话
        :return:
        """
        account_select = await account_dao.get_select()
        return await paging_data(db, account_select)

    @staticmethod
    async def get_all(*, db: AsyncSession) -> Sequence[Account]:
        """
        获取所有平台账号（1:N project）；同一项目多平台真实账号

        :param db: 数据库会话
        :return:
        """
        account_list = await account_dao.get_all(db)
        return account_list

    @staticmethod
    async def create(*, db: AsyncSession, obj: CreateAccountParam) -> None:
        """
        创建平台账号（1:N project）；同一项目多平台真实账号

        :param db: 数据库会话
        :param obj: 创建平台账号（1:N project）；同一项目多平台真实账号参数
        :return:
        """
        await account_dao.create(db, obj)

    @staticmethod
    async def update(*, db: AsyncSession, pk: int, obj: UpdateAccountParam) -> int:
        """
        更新平台账号（1:N project）；同一项目多平台真实账号

        :param db: 数据库会话
        :param pk: 平台账号（1:N project）；同一项目多平台真实账号 ID
        :param obj: 更新平台账号（1:N project）；同一项目多平台真实账号参数
        :return:
        """
        count = await account_dao.update(db, pk, obj)
        return count

    @staticmethod
    async def delete(*, db: AsyncSession, obj: DeleteAccountParam) -> int:
        """
        删除平台账号（1:N project）；同一项目多平台真实账号

        :param db: 数据库会话
        :param obj: 平台账号（1:N project）；同一项目多平台真实账号 ID 列表
        :return:
        """
        count = await account_dao.delete(db, obj.pks)
        return count


account_service: AccountService = AccountService()
