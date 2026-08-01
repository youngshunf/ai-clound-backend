from uuid import UUID

from typing import Any, Sequence

from sqlalchemy.ext.asyncio import AsyncSession

from backend.app.hasn_hosting.crud.crud_hasn_cloud_nodes import hasn_cloud_nodes_dao
from backend.app.hasn_hosting.model import HasnCloudNodes
from backend.app.hasn_hosting.schema.hasn_cloud_nodes import CreateHasnCloudNodesParam, DeleteHasnCloudNodesParam, UpdateHasnCloudNodesParam
from backend.common.exception import errors
from backend.common.pagination import paging_data


class HasnCloudNodesService:
    @staticmethod
    async def get(*, db: AsyncSession, pk: UUID) -> HasnCloudNodes:
        """
        获取云端托管节点状态

        :param db: 数据库会话
        :param pk: 云端托管节点状态 ID
        :return:
        """
        hasn_cloud_nodes = await hasn_cloud_nodes_dao.get(db, pk)
        if not hasn_cloud_nodes:
            raise errors.NotFoundError(msg='云端托管节点状态不存在')
        return hasn_cloud_nodes

    @staticmethod
    async def get_list(db: AsyncSession) -> dict[str, Any]:
        """
        获取云端托管节点状态列表

        :param db: 数据库会话
        :return:
        """
        hasn_cloud_nodes_select = await hasn_cloud_nodes_dao.get_select()
        return await paging_data(db, hasn_cloud_nodes_select)

    @staticmethod
    async def get_all(*, db: AsyncSession) -> Sequence[HasnCloudNodes]:
        """
        获取所有云端托管节点状态

        :param db: 数据库会话
        :return:
        """
        hasn_cloud_nodes_list = await hasn_cloud_nodes_dao.get_all(db)
        return hasn_cloud_nodes_list

    @staticmethod
    async def create(*, db: AsyncSession, obj: CreateHasnCloudNodesParam) -> None:
        """
        创建云端托管节点状态

        :param db: 数据库会话
        :param obj: 创建云端托管节点状态参数
        :return:
        """
        await hasn_cloud_nodes_dao.create(db, obj)

    @staticmethod
    async def update(*, db: AsyncSession, pk: UUID, obj: UpdateHasnCloudNodesParam) -> int:
        """
        更新云端托管节点状态

        :param db: 数据库会话
        :param pk: 云端托管节点状态 ID
        :param obj: 更新云端托管节点状态参数
        :return:
        """
        count = await hasn_cloud_nodes_dao.update(db, pk, obj)
        return count

    @staticmethod
    async def delete(*, db: AsyncSession, obj: DeleteHasnCloudNodesParam) -> int:
        """
        删除云端托管节点状态

        :param db: 数据库会话
        :param obj: 云端托管节点状态 ID 列表
        :return:
        """
        count = await hasn_cloud_nodes_dao.delete(db, obj.pks)
        return count


hasn_cloud_nodes_service: HasnCloudNodesService = HasnCloudNodesService()
