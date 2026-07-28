from typing import Any, Sequence

from sqlalchemy.ext.asyncio import AsyncSession

from backend.app.hasn.crud.crud_hasn_storage_objects import hasn_storage_objects_dao
from backend.app.hasn.model import HasnStorageObjects
from backend.app.hasn.schema.hasn_storage_objects import CreateHasnStorageObjectsParam, DeleteHasnStorageObjectsParam, UpdateHasnStorageObjectsParam
from backend.common.exception import errors
from backend.common.pagination import paging_data


class HasnStorageObjectsService:
    @staticmethod
    async def get(*, db: AsyncSession, pk: int) -> HasnStorageObjects:
        """
        获取用户云存储物理对象

        :param db: 数据库会话
        :param pk: 用户云存储物理对象 ID
        :return:
        """
        hasn_storage_objects = await hasn_storage_objects_dao.get(db, pk)
        if not hasn_storage_objects:
            raise errors.NotFoundError(msg='用户云存储物理对象不存在')
        return hasn_storage_objects

    @staticmethod
    async def get_list(db: AsyncSession) -> dict[str, Any]:
        """
        获取用户云存储物理对象列表

        :param db: 数据库会话
        :return:
        """
        hasn_storage_objects_select = await hasn_storage_objects_dao.get_select()
        return await paging_data(db, hasn_storage_objects_select)

    @staticmethod
    async def get_all(*, db: AsyncSession) -> Sequence[HasnStorageObjects]:
        """
        获取所有用户云存储物理对象

        :param db: 数据库会话
        :return:
        """
        hasn_storage_objects_list = await hasn_storage_objects_dao.get_all(db)
        return hasn_storage_objects_list

    @staticmethod
    async def create(*, db: AsyncSession, obj: CreateHasnStorageObjectsParam) -> None:
        """
        创建用户云存储物理对象

        :param db: 数据库会话
        :param obj: 创建用户云存储物理对象参数
        :return:
        """
        await hasn_storage_objects_dao.create(db, obj)

    @staticmethod
    async def update(*, db: AsyncSession, pk: int, obj: UpdateHasnStorageObjectsParam) -> int:
        """
        更新用户云存储物理对象

        :param db: 数据库会话
        :param pk: 用户云存储物理对象 ID
        :param obj: 更新用户云存储物理对象参数
        :return:
        """
        count = await hasn_storage_objects_dao.update(db, pk, obj)
        return count

    @staticmethod
    async def delete(*, db: AsyncSession, obj: DeleteHasnStorageObjectsParam) -> int:
        """
        删除用户云存储物理对象

        :param db: 数据库会话
        :param obj: 用户云存储物理对象 ID 列表
        :return:
        """
        count = await hasn_storage_objects_dao.delete(db, obj.pks)
        return count


hasn_storage_objects_service: HasnStorageObjectsService = HasnStorageObjectsService()
