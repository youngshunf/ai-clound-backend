from collections.abc import Sequence
from datetime import datetime, timedelta
from datetime import timezone as tz
from typing import Any

from croniter import croniter
from sqlalchemy import insert, select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.app.hasn_task.crud.crud_task import hasn_task_dao
from backend.app.hasn_task.model import HasnTask
from backend.app.hasn_task.schema.task import CreateHasnTaskParam, DeleteHasnTaskParam, UpdateHasnTaskParam
from backend.common.exception import errors
from backend.common.pagination import paging_data


def calc_next_run_at(schedule_type: str, schedule_config: dict) -> datetime | None:
    """计算下次执行时间"""
    now = datetime.now(tz.utc)
    config = schedule_config or {}

    if schedule_type == 'once':
        run_at = config.get('run_at')
        if run_at:
            if isinstance(run_at, str):
                return datetime.fromisoformat(run_at.replace('Z', '+00:00'))
            return run_at
        return now  # 立即执行

    if schedule_type == 'interval':
        minutes = config.get('minutes', 60)
        return now + timedelta(minutes=minutes)

    if schedule_type == 'cron':
        expr = config.get('expr', '0 * * * *')
        try:
            cron = croniter(expr, now)
            return cron.get_next(datetime)
        except Exception:
            return now + timedelta(hours=1)

    return now


class HasnTaskService:
    @staticmethod
    async def get(*, db: AsyncSession, pk: int) -> HasnTask:
        """
        获取任务定义

        :param db: 数据库会话
        :param pk: 任务定义 ID
        :return:
        """
        hasn_task = await hasn_task_dao.get(db, pk)
        if not hasn_task:
            raise errors.NotFoundError(msg='任务定义不存在')
        return hasn_task

    @staticmethod
    async def get_list(db: AsyncSession) -> dict[str, Any]:
        """
        获取任务定义列表

        :param db: 数据库会话
        :return:
        """
        hasn_task_select = await hasn_task_dao.get_select()
        return await paging_data(db, hasn_task_select)

    @staticmethod
    async def get_list_by_owner(
        db: AsyncSession, owner_id: str
    ) -> dict[str, Any]:
        """获取指定 owner 的任务列表"""
        select_stmt = select(HasnTask).where(HasnTask.owner_id == owner_id)
        return await paging_data(db, select_stmt)

    @staticmethod
    async def get_all(*, db: AsyncSession) -> Sequence[HasnTask]:
        """
        获取所有任务定义

        :param db: 数据库会话
        :return:
        """
        hasn_task_list = await hasn_task_dao.get_all(db)
        return hasn_task_list

    @staticmethod
    async def create(*, db: AsyncSession, obj: CreateHasnTaskParam) -> None:
        """
        创建任务定义

        :param db: 数据库会话
        :param obj: 创建任务定义参数
        :return:
        """
        await hasn_task_dao.create(db, obj)

    @staticmethod
    async def create_with_schedule(
        *,
        db: AsyncSession,
        obj: CreateHasnTaskParam,
    ) -> HasnTask:
        """创建任务并自动计算 next_run_at"""
        task_dict = obj.model_dump()
        task_dict['next_run_at'] = calc_next_run_at(
            obj.schedule_type, obj.schedule_config
        )

        # 排除时间戳字段，让 SQLAlchemy 自动处理
        task_dict.pop('created_time', None)
        task_dict.pop('updated_time', None)
        task_dict.pop('create_time', None)
        task_dict.pop('update_time', None)

        stmt = insert(HasnTask).values(**task_dict).returning(HasnTask.id)

        result = await db.execute(stmt)
        task_id = result.scalar_one()
        await db.flush()

        # 查询并返回创建的任务
        task = await hasn_task_dao.get(db, task_id)
        if task is None:
            raise RuntimeError(f'任务创建后无法读取：task_id={task_id}')
        return task

    @staticmethod
    async def update(*, db: AsyncSession, pk: int, obj: UpdateHasnTaskParam) -> int:
        """
        更新任务定义

        :param db: 数据库会话
        :param pk: 任务定义 ID
        :param obj: 更新任务定义参数
        :return:
        """
        count = await hasn_task_dao.update(db, pk, obj)
        return count

    @staticmethod
    async def delete(*, db: AsyncSession, obj: DeleteHasnTaskParam) -> int:
        """
        删除任务定义

        :param db: 数据库会话
        :param obj: 任务定义 ID 列表
        :return:
        """
        count = await hasn_task_dao.delete(db, obj.pks)
        return count

    @staticmethod
    async def enable_task(*, db: AsyncSession, task_id: int) -> HasnTask:
        """
        启用任务

        :param db: 数据库会话
        :param task_id: 任务 ID
        :return:
        """
        task = await hasn_task_dao.get(db, task_id)
        if not task:
            raise errors.NotFoundError(msg='任务不存在')

        task.enabled = True
        task.updated_time = datetime.now(tz.utc)
        await db.flush()
        return task

    @staticmethod
    async def disable_task(*, db: AsyncSession, task_id: int) -> HasnTask:
        """
        禁用任务

        :param db: 数据库会话
        :param task_id: 任务 ID
        :return:
        """
        task = await hasn_task_dao.get(db, task_id)
        if not task:
            raise errors.NotFoundError(msg='任务不存在')

        task.enabled = False
        task.updated_time = datetime.now(tz.utc)
        await db.flush()
        return task


hasn_task_service: HasnTaskService = HasnTaskService()
