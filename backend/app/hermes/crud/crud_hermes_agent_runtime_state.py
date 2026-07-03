from collections.abc import Sequence

from sqlalchemy import Select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy_crud_plus import CRUDPlus

from backend.app.hermes.model import HermesAgentRuntimeState
from backend.app.hermes.schema.hermes_agent_runtime_state import (
    CreateHermesAgentRuntimeStateParam,
    UpdateHermesAgentRuntimeStateParam,
)


class CRUDHermesAgentRuntimeState(CRUDPlus[HermesAgentRuntimeState]):
    async def get(self, db: AsyncSession, pk: int) -> HermesAgentRuntimeState | None:
        """
        获取Hermes Agent Runtime 状态

        :param db: 数据库会话
        :param pk: Hermes Agent Runtime 状态 ID
        :return:
        """
        return await self.select_model(db, pk)

    async def get_select(self) -> Select:
        """获取Hermes Agent Runtime 状态列表查询表达式"""
        return await self.select_order('id', 'desc')

    async def get_all(self, db: AsyncSession) -> Sequence[HermesAgentRuntimeState]:
        """
        获取所有Hermes Agent Runtime 状态

        :param db: 数据库会话
        :return:
        """
        return await self.select_models(db)

    async def create(self, db: AsyncSession, obj: CreateHermesAgentRuntimeStateParam) -> None:
        """
        创建Hermes Agent Runtime 状态

        :param db: 数据库会话
        :param obj: 创建Hermes Agent Runtime 状态参数
        :return:
        """
        await self.create_model(db, obj)

    async def update(self, db: AsyncSession, pk: int, obj: UpdateHermesAgentRuntimeStateParam) -> int:
        """
        更新Hermes Agent Runtime 状态

        :param db: 数据库会话
        :param pk: Hermes Agent Runtime 状态 ID
        :param obj: 更新 Hermes Agent Runtime 状态参数
        :return:
        """
        return await self.update_model(db, pk, obj)

    async def delete(self, db: AsyncSession, pks: list[int]) -> int:
        """
        批量删除Hermes Agent Runtime 状态

        :param db: 数据库会话
        :param pks: Hermes Agent Runtime 状态 ID 列表
        :return:
        """
        return await self.delete_model_by_column(db, allow_multiple=True, id__in=pks)


hermes_agent_runtime_state_dao: CRUDHermesAgentRuntimeState = CRUDHermesAgentRuntimeState(HermesAgentRuntimeState)
