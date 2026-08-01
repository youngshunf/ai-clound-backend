from uuid import UUID

from typing import Any, Sequence

from sqlalchemy.ext.asyncio import AsyncSession

from backend.app.hasn_hosting.crud.crud_hasn_cloud_node_events import hasn_cloud_node_events_dao
from backend.app.hasn_hosting.model import HasnCloudNodeEvents
from backend.app.hasn_hosting.schema.hasn_cloud_node_events import CreateHasnCloudNodeEventsParam, DeleteHasnCloudNodeEventsParam, UpdateHasnCloudNodeEventsParam
from backend.common.exception import errors
from backend.common.pagination import paging_data


class HasnCloudNodeEventsService:
    @staticmethod
    async def get(*, db: AsyncSession, pk: UUID) -> HasnCloudNodeEvents:
        """
        获取云端托管节点事件流水

        :param db: 数据库会话
        :param pk: 云端托管节点事件流水 ID
        :return:
        """
        hasn_cloud_node_events = await hasn_cloud_node_events_dao.get(db, pk)
        if not hasn_cloud_node_events:
            raise errors.NotFoundError(msg='云端托管节点事件流水不存在')
        return hasn_cloud_node_events

    @staticmethod
    async def get_list(db: AsyncSession) -> dict[str, Any]:
        """
        获取云端托管节点事件流水列表

        :param db: 数据库会话
        :return:
        """
        hasn_cloud_node_events_select = await hasn_cloud_node_events_dao.get_select()
        return await paging_data(db, hasn_cloud_node_events_select)

    @staticmethod
    async def get_all(*, db: AsyncSession) -> Sequence[HasnCloudNodeEvents]:
        """
        获取所有云端托管节点事件流水

        :param db: 数据库会话
        :return:
        """
        hasn_cloud_node_events_list = await hasn_cloud_node_events_dao.get_all(db)
        return hasn_cloud_node_events_list

    @staticmethod
    async def create(*, db: AsyncSession, obj: CreateHasnCloudNodeEventsParam) -> None:
        """
        创建云端托管节点事件流水

        :param db: 数据库会话
        :param obj: 创建云端托管节点事件流水参数
        :return:
        """
        await hasn_cloud_node_events_dao.create(db, obj)

    @staticmethod
    async def update(*, db: AsyncSession, pk: UUID, obj: UpdateHasnCloudNodeEventsParam) -> int:
        """
        更新云端托管节点事件流水

        :param db: 数据库会话
        :param pk: 云端托管节点事件流水 ID
        :param obj: 更新云端托管节点事件流水参数
        :return:
        """
        count = await hasn_cloud_node_events_dao.update(db, pk, obj)
        return count

    @staticmethod
    async def delete(*, db: AsyncSession, obj: DeleteHasnCloudNodeEventsParam) -> int:
        """
        删除云端托管节点事件流水

        :param db: 数据库会话
        :param obj: 云端托管节点事件流水 ID 列表
        :return:
        """
        count = await hasn_cloud_node_events_dao.delete(db, obj.pks)
        return count


hasn_cloud_node_events_service: HasnCloudNodeEventsService = HasnCloudNodeEventsService()
