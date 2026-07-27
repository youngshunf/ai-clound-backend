from typing import Any, Sequence

from sqlalchemy.ext.asyncio import AsyncSession

from backend.app.hasn_task.crud.crud_task_dispatch_outbox import task_dispatch_outbox_dao
from backend.app.hasn_task.model import TaskDispatchOutbox
from backend.app.hasn_task.schema.task_dispatch_outbox import CreateTaskDispatchOutboxParam, DeleteTaskDispatchOutboxParam, UpdateTaskDispatchOutboxParam
from backend.common.exception import errors
from backend.common.pagination import paging_data


class TaskDispatchOutboxService:
    @staticmethod
    async def get(*, db: AsyncSession, pk: int) -> TaskDispatchOutbox:
        """
        获取中心任务调度器向主人节点可靠投递任务执行帧的事务队列

        :param db: 数据库会话
        :param pk: 中心任务调度器向主人节点可靠投递任务执行帧的事务队列 ID
        :return:
        """
        task_dispatch_outbox = await task_dispatch_outbox_dao.get(db, pk)
        if not task_dispatch_outbox:
            raise errors.NotFoundError(msg='中心任务调度器向主人节点可靠投递任务执行帧的事务队列不存在')
        return task_dispatch_outbox

    @staticmethod
    async def get_list(db: AsyncSession) -> dict[str, Any]:
        """
        获取中心任务调度器向主人节点可靠投递任务执行帧的事务队列列表

        :param db: 数据库会话
        :return:
        """
        task_dispatch_outbox_select = await task_dispatch_outbox_dao.get_select()
        return await paging_data(db, task_dispatch_outbox_select)

    @staticmethod
    async def get_all(*, db: AsyncSession) -> Sequence[TaskDispatchOutbox]:
        """
        获取所有中心任务调度器向主人节点可靠投递任务执行帧的事务队列

        :param db: 数据库会话
        :return:
        """
        task_dispatch_outbox_list = await task_dispatch_outbox_dao.get_all(db)
        return task_dispatch_outbox_list

    @staticmethod
    async def create(*, db: AsyncSession, obj: CreateTaskDispatchOutboxParam) -> None:
        """
        创建中心任务调度器向主人节点可靠投递任务执行帧的事务队列

        :param db: 数据库会话
        :param obj: 创建中心任务调度器向主人节点可靠投递任务执行帧的事务队列参数
        :return:
        """
        await task_dispatch_outbox_dao.create(db, obj)

    @staticmethod
    async def update(*, db: AsyncSession, pk: int, obj: UpdateTaskDispatchOutboxParam) -> int:
        """
        更新中心任务调度器向主人节点可靠投递任务执行帧的事务队列

        :param db: 数据库会话
        :param pk: 中心任务调度器向主人节点可靠投递任务执行帧的事务队列 ID
        :param obj: 更新中心任务调度器向主人节点可靠投递任务执行帧的事务队列参数
        :return:
        """
        count = await task_dispatch_outbox_dao.update(db, pk, obj)
        return count

    @staticmethod
    async def delete(*, db: AsyncSession, obj: DeleteTaskDispatchOutboxParam) -> int:
        """
        删除中心任务调度器向主人节点可靠投递任务执行帧的事务队列

        :param db: 数据库会话
        :param obj: 中心任务调度器向主人节点可靠投递任务执行帧的事务队列 ID 列表
        :return:
        """
        count = await task_dispatch_outbox_dao.delete(db, obj.pks)
        return count


task_dispatch_outbox_service: TaskDispatchOutboxService = TaskDispatchOutboxService()
