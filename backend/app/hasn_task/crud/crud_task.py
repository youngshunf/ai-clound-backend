from collections.abc import Sequence
from typing import cast

from sqlalchemy import Select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy_crud_plus import CRUDPlus

from backend.app.hasn_task.model import HasnTask
from backend.app.hasn_task.schema.task import CreateHasnTaskParam, UpdateHasnTaskParam


class CRUDHasnTask(CRUDPlus[HasnTask]):
    async def get(self, db: AsyncSession, pk: int) -> HasnTask | None:
        """
        获取任务定义

        :param db: 数据库会话
        :param pk: 任务定义 ID
        :return:
        """
        return cast(HasnTask | None, await self.select_model(db, pk))

    async def get_select(self) -> Select:
        """获取任务定义列表查询表达式"""
        return await self.select_order('id', 'desc')

    async def get_all(self, db: AsyncSession) -> Sequence[HasnTask]:
        """
        获取所有任务定义

        :param db: 数据库会话
        :return:
        """
        return cast(Sequence[HasnTask], await self.select_models(db))

    async def create(self, db: AsyncSession, obj: CreateHasnTaskParam) -> None:
        """
        创建任务定义

        :param db: 数据库会话
        :param obj: 创建任务定义参数
        :return:
        """
        await self.create_model(db, obj)

    async def update(self, db: AsyncSession, pk: int, obj: UpdateHasnTaskParam) -> int:
        """
        更新任务定义

        :param db: 数据库会话
        :param pk: 任务定义 ID
        :param obj: 更新 任务定义参数
        :return:
        """
        return await self.update_model(db, pk, obj)

    async def delete(self, db: AsyncSession, pks: list[int]) -> int:
        """
        批量删除任务定义

        :param db: 数据库会话
        :param pks: 任务定义 ID 列表
        :return:
        """
        return await self.delete_model_by_column(db, allow_multiple=True, id__in=pks)


hasn_task_dao: CRUDHasnTask = CRUDHasnTask(HasnTask)
