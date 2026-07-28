from typing import Any, Sequence

from sqlalchemy.ext.asyncio import AsyncSession

from backend.app.hasn.crud.crud_hasn_storage_reservations import hasn_storage_reservations_dao
from backend.app.hasn.model import HasnStorageReservations
from backend.app.hasn.schema.hasn_storage_reservations import CreateHasnStorageReservationsParam, DeleteHasnStorageReservationsParam, UpdateHasnStorageReservationsParam
from backend.common.exception import errors
from backend.common.pagination import paging_data


class HasnStorageReservationsService:
    @staticmethod
    async def get(*, db: AsyncSession, pk: int) -> HasnStorageReservations:
        """
        获取用户云存储上传预占记录

        :param db: 数据库会话
        :param pk: 用户云存储上传预占记录 ID
        :return:
        """
        hasn_storage_reservations = await hasn_storage_reservations_dao.get(db, pk)
        if not hasn_storage_reservations:
            raise errors.NotFoundError(msg='用户云存储上传预占记录不存在')
        return hasn_storage_reservations

    @staticmethod
    async def get_list(db: AsyncSession) -> dict[str, Any]:
        """
        获取用户云存储上传预占记录列表

        :param db: 数据库会话
        :return:
        """
        hasn_storage_reservations_select = await hasn_storage_reservations_dao.get_select()
        return await paging_data(db, hasn_storage_reservations_select)

    @staticmethod
    async def get_all(*, db: AsyncSession) -> Sequence[HasnStorageReservations]:
        """
        获取所有用户云存储上传预占记录

        :param db: 数据库会话
        :return:
        """
        hasn_storage_reservations_list = await hasn_storage_reservations_dao.get_all(db)
        return hasn_storage_reservations_list

    @staticmethod
    async def create(*, db: AsyncSession, obj: CreateHasnStorageReservationsParam) -> None:
        """
        创建用户云存储上传预占记录

        :param db: 数据库会话
        :param obj: 创建用户云存储上传预占记录参数
        :return:
        """
        await hasn_storage_reservations_dao.create(db, obj)

    @staticmethod
    async def update(*, db: AsyncSession, pk: int, obj: UpdateHasnStorageReservationsParam) -> int:
        """
        更新用户云存储上传预占记录

        :param db: 数据库会话
        :param pk: 用户云存储上传预占记录 ID
        :param obj: 更新用户云存储上传预占记录参数
        :return:
        """
        count = await hasn_storage_reservations_dao.update(db, pk, obj)
        return count

    @staticmethod
    async def delete(*, db: AsyncSession, obj: DeleteHasnStorageReservationsParam) -> int:
        """
        删除用户云存储上传预占记录

        :param db: 数据库会话
        :param obj: 用户云存储上传预占记录 ID 列表
        :return:
        """
        count = await hasn_storage_reservations_dao.delete(db, obj.pks)
        return count


hasn_storage_reservations_service: HasnStorageReservationsService = HasnStorageReservationsService()
