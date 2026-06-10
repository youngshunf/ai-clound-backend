from collections.abc import Sequence

from sqlalchemy import Select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy_crud_plus import CRUDPlus

from backend.app.hasn_task.model import HasnTaskRun
from backend.app.hasn_task.schema.run import CreateHasnTaskRunParam, UpdateHasnTaskRunParam


class CRUDHasnTaskRun(CRUDPlus[HasnTaskRun]):
    async def get(self, db: AsyncSession, pk: int) -> HasnTaskRun | None:
        """
        获取任务执行记录

        :param db: 数据库会话
        :param pk: 任务执行记录 ID
        :return:
        """
        return await self.select_model(db, pk)

    async def get_select(self) -> Select:
        """获取任务执行记录列表查询表达式"""
        return await self.select_order('id', 'desc')

    async def get_all(self, db: AsyncSession) -> Sequence[HasnTaskRun]:
        """
        获取所有任务执行记录

        :param db: 数据库会话
        :return:
        """
        return await self.select_models(db)

    async def create(self, db: AsyncSession, obj: CreateHasnTaskRunParam) -> None:
        """
        创建任务执行记录

        :param db: 数据库会话
        :param obj: 创建任务执行记录参数
        :return:
        """
        await self.create_model(db, obj)

    async def update(self, db: AsyncSession, pk: int, obj: UpdateHasnTaskRunParam) -> int:
        """
        更新任务执行记录

        :param db: 数据库会话
        :param pk: 任务执行记录 ID
        :param obj: 更新 任务执行记录参数
        :return:
        """
        return await self.update_model(db, pk, obj)

    async def delete(self, db: AsyncSession, pks: list[int]) -> int:
        """
        批量删除任务执行记录

        :param db: 数据库会话
        :param pks: 任务执行记录 ID 列表
        :return:
        """
        return await self.delete_model_by_column(db, allow_multiple=True, id__in=pks)


hasn_task_run_dao: CRUDHasnTaskRun = CRUDHasnTaskRun(HasnTaskRun)
