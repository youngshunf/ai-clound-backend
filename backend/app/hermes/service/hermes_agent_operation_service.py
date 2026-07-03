from collections.abc import Sequence
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from backend.app.hermes.crud.crud_hermes_agent_operation import hermes_agent_operation_dao
from backend.app.hermes.model import HermesAgentOperation
from backend.app.hermes.schema.hermes_agent_operation import (
    CreateHermesAgentOperationParam,
    DeleteHermesAgentOperationParam,
    UpdateHermesAgentOperationParam,
)
from backend.common.exception import errors
from backend.common.pagination import paging_data


class HermesAgentOperationService:
    @staticmethod
    async def get(*, db: AsyncSession, pk: int) -> HermesAgentOperation:
        """
        获取Hermes Agent 操作记录

        :param db: 数据库会话
        :param pk: Hermes Agent 操作记录 ID
        :return:
        """
        hermes_agent_operation = await hermes_agent_operation_dao.get(db, pk)
        if not hermes_agent_operation:
            raise errors.NotFoundError(msg='Hermes Agent 操作记录不存在')
        return hermes_agent_operation

    @staticmethod
    async def get_list(db: AsyncSession) -> dict[str, Any]:
        """
        获取Hermes Agent 操作记录列表

        :param db: 数据库会话
        :return:
        """
        hermes_agent_operation_select = await hermes_agent_operation_dao.get_select()
        return await paging_data(db, hermes_agent_operation_select)

    @staticmethod
    async def get_all(*, db: AsyncSession) -> Sequence[HermesAgentOperation]:
        """
        获取所有Hermes Agent 操作记录

        :param db: 数据库会话
        :return:
        """
        hermes_agent_operations = await hermes_agent_operation_dao.get_all(db)
        return hermes_agent_operations

    @staticmethod
    async def create(*, db: AsyncSession, obj: CreateHermesAgentOperationParam) -> None:
        """
        创建Hermes Agent 操作记录

        :param db: 数据库会话
        :param obj: 创建Hermes Agent 操作记录参数
        :return:
        """
        await hermes_agent_operation_dao.create(db, obj)

    @staticmethod
    async def update(*, db: AsyncSession, pk: int, obj: UpdateHermesAgentOperationParam) -> int:
        """
        更新Hermes Agent 操作记录

        :param db: 数据库会话
        :param pk: Hermes Agent 操作记录 ID
        :param obj: 更新Hermes Agent 操作记录参数
        :return:
        """
        count = await hermes_agent_operation_dao.update(db, pk, obj)
        return count

    @staticmethod
    async def delete(*, db: AsyncSession, obj: DeleteHermesAgentOperationParam) -> int:
        """
        删除Hermes Agent 操作记录

        :param db: 数据库会话
        :param obj: Hermes Agent 操作记录 ID 列表
        :return:
        """
        count = await hermes_agent_operation_dao.delete(db, obj.pks)
        return count


hermes_agent_operation_service: HermesAgentOperationService = HermesAgentOperationService()
