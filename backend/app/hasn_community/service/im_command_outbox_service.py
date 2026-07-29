from typing import Any, Sequence

from sqlalchemy.ext.asyncio import AsyncSession

from backend.app.hasn_community.crud.crud_im_command_outbox import im_command_outbox_dao
from backend.app.hasn_community.model import ImCommandOutbox
from backend.app.hasn_community.schema.im_command_outbox import CreateImCommandOutboxParam, DeleteImCommandOutboxParam, UpdateImCommandOutboxParam
from backend.common.exception import errors
from backend.common.pagination import paging_data


class ImCommandOutboxService:
    @staticmethod
    async def get(*, db: AsyncSession, pk: int) -> ImCommandOutbox:
        """
        获取社区资源写入触发主人知情卡的事务命令队列

        :param db: 数据库会话
        :param pk: 社区资源写入触发主人知情卡的事务命令队列 ID
        :return:
        """
        im_command_outbox = await im_command_outbox_dao.get(db, pk)
        if not im_command_outbox:
            raise errors.NotFoundError(msg='社区资源写入触发主人知情卡的事务命令队列不存在')
        return im_command_outbox

    @staticmethod
    async def get_list(db: AsyncSession) -> dict[str, Any]:
        """
        获取社区资源写入触发主人知情卡的事务命令队列列表

        :param db: 数据库会话
        :return:
        """
        im_command_outbox_select = await im_command_outbox_dao.get_select()
        return await paging_data(db, im_command_outbox_select)

    @staticmethod
    async def get_all(*, db: AsyncSession) -> Sequence[ImCommandOutbox]:
        """
        获取所有社区资源写入触发主人知情卡的事务命令队列

        :param db: 数据库会话
        :return:
        """
        im_command_outbox_list = await im_command_outbox_dao.get_all(db)
        return im_command_outbox_list

    @staticmethod
    async def create(*, db: AsyncSession, obj: CreateImCommandOutboxParam) -> None:
        """
        创建社区资源写入触发主人知情卡的事务命令队列

        :param db: 数据库会话
        :param obj: 创建社区资源写入触发主人知情卡的事务命令队列参数
        :return:
        """
        await im_command_outbox_dao.create(db, obj)

    @staticmethod
    async def update(*, db: AsyncSession, pk: int, obj: UpdateImCommandOutboxParam) -> int:
        """
        更新社区资源写入触发主人知情卡的事务命令队列

        :param db: 数据库会话
        :param pk: 社区资源写入触发主人知情卡的事务命令队列 ID
        :param obj: 更新社区资源写入触发主人知情卡的事务命令队列参数
        :return:
        """
        count = await im_command_outbox_dao.update(db, pk, obj)
        return count

    @staticmethod
    async def delete(*, db: AsyncSession, obj: DeleteImCommandOutboxParam) -> int:
        """
        删除社区资源写入触发主人知情卡的事务命令队列

        :param db: 数据库会话
        :param obj: 社区资源写入触发主人知情卡的事务命令队列 ID 列表
        :return:
        """
        count = await im_command_outbox_dao.delete(db, obj.pks)
        return count


im_command_outbox_service: ImCommandOutboxService = ImCommandOutboxService()
