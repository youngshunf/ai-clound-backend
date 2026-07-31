from uuid import UUID

from typing import Sequence

from sqlalchemy import Select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy_crud_plus import CRUDPlus

from backend.app.hasn_hosting.model import HasnCloudNodeEvents
from backend.app.hasn_hosting.schema.hasn_cloud_node_events import CreateHasnCloudNodeEventsParam, UpdateHasnCloudNodeEventsParam


class CRUDHasnCloudNodeEvents(CRUDPlus[HasnCloudNodeEvents]):
    async def get(self, db: AsyncSession, pk: UUID) -> HasnCloudNodeEvents | None:
        """
        获取云端托管节点事件流水

        :param db: 数据库会话
        :param pk: 云端托管节点事件流水 ID
        :return:
        """
        return await self.select_model(db, pk)

    async def get_select(self) -> Select:
        """获取云端托管节点事件流水列表查询表达式"""
        return await self.select_order('id', 'desc')

    async def get_all(self, db: AsyncSession) -> Sequence[HasnCloudNodeEvents]:
        """
        获取所有云端托管节点事件流水

        :param db: 数据库会话
        :return:
        """
        return await self.select_models(db)

    async def create(self, db: AsyncSession, obj: CreateHasnCloudNodeEventsParam) -> None:
        """
        创建云端托管节点事件流水

        :param db: 数据库会话
        :param obj: 创建云端托管节点事件流水参数
        :return:
        """
        await self.create_model(db, obj)

    async def update(self, db: AsyncSession, pk: UUID, obj: UpdateHasnCloudNodeEventsParam) -> int:
        """
        更新云端托管节点事件流水

        :param db: 数据库会话
        :param pk: 云端托管节点事件流水 ID
        :param obj: 更新 云端托管节点事件流水参数
        :return:
        """
        return await self.update_model(db, pk, obj)

    async def delete(self, db: AsyncSession, pks: list[UUID]) -> int:
        """
        批量删除云端托管节点事件流水

        :param db: 数据库会话
        :param pks: 云端托管节点事件流水 ID 列表
        :return:
        """
        return await self.delete_model_by_column(db, allow_multiple=True, id__in=pks)


hasn_cloud_node_events_dao: CRUDHasnCloudNodeEvents = CRUDHasnCloudNodeEvents(HasnCloudNodeEvents)
