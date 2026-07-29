from typing import Any, Sequence

from sqlalchemy.ext.asyncio import AsyncSession

from backend.app.hasn.crud.crud_hasn_storage_export_items import hasn_storage_export_items_dao
from backend.app.hasn.model import HasnStorageExportItems
from backend.app.hasn.schema.hasn_storage_export_items import CreateHasnStorageExportItemsParam, DeleteHasnStorageExportItemsParam, UpdateHasnStorageExportItemsParam
from backend.common.exception import errors
from backend.common.pagination import paging_data


class HasnStorageExportItemsService:
    @staticmethod
    async def get(*, db: AsyncSession, pk: int) -> HasnStorageExportItems:
        """
        获取用户云存储导出逐资产不可变快照

        :param db: 数据库会话
        :param pk: 用户云存储导出逐资产不可变快照 ID
        :return:
        """
        hasn_storage_export_items = await hasn_storage_export_items_dao.get(db, pk)
        if not hasn_storage_export_items:
            raise errors.NotFoundError(msg='用户云存储导出逐资产不可变快照不存在')
        return hasn_storage_export_items

    @staticmethod
    async def get_list(db: AsyncSession) -> dict[str, Any]:
        """
        获取用户云存储导出逐资产不可变快照列表

        :param db: 数据库会话
        :return:
        """
        hasn_storage_export_items_select = await hasn_storage_export_items_dao.get_select()
        return await paging_data(db, hasn_storage_export_items_select)

    @staticmethod
    async def get_all(*, db: AsyncSession) -> Sequence[HasnStorageExportItems]:
        """
        获取所有用户云存储导出逐资产不可变快照

        :param db: 数据库会话
        :return:
        """
        hasn_storage_export_items_list = await hasn_storage_export_items_dao.get_all(db)
        return hasn_storage_export_items_list

    @staticmethod
    async def create(*, db: AsyncSession, obj: CreateHasnStorageExportItemsParam) -> None:
        """
        创建用户云存储导出逐资产不可变快照

        :param db: 数据库会话
        :param obj: 创建用户云存储导出逐资产不可变快照参数
        :return:
        """
        await hasn_storage_export_items_dao.create(db, obj)

    @staticmethod
    async def update(*, db: AsyncSession, pk: int, obj: UpdateHasnStorageExportItemsParam) -> int:
        """
        更新用户云存储导出逐资产不可变快照

        :param db: 数据库会话
        :param pk: 用户云存储导出逐资产不可变快照 ID
        :param obj: 更新用户云存储导出逐资产不可变快照参数
        :return:
        """
        count = await hasn_storage_export_items_dao.update(db, pk, obj)
        return count

    @staticmethod
    async def delete(*, db: AsyncSession, obj: DeleteHasnStorageExportItemsParam) -> int:
        """
        删除用户云存储导出逐资产不可变快照

        :param db: 数据库会话
        :param obj: 用户云存储导出逐资产不可变快照 ID 列表
        :return:
        """
        count = await hasn_storage_export_items_dao.delete(db, obj.pks)
        return count


hasn_storage_export_items_service: HasnStorageExportItemsService = HasnStorageExportItemsService()
