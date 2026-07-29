from typing import Sequence, cast

from sqlalchemy import Select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy_crud_plus import CRUDPlus

from backend.app.hasn_task.model import TaskDispatchOutbox
from backend.app.hasn_task.schema.task_dispatch_outbox import CreateTaskDispatchOutboxParam, UpdateTaskDispatchOutboxParam


class CRUDTaskDispatchOutbox(CRUDPlus[TaskDispatchOutbox]):
    async def get(self, db: AsyncSession, pk: int) -> TaskDispatchOutbox | None:
        """
        获取中心任务调度器向主人节点可靠投递任务执行帧的事务队列

        :param db: 数据库会话
        :param pk: 中心任务调度器向主人节点可靠投递任务执行帧的事务队列 ID
        :return:
        """
        return cast(TaskDispatchOutbox | None, await self.select_model(db, pk))

    async def get_select(self) -> Select:
        """获取中心任务调度器向主人节点可靠投递任务执行帧的事务队列列表查询表达式"""
        return await self.select_order('id', 'desc')

    async def get_all(self, db: AsyncSession) -> Sequence[TaskDispatchOutbox]:
        """
        获取所有中心任务调度器向主人节点可靠投递任务执行帧的事务队列

        :param db: 数据库会话
        :return:
        """
        return cast(Sequence[TaskDispatchOutbox], await self.select_models(db))

    async def create(self, db: AsyncSession, obj: CreateTaskDispatchOutboxParam) -> None:
        """
        创建中心任务调度器向主人节点可靠投递任务执行帧的事务队列

        :param db: 数据库会话
        :param obj: 创建中心任务调度器向主人节点可靠投递任务执行帧的事务队列参数
        :return:
        """
        await self.create_model(db, obj)

    async def update(self, db: AsyncSession, pk: int, obj: UpdateTaskDispatchOutboxParam) -> int:
        """
        更新中心任务调度器向主人节点可靠投递任务执行帧的事务队列

        :param db: 数据库会话
        :param pk: 中心任务调度器向主人节点可靠投递任务执行帧的事务队列 ID
        :param obj: 更新 中心任务调度器向主人节点可靠投递任务执行帧的事务队列参数
        :return:
        """
        return await self.update_model(db, pk, obj)

    async def delete(self, db: AsyncSession, pks: list[int]) -> int:
        """
        批量删除中心任务调度器向主人节点可靠投递任务执行帧的事务队列

        :param db: 数据库会话
        :param pks: 中心任务调度器向主人节点可靠投递任务执行帧的事务队列 ID 列表
        :return:
        """
        return await self.delete_model_by_column(db, allow_multiple=True, id__in=pks)


task_dispatch_outbox_dao: CRUDTaskDispatchOutbox = CRUDTaskDispatchOutbox(TaskDispatchOutbox)
