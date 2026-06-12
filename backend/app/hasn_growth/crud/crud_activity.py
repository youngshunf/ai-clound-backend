from typing import Sequence

from sqlalchemy import Select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy_crud_plus import CRUDPlus

from backend.app.hasn_growth.model import Activity
from backend.app.hasn_growth.schema.activity import CreateActivityParam, UpdateActivityParam


class CRUDActivity(CRUDPlus[Activity]):
    async def get(self, db: AsyncSession, pk: int) -> Activity | None:
        """
        获取获客客户活动时间线（触达/回复/阶段变更/任务 run/人工备注统一流水）

        :param db: 数据库会话
        :param pk: 获客客户活动时间线（触达/回复/阶段变更/任务 run/人工备注统一流水） ID
        :return:
        """
        return await self.select_model(db, pk)

    async def get_select(self) -> Select:
        """获取获客客户活动时间线（触达/回复/阶段变更/任务 run/人工备注统一流水）列表查询表达式"""
        return await self.select_order('id', 'desc')

    async def get_all(self, db: AsyncSession) -> Sequence[Activity]:
        """
        获取所有获客客户活动时间线（触达/回复/阶段变更/任务 run/人工备注统一流水）

        :param db: 数据库会话
        :return:
        """
        return await self.select_models(db)

    async def create(self, db: AsyncSession, obj: CreateActivityParam) -> None:
        """
        创建获客客户活动时间线（触达/回复/阶段变更/任务 run/人工备注统一流水）

        :param db: 数据库会话
        :param obj: 创建获客客户活动时间线（触达/回复/阶段变更/任务 run/人工备注统一流水）参数
        :return:
        """
        await self.create_model(db, obj)

    async def update(self, db: AsyncSession, pk: int, obj: UpdateActivityParam) -> int:
        """
        更新获客客户活动时间线（触达/回复/阶段变更/任务 run/人工备注统一流水）

        :param db: 数据库会话
        :param pk: 获客客户活动时间线（触达/回复/阶段变更/任务 run/人工备注统一流水） ID
        :param obj: 更新 获客客户活动时间线（触达/回复/阶段变更/任务 run/人工备注统一流水）参数
        :return:
        """
        return await self.update_model(db, pk, obj)

    async def delete(self, db: AsyncSession, pks: list[int]) -> int:
        """
        批量删除获客客户活动时间线（触达/回复/阶段变更/任务 run/人工备注统一流水）

        :param db: 数据库会话
        :param pks: 获客客户活动时间线（触达/回复/阶段变更/任务 run/人工备注统一流水） ID 列表
        :return:
        """
        return await self.delete_model_by_column(db, allow_multiple=True, id__in=pks)


activity_dao: CRUDActivity = CRUDActivity(Activity)
