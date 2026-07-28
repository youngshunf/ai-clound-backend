from typing import Any, Sequence

from sqlalchemy.ext.asyncio import AsyncSession

from backend.app.hasn.crud.crud_hasn_storage_migration_items import hasn_storage_migration_items_dao
from backend.app.hasn.model import HasnStorageMigrationItems
from backend.app.hasn.schema.hasn_storage_migration_items import CreateHasnStorageMigrationItemsParam, DeleteHasnStorageMigrationItemsParam, UpdateHasnStorageMigrationItemsParam
from backend.common.exception import errors
from backend.common.pagination import paging_data


class HasnStorageMigrationItemsService:
    @staticmethod
    async def get(*, db: AsyncSession, pk: int) -> HasnStorageMigrationItems:
        """
        获取用户云存储迁移逐对象明细

        :param db: 数据库会话
        :param pk: 用户云存储迁移逐对象明细 ID
        :return:
        """
        hasn_storage_migration_items = await hasn_storage_migration_items_dao.get(db, pk)
        if not hasn_storage_migration_items:
            raise errors.NotFoundError(msg='用户云存储迁移逐对象明细不存在')
        return hasn_storage_migration_items

    @staticmethod
    async def get_list(db: AsyncSession) -> dict[str, Any]:
        """
        获取用户云存储迁移逐对象明细列表

        :param db: 数据库会话
        :return:
        """
        hasn_storage_migration_items_select = await hasn_storage_migration_items_dao.get_select()
        return await paging_data(db, hasn_storage_migration_items_select)

    @staticmethod
    async def get_all(*, db: AsyncSession) -> Sequence[HasnStorageMigrationItems]:
        """
        获取所有用户云存储迁移逐对象明细

        :param db: 数据库会话
        :return:
        """
        hasn_storage_migration_items_list = await hasn_storage_migration_items_dao.get_all(db)
        return hasn_storage_migration_items_list

    @staticmethod
    async def create(*, db: AsyncSession, obj: CreateHasnStorageMigrationItemsParam) -> None:
        """
        创建用户云存储迁移逐对象明细

        :param db: 数据库会话
        :param obj: 创建用户云存储迁移逐对象明细参数
        :return:
        """
        await hasn_storage_migration_items_dao.create(db, obj)

    @staticmethod
    async def update(*, db: AsyncSession, pk: int, obj: UpdateHasnStorageMigrationItemsParam) -> int:
        """
        更新用户云存储迁移逐对象明细

        :param db: 数据库会话
        :param pk: 用户云存储迁移逐对象明细 ID
        :param obj: 更新用户云存储迁移逐对象明细参数
        :return:
        """
        count = await hasn_storage_migration_items_dao.update(db, pk, obj)
        return count

    @staticmethod
    async def delete(*, db: AsyncSession, obj: DeleteHasnStorageMigrationItemsParam) -> int:
        """
        删除用户云存储迁移逐对象明细

        :param db: 数据库会话
        :param obj: 用户云存储迁移逐对象明细 ID 列表
        :return:
        """
        count = await hasn_storage_migration_items_dao.delete(db, obj.pks)
        return count


hasn_storage_migration_items_service: HasnStorageMigrationItemsService = HasnStorageMigrationItemsService()
