from typing import Any, Sequence

from sqlalchemy.ext.asyncio import AsyncSession

from backend.app.hasn.crud.crud_hasn_storage_entries import hasn_storage_entries_dao
from backend.app.hasn.model import HasnStorageEntries
from backend.app.hasn.schema.hasn_storage_entries import CreateHasnStorageEntriesParam, DeleteHasnStorageEntriesParam, UpdateHasnStorageEntriesParam
from backend.common.exception import errors
from backend.common.pagination import paging_data


class HasnStorageEntriesService:
    @staticmethod
    async def get(*, db: AsyncSession, pk: int) -> HasnStorageEntries:
        """
        获取用户云存储逻辑目录项

        :param db: 数据库会话
        :param pk: 用户云存储逻辑目录项 ID
        :return:
        """
        hasn_storage_entries = await hasn_storage_entries_dao.get(db, pk)
        if not hasn_storage_entries:
            raise errors.NotFoundError(msg='用户云存储逻辑目录项不存在')
        return hasn_storage_entries

    @staticmethod
    async def get_list(db: AsyncSession) -> dict[str, Any]:
        """
        获取用户云存储逻辑目录项列表

        :param db: 数据库会话
        :return:
        """
        hasn_storage_entries_select = await hasn_storage_entries_dao.get_select()
        return await paging_data(db, hasn_storage_entries_select)

    @staticmethod
    async def get_all(*, db: AsyncSession) -> Sequence[HasnStorageEntries]:
        """
        获取所有用户云存储逻辑目录项

        :param db: 数据库会话
        :return:
        """
        hasn_storage_entries_list = await hasn_storage_entries_dao.get_all(db)
        return hasn_storage_entries_list

    @staticmethod
    async def create(*, db: AsyncSession, obj: CreateHasnStorageEntriesParam) -> None:
        """
        创建用户云存储逻辑目录项

        :param db: 数据库会话
        :param obj: 创建用户云存储逻辑目录项参数
        :return:
        """
        await hasn_storage_entries_dao.create(db, obj)

    @staticmethod
    async def update(*, db: AsyncSession, pk: int, obj: UpdateHasnStorageEntriesParam) -> int:
        """
        更新用户云存储逻辑目录项

        :param db: 数据库会话
        :param pk: 用户云存储逻辑目录项 ID
        :param obj: 更新用户云存储逻辑目录项参数
        :return:
        """
        count = await hasn_storage_entries_dao.update(db, pk, obj)
        return count

    @staticmethod
    async def delete(*, db: AsyncSession, obj: DeleteHasnStorageEntriesParam) -> int:
        """
        删除用户云存储逻辑目录项

        :param db: 数据库会话
        :param obj: 用户云存储逻辑目录项 ID 列表
        :return:
        """
        count = await hasn_storage_entries_dao.delete(db, obj.pks)
        return count


hasn_storage_entries_service: HasnStorageEntriesService = HasnStorageEntriesService()
