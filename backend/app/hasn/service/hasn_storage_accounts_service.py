from typing import Any, Sequence

from sqlalchemy.ext.asyncio import AsyncSession

from backend.app.hasn.crud.crud_hasn_storage_accounts import hasn_storage_accounts_dao
from backend.app.hasn.model import HasnStorageAccounts
from backend.app.hasn.schema.hasn_storage_accounts import CreateHasnStorageAccountsParam, DeleteHasnStorageAccountsParam, UpdateHasnStorageAccountsParam
from backend.common.exception import errors
from backend.common.pagination import paging_data


class HasnStorageAccountsService:
    @staticmethod
    async def get(*, db: AsyncSession, pk: int) -> HasnStorageAccounts:
        """
        获取用户云存储账户投影

        :param db: 数据库会话
        :param pk: 用户云存储账户投影 ID
        :return:
        """
        hasn_storage_accounts = await hasn_storage_accounts_dao.get(db, pk)
        if not hasn_storage_accounts:
            raise errors.NotFoundError(msg='用户云存储账户投影不存在')
        return hasn_storage_accounts

    @staticmethod
    async def get_list(db: AsyncSession) -> dict[str, Any]:
        """
        获取用户云存储账户投影列表

        :param db: 数据库会话
        :return:
        """
        hasn_storage_accounts_select = await hasn_storage_accounts_dao.get_select()
        return await paging_data(db, hasn_storage_accounts_select)

    @staticmethod
    async def get_all(*, db: AsyncSession) -> Sequence[HasnStorageAccounts]:
        """
        获取所有用户云存储账户投影

        :param db: 数据库会话
        :return:
        """
        hasn_storage_accounts_list = await hasn_storage_accounts_dao.get_all(db)
        return hasn_storage_accounts_list

    @staticmethod
    async def create(*, db: AsyncSession, obj: CreateHasnStorageAccountsParam) -> None:
        """
        创建用户云存储账户投影

        :param db: 数据库会话
        :param obj: 创建用户云存储账户投影参数
        :return:
        """
        await hasn_storage_accounts_dao.create(db, obj)

    @staticmethod
    async def update(*, db: AsyncSession, pk: int, obj: UpdateHasnStorageAccountsParam) -> int:
        """
        更新用户云存储账户投影

        :param db: 数据库会话
        :param pk: 用户云存储账户投影 ID
        :param obj: 更新用户云存储账户投影参数
        :return:
        """
        count = await hasn_storage_accounts_dao.update(db, pk, obj)
        return count

    @staticmethod
    async def delete(*, db: AsyncSession, obj: DeleteHasnStorageAccountsParam) -> int:
        """
        删除用户云存储账户投影

        :param db: 数据库会话
        :param obj: 用户云存储账户投影 ID 列表
        :return:
        """
        count = await hasn_storage_accounts_dao.delete(db, obj.pks)
        return count


hasn_storage_accounts_service: HasnStorageAccountsService = HasnStorageAccountsService()
