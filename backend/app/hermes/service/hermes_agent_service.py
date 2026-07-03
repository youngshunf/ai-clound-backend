from collections.abc import Sequence
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from backend.app.hermes.crud.crud_hermes_agent import hermes_agent_dao
from backend.app.hermes.model import HermesAgent
from backend.app.hermes.schema.hermes_agent import (
    CreateHermesAgentParam,
    DeleteHermesAgentParam,
    UpdateHermesAgentParam,
)
from backend.common.exception import errors
from backend.common.pagination import paging_data


class HermesAgentService:
    @staticmethod
    async def get(*, db: AsyncSession, pk: int) -> HermesAgent:
        """
        获取Hermes Agent

        :param db: 数据库会话
        :param pk: Hermes Agent  ID
        :return:
        """
        hermes_agent = await hermes_agent_dao.get(db, pk)
        if not hermes_agent:
            raise errors.NotFoundError(msg='Hermes Agent 不存在')
        return hermes_agent

    @staticmethod
    async def get_list(db: AsyncSession) -> dict[str, Any]:
        """
        获取Hermes Agent 列表

        :param db: 数据库会话
        :return:
        """
        hermes_agent_select = await hermes_agent_dao.get_select()
        return await paging_data(db, hermes_agent_select)

    @staticmethod
    async def get_all(*, db: AsyncSession) -> Sequence[HermesAgent]:
        """
        获取所有Hermes Agent

        :param db: 数据库会话
        :return:
        """
        hermes_agents = await hermes_agent_dao.get_all(db)
        return hermes_agents

    @staticmethod
    async def create(*, db: AsyncSession, obj: CreateHermesAgentParam) -> None:
        """
        创建Hermes Agent

        :param db: 数据库会话
        :param obj: 创建Hermes Agent 参数
        :return:
        """
        await hermes_agent_dao.create(db, obj)

    @staticmethod
    async def update(*, db: AsyncSession, pk: int, obj: UpdateHermesAgentParam) -> int:
        """
        更新Hermes Agent

        :param db: 数据库会话
        :param pk: Hermes Agent  ID
        :param obj: 更新Hermes Agent 参数
        :return:
        """
        count = await hermes_agent_dao.update(db, pk, obj)
        return count

    @staticmethod
    async def delete(*, db: AsyncSession, obj: DeleteHermesAgentParam) -> int:
        """
        删除Hermes Agent

        :param db: 数据库会话
        :param obj: Hermes Agent  ID 列表
        :return:
        """
        count = await hermes_agent_dao.delete(db, obj.pks)
        return count


hermes_agent_service: HermesAgentService = HermesAgentService()
