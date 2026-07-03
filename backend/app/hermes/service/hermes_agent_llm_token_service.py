from collections.abc import Sequence
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from backend.app.hermes.crud.crud_hermes_agent_llm_token import hermes_agent_llm_token_dao
from backend.app.hermes.model import HermesAgentLlmToken
from backend.app.hermes.schema.hermes_agent_llm_token import (
    CreateHermesAgentLlmTokenParam,
    DeleteHermesAgentLlmTokenParam,
    UpdateHermesAgentLlmTokenParam,
)
from backend.common.exception import errors
from backend.common.pagination import paging_data


class HermesAgentLlmTokenService:
    @staticmethod
    async def get(*, db: AsyncSession, pk: int) -> HermesAgentLlmToken:
        """
        获取Hermes Agent 级 LLM token 隔离记录

        :param db: 数据库会话
        :param pk: Hermes Agent 级 LLM token 隔离记录 ID
        :return:
        """
        hermes_agent_llm_token = await hermes_agent_llm_token_dao.get(db, pk)
        if not hermes_agent_llm_token:
            raise errors.NotFoundError(msg='Hermes Agent 级 LLM token 隔离记录不存在')
        return hermes_agent_llm_token

    @staticmethod
    async def get_list(db: AsyncSession) -> dict[str, Any]:
        """
        获取Hermes Agent 级 LLM token 隔离记录列表

        :param db: 数据库会话
        :return:
        """
        hermes_agent_llm_token_select = await hermes_agent_llm_token_dao.get_select()
        return await paging_data(db, hermes_agent_llm_token_select)

    @staticmethod
    async def get_all(*, db: AsyncSession) -> Sequence[HermesAgentLlmToken]:
        """
        获取所有Hermes Agent 级 LLM token 隔离记录

        :param db: 数据库会话
        :return:
        """
        hermes_agent_llm_tokens = await hermes_agent_llm_token_dao.get_all(db)
        return hermes_agent_llm_tokens

    @staticmethod
    async def create(*, db: AsyncSession, obj: CreateHermesAgentLlmTokenParam) -> None:
        """
        创建Hermes Agent 级 LLM token 隔离记录

        :param db: 数据库会话
        :param obj: 创建Hermes Agent 级 LLM token 隔离记录参数
        :return:
        """
        await hermes_agent_llm_token_dao.create(db, obj)

    @staticmethod
    async def update(*, db: AsyncSession, pk: int, obj: UpdateHermesAgentLlmTokenParam) -> int:
        """
        更新Hermes Agent 级 LLM token 隔离记录

        :param db: 数据库会话
        :param pk: Hermes Agent 级 LLM token 隔离记录 ID
        :param obj: 更新Hermes Agent 级 LLM token 隔离记录参数
        :return:
        """
        count = await hermes_agent_llm_token_dao.update(db, pk, obj)
        return count

    @staticmethod
    async def delete(*, db: AsyncSession, obj: DeleteHermesAgentLlmTokenParam) -> int:
        """
        删除Hermes Agent 级 LLM token 隔离记录

        :param db: 数据库会话
        :param obj: Hermes Agent 级 LLM token 隔离记录 ID 列表
        :return:
        """
        count = await hermes_agent_llm_token_dao.delete(db, obj.pks)
        return count


hermes_agent_llm_token_service: HermesAgentLlmTokenService = HermesAgentLlmTokenService()
