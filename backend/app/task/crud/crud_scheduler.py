from collections.abc import Sequence
from typing import Any, cast

from sqlalchemy import Select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy_crud_plus import CRUDPlus

from backend.app.task.model import TaskScheduler
from backend.app.task.schema.scheduler import CreateTaskSchedulerParam, UpdateTaskSchedulerParam


class CRUDTaskScheduler(CRUDPlus[TaskScheduler]):
    """任务调度数据库操作类"""

    @staticmethod
    def _single_scheduler(result: object) -> TaskScheduler | None:
        """将无关联加载的查询结果收紧为单个任务调度。"""
        if result is not None and not isinstance(result, TaskScheduler):
            raise TypeError('任务调度单模型查询返回了关联结果')
        return cast(TaskScheduler | None, result)

    @staticmethod
    def _scheduler_sequence(result: Sequence[object]) -> Sequence[TaskScheduler]:
        """将无关联加载的查询结果收紧为任务调度序列。"""
        if not all(isinstance(item, TaskScheduler) for item in result):
            raise TypeError('任务调度列表查询返回了关联结果')
        return cast(Sequence[TaskScheduler], result)

    @staticmethod
    async def get(db: AsyncSession, pk: int) -> TaskScheduler | None:
        """
        获取任务调度

        :param db: 数据库会话
        :param pk: 任务调度 ID
        :return:
        """
        return task_scheduler_dao._single_scheduler(await task_scheduler_dao.select_model(db, pk))

    async def get_all(self, db: AsyncSession) -> Sequence[TaskScheduler]:
        """
        获取所有任务调度

        :param db: 数据库会话
        :return:
        """
        return self._scheduler_sequence(await self.select_models(db))

    async def get_select(self, name: str | None, type: int | None) -> Select:
        """
        获取任务调度列表查询表达式

        :param name: 任务调度名称
        :param type: 任务调度类型
        :return:
        """
        filters: dict[str, Any] = {}

        if name is not None:
            filters['name__like'] = f'%{name}%'
        if type is not None:
            filters['type'] = type

        return await self.select_order('id', **filters)

    async def get_by_name(self, db: AsyncSession, name: str) -> TaskScheduler | None:
        """
        通过名称获取任务调度

        :param db: 数据库会话
        :param name: 任务调度名称
        :return:
        """
        return self._single_scheduler(await self.select_model_by_column(db, name=name))

    async def create(self, db: AsyncSession, obj: CreateTaskSchedulerParam) -> None:
        """
        创建任务调度

        :param db: 数据库会话
        :param obj: 创建任务调度参数
        :return:
        """
        await self.create_model(db, obj, flush=True)
        TaskScheduler.no_changes = False

    async def update(self, db: AsyncSession, pk: int, obj: UpdateTaskSchedulerParam) -> int:
        """
        更新任务调度

        :param db: 数据库会话
        :param pk: 任务调度 ID
        :param obj: 更新任务调度参数
        :return:
        """
        task_scheduler = await self.get(db, pk)
        if task_scheduler is None:
            return 0
        for key, value in obj.model_dump(exclude_unset=True).items():
            setattr(task_scheduler, key, value)
        TaskScheduler.no_changes = False
        return 1

    async def set_status(self, db: AsyncSession, pk: int, *, status: bool) -> int:
        """
        设置任务调度状态

        :param db: 数据库会话
        :param pk: 任务调度 ID
        :param status: 状态
        :return:
        """
        task_scheduler = await self.get(db, pk)
        if task_scheduler is None:
            return 0
        task_scheduler.enabled = status
        TaskScheduler.no_changes = False
        return 1

    async def delete(self, db: AsyncSession, pk: int) -> int:
        """
        删除任务调度

        :param db: 数据库会话
        :param pk: 任务调度 ID
        :return:
        """
        task_scheduler = await self.get(db, pk)
        if task_scheduler is None:
            return 0
        await db.delete(task_scheduler)
        TaskScheduler.no_changes = False
        return 1


task_scheduler_dao: CRUDTaskScheduler = CRUDTaskScheduler(TaskScheduler)
