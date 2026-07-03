from collections.abc import Sequence
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from backend.app.hasn.crud.crud_hasn_clients import hasn_clients_dao
from backend.app.hasn.model import HasnClients
from backend.app.hasn.schema.hasn_clients import CreateHasnClientsParam, DeleteHasnClientsParam, UpdateHasnClientsParam
from backend.common.exception import errors
from backend.common.pagination import paging_data


class HasnClientsService:
    @staticmethod
    async def get(*, db: AsyncSession, pk: int) -> HasnClients:
        """
        获取HASN 客户端设备

        :param db: 数据库会话
        :param pk: HASN 客户端设备 ID
        :return:
        """
        hasn_clients = await hasn_clients_dao.get(db, pk)
        if not hasn_clients:
            raise errors.NotFoundError(msg='HASN 客户端设备不存在')
        return hasn_clients

    @staticmethod
    async def get_list(db: AsyncSession) -> dict[str, Any]:
        """
        获取HASN 客户端设备列表

        :param db: 数据库会话
        :return:
        """
        hasn_clients_select = await hasn_clients_dao.get_select()
        return await paging_data(db, hasn_clients_select)

    @staticmethod
    async def get_all(*, db: AsyncSession) -> Sequence[HasnClients]:
        """
        获取所有HASN 客户端设备

        :param db: 数据库会话
        :return:
        """
        hasn_clientss = await hasn_clients_dao.get_all(db)
        return hasn_clientss

    @staticmethod
    async def create(*, db: AsyncSession, obj: CreateHasnClientsParam) -> None:
        """
        创建HASN 客户端设备

        :param db: 数据库会话
        :param obj: 创建HASN 客户端设备参数
        :return:
        """
        await hasn_clients_dao.create(db, obj)

    @staticmethod
    async def update(*, db: AsyncSession, pk: int, obj: UpdateHasnClientsParam) -> int:
        """
        更新HASN 客户端设备

        :param db: 数据库会话
        :param pk: HASN 客户端设备 ID
        :param obj: 更新HASN 客户端设备参数
        :return:
        """
        count = await hasn_clients_dao.update(db, pk, obj)
        return count

    @staticmethod
    async def delete(*, db: AsyncSession, obj: DeleteHasnClientsParam) -> int:
        """
        删除HASN 客户端设备

        :param db: 数据库会话
        :param obj: HASN 客户端设备 ID 列表
        :return:
        """
        count = await hasn_clients_dao.delete(db, obj.pks)
        return count


hasn_clients_service: HasnClientsService = HasnClientsService()
