from typing import Any, Sequence

from sqlalchemy.ext.asyncio import AsyncSession

from backend.app.hasn.crud.crud_hasn_session_im_command_outbox import hasn_session_im_command_outbox_dao
from backend.app.hasn.model import HasnSessionImCommandOutbox
from backend.app.hasn.schema.hasn_session_im_command_outbox import CreateHasnSessionImCommandOutboxParam, DeleteHasnSessionImCommandOutboxParam, UpdateHasnSessionImCommandOutboxParam
from backend.common.exception import errors
from backend.common.pagination import paging_data


class HasnSessionImCommandOutboxService:
    @staticmethod
    async def get(*, db: AsyncSession, pk: int) -> HasnSessionImCommandOutbox:
        """
        获取工作会话结果与应用完成卡的事务 IM 命令队列

        :param db: 数据库会话
        :param pk: 工作会话结果与应用完成卡的事务 IM 命令队列 ID
        :return:
        """
        hasn_session_im_command_outbox = await hasn_session_im_command_outbox_dao.get(db, pk)
        if not hasn_session_im_command_outbox:
            raise errors.NotFoundError(msg='工作会话结果与应用完成卡的事务 IM 命令队列不存在')
        return hasn_session_im_command_outbox

    @staticmethod
    async def get_list(db: AsyncSession) -> dict[str, Any]:
        """
        获取工作会话结果与应用完成卡的事务 IM 命令队列列表

        :param db: 数据库会话
        :return:
        """
        hasn_session_im_command_outbox_select = await hasn_session_im_command_outbox_dao.get_select()
        return await paging_data(db, hasn_session_im_command_outbox_select)

    @staticmethod
    async def get_all(*, db: AsyncSession) -> Sequence[HasnSessionImCommandOutbox]:
        """
        获取所有工作会话结果与应用完成卡的事务 IM 命令队列

        :param db: 数据库会话
        :return:
        """
        hasn_session_im_command_outbox_list = await hasn_session_im_command_outbox_dao.get_all(db)
        return hasn_session_im_command_outbox_list

    @staticmethod
    async def create(*, db: AsyncSession, obj: CreateHasnSessionImCommandOutboxParam) -> None:
        """
        创建工作会话结果与应用完成卡的事务 IM 命令队列

        :param db: 数据库会话
        :param obj: 创建工作会话结果与应用完成卡的事务 IM 命令队列参数
        :return:
        """
        await hasn_session_im_command_outbox_dao.create(db, obj)

    @staticmethod
    async def update(*, db: AsyncSession, pk: int, obj: UpdateHasnSessionImCommandOutboxParam) -> int:
        """
        更新工作会话结果与应用完成卡的事务 IM 命令队列

        :param db: 数据库会话
        :param pk: 工作会话结果与应用完成卡的事务 IM 命令队列 ID
        :param obj: 更新工作会话结果与应用完成卡的事务 IM 命令队列参数
        :return:
        """
        count = await hasn_session_im_command_outbox_dao.update(db, pk, obj)
        return count

    @staticmethod
    async def delete(*, db: AsyncSession, obj: DeleteHasnSessionImCommandOutboxParam) -> int:
        """
        删除工作会话结果与应用完成卡的事务 IM 命令队列

        :param db: 数据库会话
        :param obj: 工作会话结果与应用完成卡的事务 IM 命令队列 ID 列表
        :return:
        """
        count = await hasn_session_im_command_outbox_dao.delete(db, obj.pks)
        return count


hasn_session_im_command_outbox_service: HasnSessionImCommandOutboxService = HasnSessionImCommandOutboxService()
