from typing import Any, Sequence

from sqlalchemy.ext.asyncio import AsyncSession

from backend.app.hasn_growth.crud.crud_activity import activity_dao
from backend.app.hasn_growth.model import Activity
from backend.app.hasn_growth.schema.activity import CreateActivityParam, DeleteActivityParam, UpdateActivityParam
from backend.common.exception import errors
from backend.common.pagination import paging_data


class ActivityService:
    @staticmethod
    async def get(*, db: AsyncSession, pk: int) -> Activity:
        """
        获取获客客户活动时间线（触达/回复/阶段变更/任务 run/人工备注统一流水）

        :param db: 数据库会话
        :param pk: 获客客户活动时间线（触达/回复/阶段变更/任务 run/人工备注统一流水） ID
        :return:
        """
        activity = await activity_dao.get(db, pk)
        if not activity:
            raise errors.NotFoundError(msg='获客客户活动时间线（触达/回复/阶段变更/任务 run/人工备注统一流水）不存在')
        return activity

    @staticmethod
    async def get_list(db: AsyncSession) -> dict[str, Any]:
        """
        获取获客客户活动时间线（触达/回复/阶段变更/任务 run/人工备注统一流水）列表

        :param db: 数据库会话
        :return:
        """
        activity_select = await activity_dao.get_select()
        return await paging_data(db, activity_select)

    @staticmethod
    async def get_all(*, db: AsyncSession) -> Sequence[Activity]:
        """
        获取所有获客客户活动时间线（触达/回复/阶段变更/任务 run/人工备注统一流水）

        :param db: 数据库会话
        :return:
        """
        activity_list = await activity_dao.get_all(db)
        return activity_list

    @staticmethod
    async def create(*, db: AsyncSession, obj: CreateActivityParam) -> None:
        """
        创建获客客户活动时间线（触达/回复/阶段变更/任务 run/人工备注统一流水）

        :param db: 数据库会话
        :param obj: 创建获客客户活动时间线（触达/回复/阶段变更/任务 run/人工备注统一流水）参数
        :return:
        """
        await activity_dao.create(db, obj)

    @staticmethod
    async def update(*, db: AsyncSession, pk: int, obj: UpdateActivityParam) -> int:
        """
        更新获客客户活动时间线（触达/回复/阶段变更/任务 run/人工备注统一流水）

        :param db: 数据库会话
        :param pk: 获客客户活动时间线（触达/回复/阶段变更/任务 run/人工备注统一流水） ID
        :param obj: 更新获客客户活动时间线（触达/回复/阶段变更/任务 run/人工备注统一流水）参数
        :return:
        """
        count = await activity_dao.update(db, pk, obj)
        return count

    @staticmethod
    async def delete(*, db: AsyncSession, obj: DeleteActivityParam) -> int:
        """
        删除获客客户活动时间线（触达/回复/阶段变更/任务 run/人工备注统一流水）

        :param db: 数据库会话
        :param obj: 获客客户活动时间线（触达/回复/阶段变更/任务 run/人工备注统一流水） ID 列表
        :return:
        """
        count = await activity_dao.delete(db, obj.pks)
        return count


activity_service: ActivityService = ActivityService()
