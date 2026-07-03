from collections.abc import Sequence
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from backend.app.hasn.crud.crud_hasn_agent_runtime_reports import hasn_agent_runtime_reports_dao
from backend.app.hasn.model import HasnAgentRuntimeReports
from backend.app.hasn.schema.hasn_agent_runtime_reports import (
    CreateHasnAgentRuntimeReportsParam,
    DeleteHasnAgentRuntimeReportsParam,
    UpdateHasnAgentRuntimeReportsParam,
)
from backend.common.exception import errors
from backend.common.pagination import paging_data


class HasnAgentRuntimeReportsService:
    @staticmethod
    async def get(*, db: AsyncSession, pk: int) -> HasnAgentRuntimeReports:
        """
        获取HASN Agent Runtime 脱敏摘要上报

        :param db: 数据库会话
        :param pk: HASN Agent Runtime 脱敏摘要上报 ID
        :return:
        """
        hasn_agent_runtime_reports = await hasn_agent_runtime_reports_dao.get(db, pk)
        if not hasn_agent_runtime_reports:
            raise errors.NotFoundError(msg='HASN Agent Runtime 脱敏摘要上报不存在')
        return hasn_agent_runtime_reports

    @staticmethod
    async def get_list(db: AsyncSession) -> dict[str, Any]:
        """
        获取HASN Agent Runtime 脱敏摘要上报列表

        :param db: 数据库会话
        :return:
        """
        hasn_agent_runtime_reports_select = await hasn_agent_runtime_reports_dao.get_select()
        return await paging_data(db, hasn_agent_runtime_reports_select)

    @staticmethod
    async def get_all(*, db: AsyncSession) -> Sequence[HasnAgentRuntimeReports]:
        """
        获取所有HASN Agent Runtime 脱敏摘要上报

        :param db: 数据库会话
        :return:
        """
        hasn_agent_runtime_reportss = await hasn_agent_runtime_reports_dao.get_all(db)
        return hasn_agent_runtime_reportss

    @staticmethod
    async def create(*, db: AsyncSession, obj: CreateHasnAgentRuntimeReportsParam) -> None:
        """
        创建HASN Agent Runtime 脱敏摘要上报

        :param db: 数据库会话
        :param obj: 创建HASN Agent Runtime 脱敏摘要上报参数
        :return:
        """
        await hasn_agent_runtime_reports_dao.create(db, obj)

    @staticmethod
    async def update(*, db: AsyncSession, pk: int, obj: UpdateHasnAgentRuntimeReportsParam) -> int:
        """
        更新HASN Agent Runtime 脱敏摘要上报

        :param db: 数据库会话
        :param pk: HASN Agent Runtime 脱敏摘要上报 ID
        :param obj: 更新HASN Agent Runtime 脱敏摘要上报参数
        :return:
        """
        count = await hasn_agent_runtime_reports_dao.update(db, pk, obj)
        return count

    @staticmethod
    async def delete(*, db: AsyncSession, obj: DeleteHasnAgentRuntimeReportsParam) -> int:
        """
        删除HASN Agent Runtime 脱敏摘要上报

        :param db: 数据库会话
        :param obj: HASN Agent Runtime 脱敏摘要上报 ID 列表
        :return:
        """
        count = await hasn_agent_runtime_reports_dao.delete(db, obj.pks)
        return count


hasn_agent_runtime_reports_service: HasnAgentRuntimeReportsService = HasnAgentRuntimeReportsService()
