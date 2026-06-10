from collections.abc import Sequence
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.app.hasn_task.crud.crud_run import hasn_task_run_dao
from backend.app.hasn_task.model import HasnTask, HasnTaskRun
from backend.app.hasn_task.schema.run import CreateHasnTaskRunParam, DeleteHasnTaskRunParam, UpdateHasnTaskRunParam
from backend.common.exception import errors
from backend.common.pagination import paging_data


class HasnTaskRunService:
    @staticmethod
    async def get(*, db: AsyncSession, pk: int) -> HasnTaskRun:
        """
        获取任务执行记录

        :param db: 数据库会话
        :param pk: 任务执行记录 ID
        :return:
        """
        hasn_task_run = await hasn_task_run_dao.get(db, pk)
        if not hasn_task_run:
            raise errors.NotFoundError(msg='任务执行记录不存在')
        return hasn_task_run

    @staticmethod
    async def get_list(db: AsyncSession) -> dict[str, Any]:
        """
        获取任务执行记录列表

        :param db: 数据库会话
        :return:
        """
        hasn_task_run_select = await hasn_task_run_dao.get_select()
        return await paging_data(db, hasn_task_run_select)

    @staticmethod
    async def get_list_by_task_id(
        db: AsyncSession, task_id: int
    ) -> dict[str, Any]:
        """获取指定任务的执行记录列表"""
        select_stmt = (
            select(HasnTaskRun)
            .where(HasnTaskRun.task_id == task_id)
            .order_by(HasnTaskRun.created_time.desc())
        )
        return await paging_data(db, select_stmt)

    @staticmethod
    async def get_list_by_owner(db: AsyncSession, owner_id: str) -> dict[str, Any]:
        """获取指定 owner 任务下的执行记录列表。"""
        select_stmt = (
            select(HasnTaskRun)
            .join(HasnTask, HasnTask.id == HasnTaskRun.task_id)
            .where(HasnTask.owner_id == owner_id)
            .order_by(HasnTaskRun.created_time.desc())
        )
        return await paging_data(db, select_stmt)

    @staticmethod
    async def get_all(*, db: AsyncSession) -> Sequence[HasnTaskRun]:
        """
        获取所有任务执行记录

        :param db: 数据库会话
        :return:
        """
        hasn_task_run_list = await hasn_task_run_dao.get_all(db)
        return hasn_task_run_list

    @staticmethod
    async def create(*, db: AsyncSession, obj: CreateHasnTaskRunParam) -> None:
        """
        创建任务执行记录

        :param db: 数据库会话
        :param obj: 创建任务执行记录参数
        :return:
        """
        await hasn_task_run_dao.create(db, obj)

    @staticmethod
    async def update(*, db: AsyncSession, pk: int, obj: UpdateHasnTaskRunParam) -> int:
        """
        更新任务执行记录

        :param db: 数据库会话
        :param pk: 任务执行记录 ID
        :param obj: 更新任务执行记录参数
        :return:
        """
        count = await hasn_task_run_dao.update(db, pk, obj)
        return count

    @staticmethod
    async def delete(*, db: AsyncSession, obj: DeleteHasnTaskRunParam) -> int:
        """
        删除任务执行记录

        :param db: 数据库会话
        :param obj: 任务执行记录 ID 列表
        :return:
        """
        count = await hasn_task_run_dao.delete(db, obj.pks)
        return count


hasn_task_run_service: HasnTaskRunService = HasnTaskRunService()
