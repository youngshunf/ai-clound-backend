from typing import Sequence

from sqlalchemy import Select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy_crud_plus import CRUDPlus

from backend.app.hasn.model import HasnGroupImCommandOutbox
from backend.app.hasn.schema.hasn_group_im_command_outbox import CreateHasnGroupImCommandOutboxParam, UpdateHasnGroupImCommandOutboxParam


class CRUDHasnGroupImCommandOutbox(CRUDPlus[HasnGroupImCommandOutbox]):
    async def get(self, db: AsyncSession, pk: int) -> HasnGroupImCommandOutbox | None:
        """
        获取群邀请等群业务状态的事务 IM 命令队列

        :param db: 数据库会话
        :param pk: 群邀请等群业务状态的事务 IM 命令队列 ID
        :return:
        """
        return await self.select_model(db, pk)

    async def get_select(self) -> Select:
        """获取群邀请等群业务状态的事务 IM 命令队列列表查询表达式"""
        return await self.select_order('id', 'desc')

    async def get_all(self, db: AsyncSession) -> Sequence[HasnGroupImCommandOutbox]:
        """
        获取所有群邀请等群业务状态的事务 IM 命令队列

        :param db: 数据库会话
        :return:
        """
        return await self.select_models(db)

    async def create(self, db: AsyncSession, obj: CreateHasnGroupImCommandOutboxParam) -> None:
        """
        创建群邀请等群业务状态的事务 IM 命令队列

        :param db: 数据库会话
        :param obj: 创建群邀请等群业务状态的事务 IM 命令队列参数
        :return:
        """
        await self.create_model(db, obj)

    async def update(self, db: AsyncSession, pk: int, obj: UpdateHasnGroupImCommandOutboxParam) -> int:
        """
        更新群邀请等群业务状态的事务 IM 命令队列

        :param db: 数据库会话
        :param pk: 群邀请等群业务状态的事务 IM 命令队列 ID
        :param obj: 更新 群邀请等群业务状态的事务 IM 命令队列参数
        :return:
        """
        return await self.update_model(db, pk, obj)

    async def delete(self, db: AsyncSession, pks: list[int]) -> int:
        """
        批量删除群邀请等群业务状态的事务 IM 命令队列

        :param db: 数据库会话
        :param pks: 群邀请等群业务状态的事务 IM 命令队列 ID 列表
        :return:
        """
        return await self.delete_model_by_column(db, allow_multiple=True, id__in=pks)


hasn_group_im_command_outbox_dao: CRUDHasnGroupImCommandOutbox = CRUDHasnGroupImCommandOutbox(HasnGroupImCommandOutbox)
