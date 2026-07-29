from typing import Any, Sequence

from sqlalchemy.ext.asyncio import AsyncSession

from backend.app.notification.crud.crud_hasn_notification_im_command_outbox import hasn_notification_im_command_outbox_dao
from backend.app.notification.model import HasnNotificationImCommandOutbox
from backend.app.notification.schema.hasn_notification_im_command_outbox import CreateHasnNotificationImCommandOutboxParam, DeleteHasnNotificationImCommandOutboxParam, UpdateHasnNotificationImCommandOutboxParam
from backend.common.exception import errors
from backend.common.pagination import paging_data


class HasnNotificationImCommandOutboxService:
    @staticmethod
    async def get(*, db: AsyncSession, pk: int) -> HasnNotificationImCommandOutbox:
        """
        获取通知业务状态触发 IM 卡片的事务命令队列

        :param db: 数据库会话
        :param pk: 通知业务状态触发 IM 卡片的事务命令队列 ID
        :return:
        """
        hasn_notification_im_command_outbox = await hasn_notification_im_command_outbox_dao.get(db, pk)
        if not hasn_notification_im_command_outbox:
            raise errors.NotFoundError(msg='通知业务状态触发 IM 卡片的事务命令队列不存在')
        return hasn_notification_im_command_outbox

    @staticmethod
    async def get_list(db: AsyncSession) -> dict[str, Any]:
        """
        获取通知业务状态触发 IM 卡片的事务命令队列列表

        :param db: 数据库会话
        :return:
        """
        hasn_notification_im_command_outbox_select = await hasn_notification_im_command_outbox_dao.get_select()
        return await paging_data(db, hasn_notification_im_command_outbox_select)

    @staticmethod
    async def get_all(*, db: AsyncSession) -> Sequence[HasnNotificationImCommandOutbox]:
        """
        获取所有通知业务状态触发 IM 卡片的事务命令队列

        :param db: 数据库会话
        :return:
        """
        hasn_notification_im_command_outbox_list = await hasn_notification_im_command_outbox_dao.get_all(db)
        return hasn_notification_im_command_outbox_list

    @staticmethod
    async def create(*, db: AsyncSession, obj: CreateHasnNotificationImCommandOutboxParam) -> None:
        """
        创建通知业务状态触发 IM 卡片的事务命令队列

        :param db: 数据库会话
        :param obj: 创建通知业务状态触发 IM 卡片的事务命令队列参数
        :return:
        """
        await hasn_notification_im_command_outbox_dao.create(db, obj)

    @staticmethod
    async def update(*, db: AsyncSession, pk: int, obj: UpdateHasnNotificationImCommandOutboxParam) -> int:
        """
        更新通知业务状态触发 IM 卡片的事务命令队列

        :param db: 数据库会话
        :param pk: 通知业务状态触发 IM 卡片的事务命令队列 ID
        :param obj: 更新通知业务状态触发 IM 卡片的事务命令队列参数
        :return:
        """
        count = await hasn_notification_im_command_outbox_dao.update(db, pk, obj)
        return count

    @staticmethod
    async def delete(*, db: AsyncSession, obj: DeleteHasnNotificationImCommandOutboxParam) -> int:
        """
        删除通知业务状态触发 IM 卡片的事务命令队列

        :param db: 数据库会话
        :param obj: 通知业务状态触发 IM 卡片的事务命令队列 ID 列表
        :return:
        """
        count = await hasn_notification_im_command_outbox_dao.delete(db, obj.pks)
        return count


hasn_notification_im_command_outbox_service: HasnNotificationImCommandOutboxService = HasnNotificationImCommandOutboxService()
