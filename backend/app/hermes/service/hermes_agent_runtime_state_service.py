from collections.abc import Sequence
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from backend.app.hermes.crud.crud_hermes_agent_runtime_state import hermes_agent_runtime_state_dao
from backend.app.hermes.model import HermesAgentRuntimeState
from backend.app.hermes.schema.hermes_agent_runtime_state import (
    CreateHermesAgentRuntimeStateParam,
    DeleteHermesAgentRuntimeStateParam,
    UpdateHermesAgentRuntimeStateParam,
)
from backend.common.exception import errors
from backend.common.pagination import paging_data


class HermesAgentRuntimeStateService:
    @staticmethod
    async def get(*, db: AsyncSession, pk: int) -> HermesAgentRuntimeState:
        """
        获取Hermes Agent Runtime 状态

        :param db: 数据库会话
        :param pk: Hermes Agent Runtime 状态 ID
        :return:
        """
        hermes_agent_runtime_state = await hermes_agent_runtime_state_dao.get(db, pk)
        if not hermes_agent_runtime_state:
            raise errors.NotFoundError(msg='Hermes Agent Runtime 状态不存在')
        return hermes_agent_runtime_state

    @staticmethod
    async def get_list(db: AsyncSession) -> dict[str, Any]:
        """
        获取Hermes Agent Runtime 状态列表

        :param db: 数据库会话
        :return:
        """
        hermes_agent_runtime_state_select = await hermes_agent_runtime_state_dao.get_select()
        return await paging_data(db, hermes_agent_runtime_state_select)

    @staticmethod
    async def get_all(*, db: AsyncSession) -> Sequence[HermesAgentRuntimeState]:
        """
        获取所有Hermes Agent Runtime 状态

        :param db: 数据库会话
        :return:
        """
        hermes_agent_runtime_states = await hermes_agent_runtime_state_dao.get_all(db)
        return hermes_agent_runtime_states

    @staticmethod
    async def create(*, db: AsyncSession, obj: CreateHermesAgentRuntimeStateParam) -> None:
        """
        创建Hermes Agent Runtime 状态

        :param db: 数据库会话
        :param obj: 创建Hermes Agent Runtime 状态参数
        :return:
        """
        await hermes_agent_runtime_state_dao.create(db, obj)

    @staticmethod
    async def update(*, db: AsyncSession, pk: int, obj: UpdateHermesAgentRuntimeStateParam) -> int:
        """
        更新Hermes Agent Runtime 状态

        :param db: 数据库会话
        :param pk: Hermes Agent Runtime 状态 ID
        :param obj: 更新Hermes Agent Runtime 状态参数
        :return:
        """
        count = await hermes_agent_runtime_state_dao.update(db, pk, obj)
        return count

    @staticmethod
    async def delete(*, db: AsyncSession, obj: DeleteHermesAgentRuntimeStateParam) -> int:
        """
        删除Hermes Agent Runtime 状态

        :param db: 数据库会话
        :param obj: Hermes Agent Runtime 状态 ID 列表
        :return:
        """
        count = await hermes_agent_runtime_state_dao.delete(db, obj.pks)
        return count


hermes_agent_runtime_state_service: HermesAgentRuntimeStateService = HermesAgentRuntimeStateService()
