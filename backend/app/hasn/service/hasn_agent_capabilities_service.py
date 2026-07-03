from collections.abc import Sequence
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from backend.app.hasn.crud.crud_hasn_agent_capabilities import hasn_agent_capabilities_dao
from backend.app.hasn.model import HasnAgentCapabilities
from backend.app.hasn.schema.hasn_agent_capabilities import (
    CreateHasnAgentCapabilitiesParam,
    DeleteHasnAgentCapabilitiesParam,
    UpdateHasnAgentCapabilitiesParam,
)
from backend.common.exception import errors
from backend.common.pagination import paging_data


class HasnAgentCapabilitiesService:
    @staticmethod
    async def get(*, db: AsyncSession, pk: int) -> HasnAgentCapabilities:
        """
        获取HASN Agent 能力声明

        :param db: 数据库会话
        :param pk: HASN Agent 能力声明 ID
        :return:
        """
        hasn_agent_capabilities = await hasn_agent_capabilities_dao.get(db, pk)
        if not hasn_agent_capabilities:
            raise errors.NotFoundError(msg='HASN Agent 能力声明不存在')
        return hasn_agent_capabilities

    @staticmethod
    async def get_list(db: AsyncSession) -> dict[str, Any]:
        """
        获取HASN Agent 能力声明列表

        :param db: 数据库会话
        :return:
        """
        hasn_agent_capabilities_select = await hasn_agent_capabilities_dao.get_select()
        return await paging_data(db, hasn_agent_capabilities_select)

    @staticmethod
    async def get_all(*, db: AsyncSession) -> Sequence[HasnAgentCapabilities]:
        """
        获取所有HASN Agent 能力声明

        :param db: 数据库会话
        :return:
        """
        hasn_agent_capabilitiess = await hasn_agent_capabilities_dao.get_all(db)
        return hasn_agent_capabilitiess

    @staticmethod
    async def create(*, db: AsyncSession, obj: CreateHasnAgentCapabilitiesParam) -> None:
        """
        创建HASN Agent 能力声明

        :param db: 数据库会话
        :param obj: 创建HASN Agent 能力声明参数
        :return:
        """
        await hasn_agent_capabilities_dao.create(db, obj)

    @staticmethod
    async def update(*, db: AsyncSession, pk: int, obj: UpdateHasnAgentCapabilitiesParam) -> int:
        """
        更新HASN Agent 能力声明

        :param db: 数据库会话
        :param pk: HASN Agent 能力声明 ID
        :param obj: 更新HASN Agent 能力声明参数
        :return:
        """
        count = await hasn_agent_capabilities_dao.update(db, pk, obj)
        return count

    @staticmethod
    async def delete(*, db: AsyncSession, obj: DeleteHasnAgentCapabilitiesParam) -> int:
        """
        删除HASN Agent 能力声明

        :param db: 数据库会话
        :param obj: HASN Agent 能力声明 ID 列表
        :return:
        """
        count = await hasn_agent_capabilities_dao.delete(db, obj.pks)
        return count


hasn_agent_capabilities_service: HasnAgentCapabilitiesService = HasnAgentCapabilitiesService()
