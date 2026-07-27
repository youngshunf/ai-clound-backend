from typing import Any, Sequence

from sqlalchemy.ext.asyncio import AsyncSession

from backend.app.hasn.crud.crud_hasn_group_im_command_outbox import hasn_group_im_command_outbox_dao
from backend.app.hasn.model import HasnGroupImCommandOutbox
from backend.app.hasn.schema.hasn_group_im_command_outbox import CreateHasnGroupImCommandOutboxParam, DeleteHasnGroupImCommandOutboxParam, UpdateHasnGroupImCommandOutboxParam
from backend.common.exception import errors
from backend.common.pagination import paging_data


class HasnGroupImCommandOutboxService:
    @staticmethod
    async def get(*, db: AsyncSession, pk: int) -> HasnGroupImCommandOutbox:
        """
        获取群邀请等群业务状态的事务 IM 命令队列

        :param db: 数据库会话
        :param pk: 群邀请等群业务状态的事务 IM 命令队列 ID
        :return:
        """
        hasn_group_im_command_outbox = await hasn_group_im_command_outbox_dao.get(db, pk)
        if not hasn_group_im_command_outbox:
            raise errors.NotFoundError(msg='群邀请等群业务状态的事务 IM 命令队列不存在')
        return hasn_group_im_command_outbox

    @staticmethod
    async def get_list(db: AsyncSession) -> dict[str, Any]:
        """
        获取群邀请等群业务状态的事务 IM 命令队列列表

        :param db: 数据库会话
        :return:
        """
        hasn_group_im_command_outbox_select = await hasn_group_im_command_outbox_dao.get_select()
        return await paging_data(db, hasn_group_im_command_outbox_select)

    @staticmethod
    async def get_all(*, db: AsyncSession) -> Sequence[HasnGroupImCommandOutbox]:
        """
        获取所有群邀请等群业务状态的事务 IM 命令队列

        :param db: 数据库会话
        :return:
        """
        hasn_group_im_command_outbox_list = await hasn_group_im_command_outbox_dao.get_all(db)
        return hasn_group_im_command_outbox_list

    @staticmethod
    async def create(*, db: AsyncSession, obj: CreateHasnGroupImCommandOutboxParam) -> None:
        """
        创建群邀请等群业务状态的事务 IM 命令队列

        :param db: 数据库会话
        :param obj: 创建群邀请等群业务状态的事务 IM 命令队列参数
        :return:
        """
        await hasn_group_im_command_outbox_dao.create(db, obj)

    @staticmethod
    async def update(*, db: AsyncSession, pk: int, obj: UpdateHasnGroupImCommandOutboxParam) -> int:
        """
        更新群邀请等群业务状态的事务 IM 命令队列

        :param db: 数据库会话
        :param pk: 群邀请等群业务状态的事务 IM 命令队列 ID
        :param obj: 更新群邀请等群业务状态的事务 IM 命令队列参数
        :return:
        """
        count = await hasn_group_im_command_outbox_dao.update(db, pk, obj)
        return count

    @staticmethod
    async def delete(*, db: AsyncSession, obj: DeleteHasnGroupImCommandOutboxParam) -> int:
        """
        删除群邀请等群业务状态的事务 IM 命令队列

        :param db: 数据库会话
        :param obj: 群邀请等群业务状态的事务 IM 命令队列 ID 列表
        :return:
        """
        count = await hasn_group_im_command_outbox_dao.delete(db, obj.pks)
        return count


hasn_group_im_command_outbox_service: HasnGroupImCommandOutboxService = HasnGroupImCommandOutboxService()
