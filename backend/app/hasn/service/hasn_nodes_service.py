from typing import Any, Sequence

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.app.hasn.crud.crud_hasn_nodes import hasn_nodes_dao
from backend.app.hasn.model import HasnNodes
from backend.app.hasn.schema.hasn_nodes import CreateHasnNodesParam, DeleteHasnNodesParam, UpdateHasnNodesParam
from backend.common.exception import errors
from backend.common.pagination import paging_data


class HasnNodesService:
    @staticmethod
    async def get(*, db: AsyncSession, pk: int) -> HasnNodes:
        """
        获取HASN Node 主

        :param db: 数据库会话
        :param pk: HASN Node 主 ID
        :return:
        """
        hasn_nodes = await hasn_nodes_dao.get(db, pk)
        if not hasn_nodes:
            raise errors.NotFoundError(msg='HASN Node 主不存在')
        return hasn_nodes

    @staticmethod
    async def get_list(db: AsyncSession, user_id: int | None = None) -> dict[str, Any]:
        """
        获取HASN Node 主列表

        :param db: 数据库会话
        :return:
        """
        hasn_nodes_select = await hasn_nodes_dao.get_select()
        if user_id is not None:
            hasn_nodes_select = hasn_nodes_select.where(HasnNodes.user_id == user_id)
        return await paging_data(db, hasn_nodes_select)

    @staticmethod
    async def get_all(*, db: AsyncSession) -> Sequence[HasnNodes]:
        """
        获取所有HASN Node 主

        :param db: 数据库会话
        :return:
        """
        hasn_nodess = await hasn_nodes_dao.get_all(db)
        return hasn_nodess

    @staticmethod
    async def create(*, db: AsyncSession, obj: CreateHasnNodesParam, user_id: int | None = None) -> HasnNodes:
        """
        创建HASN Node 主

        :param db: 数据库会话
        :param obj: 创建HASN Node 主参数
        :return:
        """
        payload = obj.model_dump()
        if user_id is not None:
            payload['user_id'] = user_id
        await hasn_nodes_dao.create(db, CreateHasnNodesParam(**payload))
        result = await db.execute(
            select(HasnNodes).where(HasnNodes.node_id == payload['node_id'])
        )
        return result.scalar_one()

    @staticmethod
    async def update(*, db: AsyncSession, pk: int, obj: UpdateHasnNodesParam, user_id: int | None = None) -> int:
        """
        更新HASN Node 主

        :param db: 数据库会话
        :param pk: HASN Node 主 ID
        :param obj: 更新HASN Node 主参数
        :return:
        """
        count = await hasn_nodes_dao.update(db, pk, obj)
        return count

    @staticmethod
    async def delete(*, db: AsyncSession, obj: DeleteHasnNodesParam) -> int:
        """
        删除HASN Node 主

        :param db: 数据库会话
        :param obj: HASN Node 主 ID 列表
        :return:
        """
        count = await hasn_nodes_dao.delete(db, obj.pks)
        return count

    @staticmethod
    async def update_runtime_metadata(
        *,
        db: AsyncSession,
        node_id: str,
        ip_address: str | None = None,
        ip_location: str | None = None,
        device_platform: str | None = None,
        app_version: str | None = None,
    ) -> None:
        """回填节点运行期元数据（客户端 IP / 归属地 / 平台 / 版本）。

        约定：
        - device_platform / app_version 为 None/空 时跳过，不覆盖既有值；
        - ip_location **跟随** ip_address：仅当本次拿到 ip_address 才一并写 ip_location，
          且 ip_location 为 None（私网/无 mmdb/库内查不到）时如实落空，**绝不伪造城市**。
        """
        if not node_id:
            return
        result = await db.execute(
            select(HasnNodes).where(
                HasnNodes.node_id == node_id,
                HasnNodes.status == 'active',
            )
        )
        node = result.scalar_one_or_none()
        if node is None:
            return
        if ip_address:
            node.ip_address = ip_address
            node.ip_location = ip_location  # 跟随 IP：未知归属地如实写 None
        if device_platform:
            node.device_platform = device_platform
        if app_version:
            node.app_version = app_version
        await db.flush()


hasn_nodes_service: HasnNodesService = HasnNodesService()
