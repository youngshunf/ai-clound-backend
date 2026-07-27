from typing import Sequence

from sqlalchemy import Select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy_crud_plus import CRUDPlus

from backend.app.notification.model import HasnNotificationImCommandOutbox
from backend.app.notification.schema.hasn_notification_im_command_outbox import CreateHasnNotificationImCommandOutboxParam, UpdateHasnNotificationImCommandOutboxParam


class CRUDHasnNotificationImCommandOutbox(CRUDPlus[HasnNotificationImCommandOutbox]):
    async def get(self, db: AsyncSession, pk: int) -> HasnNotificationImCommandOutbox | None:
        """
        获取通知业务状态触发 IM 卡片的事务命令队列

        :param db: 数据库会话
        :param pk: 通知业务状态触发 IM 卡片的事务命令队列 ID
        :return:
        """
        return await self.select_model(db, pk)

    async def get_select(self) -> Select:
        """获取通知业务状态触发 IM 卡片的事务命令队列列表查询表达式"""
        return await self.select_order('id', 'desc')

    async def get_all(self, db: AsyncSession) -> Sequence[HasnNotificationImCommandOutbox]:
        """
        获取所有通知业务状态触发 IM 卡片的事务命令队列

        :param db: 数据库会话
        :return:
        """
        return await self.select_models(db)

    async def create(self, db: AsyncSession, obj: CreateHasnNotificationImCommandOutboxParam) -> None:
        """
        创建通知业务状态触发 IM 卡片的事务命令队列

        :param db: 数据库会话
        :param obj: 创建通知业务状态触发 IM 卡片的事务命令队列参数
        :return:
        """
        await self.create_model(db, obj)

    async def update(self, db: AsyncSession, pk: int, obj: UpdateHasnNotificationImCommandOutboxParam) -> int:
        """
        更新通知业务状态触发 IM 卡片的事务命令队列

        :param db: 数据库会话
        :param pk: 通知业务状态触发 IM 卡片的事务命令队列 ID
        :param obj: 更新 通知业务状态触发 IM 卡片的事务命令队列参数
        :return:
        """
        return await self.update_model(db, pk, obj)

    async def delete(self, db: AsyncSession, pks: list[int]) -> int:
        """
        批量删除通知业务状态触发 IM 卡片的事务命令队列

        :param db: 数据库会话
        :param pks: 通知业务状态触发 IM 卡片的事务命令队列 ID 列表
        :return:
        """
        return await self.delete_model_by_column(db, allow_multiple=True, id__in=pks)


hasn_notification_im_command_outbox_dao: CRUDHasnNotificationImCommandOutbox = CRUDHasnNotificationImCommandOutbox(HasnNotificationImCommandOutbox)
