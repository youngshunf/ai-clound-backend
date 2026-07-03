from collections.abc import Sequence
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from backend.app.hasn.crud.crud_hasn_messages import hasn_messages_dao
from backend.app.hasn.model import HasnMessages
from backend.app.hasn.schema.hasn_messages import (
    CreateHasnMessagesParam,
    DeleteHasnMessagesParam,
    UpdateHasnMessagesParam,
)
from backend.common.exception import errors
from backend.common.pagination import paging_data


class HasnMessagesService:
    @staticmethod
    async def get(*, db: AsyncSession, pk: int) -> HasnMessages:
        """
        获取HASN 消息

        :param db: 数据库会话
        :param pk: HASN 消息 ID
        :return:
        """
        hasn_messages = await hasn_messages_dao.get(db, pk)
        if not hasn_messages:
            raise errors.NotFoundError(msg='HASN 消息不存在')
        return hasn_messages

    @staticmethod
    async def get_list(db: AsyncSession) -> dict[str, Any]:
        """
        获取HASN 消息列表

        :param db: 数据库会话
        :return:
        """
        hasn_messages_select = await hasn_messages_dao.get_select()
        return await paging_data(db, hasn_messages_select)

    @staticmethod
    async def get_all(*, db: AsyncSession) -> Sequence[HasnMessages]:
        """
        获取所有HASN 消息

        :param db: 数据库会话
        :return:
        """
        hasn_messagess = await hasn_messages_dao.get_all(db)
        return hasn_messagess

    @staticmethod
    async def create(*, db: AsyncSession, obj: CreateHasnMessagesParam) -> None:
        """
        创建HASN 消息

        :param db: 数据库会话
        :param obj: 创建HASN 消息参数
        :return:
        """
        await hasn_messages_dao.create(db, obj)

    @staticmethod
    async def update(*, db: AsyncSession, pk: int, obj: UpdateHasnMessagesParam) -> int:
        """
        更新HASN 消息

        :param db: 数据库会话
        :param pk: HASN 消息 ID
        :param obj: 更新HASN 消息参数
        :return:
        """
        count = await hasn_messages_dao.update(db, pk, obj)
        return count

    @staticmethod
    async def delete(*, db: AsyncSession, obj: DeleteHasnMessagesParam) -> int:
        """
        删除HASN 消息

        :param db: 数据库会话
        :param obj: HASN 消息 ID 列表
        :return:
        """
        count = await hasn_messages_dao.delete(db, obj.pks)
        return count


hasn_messages_service: HasnMessagesService = HasnMessagesService()
