from collections.abc import Sequence

from sqlalchemy import Select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy_crud_plus import CRUDPlus

from backend.app.hermes.model import HermesAgentLlmToken
from backend.app.hermes.schema.hermes_agent_llm_token import (
    CreateHermesAgentLlmTokenParam,
    UpdateHermesAgentLlmTokenParam,
)


class CRUDHermesAgentLlmToken(CRUDPlus[HermesAgentLlmToken]):
    async def get(self, db: AsyncSession, pk: int) -> HermesAgentLlmToken | None:
        """
        获取Hermes Agent 级 LLM token 隔离记录

        :param db: 数据库会话
        :param pk: Hermes Agent 级 LLM token 隔离记录 ID
        :return:
        """
        return await self.select_model(db, pk)

    async def get_select(self) -> Select:
        """获取Hermes Agent 级 LLM token 隔离记录列表查询表达式"""
        return await self.select_order('id', 'desc')

    async def get_all(self, db: AsyncSession) -> Sequence[HermesAgentLlmToken]:
        """
        获取所有Hermes Agent 级 LLM token 隔离记录

        :param db: 数据库会话
        :return:
        """
        return await self.select_models(db)

    async def create(self, db: AsyncSession, obj: CreateHermesAgentLlmTokenParam) -> None:
        """
        创建Hermes Agent 级 LLM token 隔离记录

        :param db: 数据库会话
        :param obj: 创建Hermes Agent 级 LLM token 隔离记录参数
        :return:
        """
        await self.create_model(db, obj)

    async def update(self, db: AsyncSession, pk: int, obj: UpdateHermesAgentLlmTokenParam) -> int:
        """
        更新Hermes Agent 级 LLM token 隔离记录

        :param db: 数据库会话
        :param pk: Hermes Agent 级 LLM token 隔离记录 ID
        :param obj: 更新 Hermes Agent 级 LLM token 隔离记录参数
        :return:
        """
        return await self.update_model(db, pk, obj)

    async def delete(self, db: AsyncSession, pks: list[int]) -> int:
        """
        批量删除Hermes Agent 级 LLM token 隔离记录

        :param db: 数据库会话
        :param pks: Hermes Agent 级 LLM token 隔离记录 ID 列表
        :return:
        """
        return await self.delete_model_by_column(db, allow_multiple=True, id__in=pks)


hermes_agent_llm_token_dao: CRUDHermesAgentLlmToken = CRUDHermesAgentLlmToken(HermesAgentLlmToken)
