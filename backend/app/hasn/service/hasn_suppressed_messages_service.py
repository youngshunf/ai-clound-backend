from collections.abc import Sequence
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from backend.app.hasn.crud.crud_hasn_suppressed_messages import hasn_suppressed_messages_dao
from backend.app.hasn.model import HasnSuppressedMessages
from backend.app.hasn.schema.hasn_suppressed_messages import (
    CreateHasnSuppressedMessagesParam,
    DeleteHasnSuppressedMessagesParam,
    UpdateHasnSuppressedMessagesParam,
)
from backend.common.exception import errors
from backend.common.pagination import paging_data


class HasnSuppressedMessagesService:
    @staticmethod
    async def get(*, db: AsyncSession, pk: int) -> HasnSuppressedMessages:
        """
        获取HASN Runtime 抑制箱 / owner 可拉取消息

        :param db: 数据库会话
        :param pk: HASN Runtime 抑制箱 / owner 可拉取消息 ID
        :return:
        """
        hasn_suppressed_messages = await hasn_suppressed_messages_dao.get(db, pk)
        if not hasn_suppressed_messages:
            raise errors.NotFoundError(msg='HASN Runtime 抑制箱 / owner 可拉取消息不存在')
        return hasn_suppressed_messages

    @staticmethod
    async def get_list(db: AsyncSession) -> dict[str, Any]:
        """
        获取HASN Runtime 抑制箱 / owner 可拉取消息列表

        :param db: 数据库会话
        :return:
        """
        hasn_suppressed_messages_select = await hasn_suppressed_messages_dao.get_select()
        return await paging_data(db, hasn_suppressed_messages_select)

    @staticmethod
    async def get_all(*, db: AsyncSession) -> Sequence[HasnSuppressedMessages]:
        """
        获取所有HASN Runtime 抑制箱 / owner 可拉取消息

        :param db: 数据库会话
        :return:
        """
        hasn_suppressed_messagess = await hasn_suppressed_messages_dao.get_all(db)
        return hasn_suppressed_messagess

    @staticmethod
    async def create(*, db: AsyncSession, obj: CreateHasnSuppressedMessagesParam) -> None:
        """
        创建HASN Runtime 抑制箱 / owner 可拉取消息

        :param db: 数据库会话
        :param obj: 创建HASN Runtime 抑制箱 / owner 可拉取消息参数
        :return:
        """
        await hasn_suppressed_messages_dao.create(db, obj)

    @staticmethod
    async def update(*, db: AsyncSession, pk: int, obj: UpdateHasnSuppressedMessagesParam) -> int:
        """
        更新HASN Runtime 抑制箱 / owner 可拉取消息

        :param db: 数据库会话
        :param pk: HASN Runtime 抑制箱 / owner 可拉取消息 ID
        :param obj: 更新HASN Runtime 抑制箱 / owner 可拉取消息参数
        :return:
        """
        count = await hasn_suppressed_messages_dao.update(db, pk, obj)
        return count

    @staticmethod
    async def delete(*, db: AsyncSession, obj: DeleteHasnSuppressedMessagesParam) -> int:
        """
        删除HASN Runtime 抑制箱 / owner 可拉取消息

        :param db: 数据库会话
        :param obj: HASN Runtime 抑制箱 / owner 可拉取消息 ID 列表
        :return:
        """
        count = await hasn_suppressed_messages_dao.delete(db, obj.pks)
        return count


hasn_suppressed_messages_service: HasnSuppressedMessagesService = HasnSuppressedMessagesService()
