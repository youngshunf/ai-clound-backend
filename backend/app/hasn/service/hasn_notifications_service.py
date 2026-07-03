from collections.abc import Sequence
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from backend.app.hasn.crud.crud_hasn_notifications import hasn_notifications_dao
from backend.app.hasn.model import HasnNotifications
from backend.app.hasn.schema.hasn_notifications import (
    CreateHasnNotificationsParam,
    DeleteHasnNotificationsParam,
    UpdateHasnNotificationsParam,
)
from backend.common.exception import errors
from backend.common.pagination import paging_data


class HasnNotificationsService:
    @staticmethod
    async def get(*, db: AsyncSession, pk: int) -> HasnNotifications:
        """
        获取HASN 通知队列

        :param db: 数据库会话
        :param pk: HASN 通知队列 ID
        :return:
        """
        hasn_notifications = await hasn_notifications_dao.get(db, pk)
        if not hasn_notifications:
            raise errors.NotFoundError(msg='HASN 通知队列不存在')
        return hasn_notifications

    @staticmethod
    async def get_list(db: AsyncSession) -> dict[str, Any]:
        """
        获取HASN 通知队列列表

        :param db: 数据库会话
        :return:
        """
        hasn_notifications_select = await hasn_notifications_dao.get_select()
        return await paging_data(db, hasn_notifications_select)

    @staticmethod
    async def get_all(*, db: AsyncSession) -> Sequence[HasnNotifications]:
        """
        获取所有HASN 通知队列

        :param db: 数据库会话
        :return:
        """
        hasn_notificationss = await hasn_notifications_dao.get_all(db)
        return hasn_notificationss

    @staticmethod
    async def create(*, db: AsyncSession, obj: CreateHasnNotificationsParam) -> None:
        """
        创建HASN 通知队列

        :param db: 数据库会话
        :param obj: 创建HASN 通知队列参数
        :return:
        """
        await hasn_notifications_dao.create(db, obj)

    @staticmethod
    async def update(*, db: AsyncSession, pk: int, obj: UpdateHasnNotificationsParam) -> int:
        """
        更新HASN 通知队列

        :param db: 数据库会话
        :param pk: HASN 通知队列 ID
        :param obj: 更新HASN 通知队列参数
        :return:
        """
        count = await hasn_notifications_dao.update(db, pk, obj)
        return count

    @staticmethod
    async def delete(*, db: AsyncSession, obj: DeleteHasnNotificationsParam) -> int:
        """
        删除HASN 通知队列

        :param db: 数据库会话
        :param obj: HASN 通知队列 ID 列表
        :return:
        """
        count = await hasn_notifications_dao.delete(db, obj.pks)
        return count


hasn_notifications_service: HasnNotificationsService = HasnNotificationsService()
