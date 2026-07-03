from collections.abc import Sequence

from sqlalchemy import Select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy_crud_plus import CRUDPlus

from backend.app.hasn.model import HasnAgentRuntimeReports
from backend.app.hasn.schema.hasn_agent_runtime_reports import (
    CreateHasnAgentRuntimeReportsParam,
    UpdateHasnAgentRuntimeReportsParam,
)


class CRUDHasnAgentRuntimeReports(CRUDPlus[HasnAgentRuntimeReports]):
    async def get(self, db: AsyncSession, pk: int) -> HasnAgentRuntimeReports | None:
        """
        获取HASN Agent Runtime 脱敏摘要上报

        :param db: 数据库会话
        :param pk: HASN Agent Runtime 脱敏摘要上报 ID
        :return:
        """
        return await self.select_model(db, pk)

    async def get_select(self) -> Select:
        """获取HASN Agent Runtime 脱敏摘要上报列表查询表达式"""
        return await self.select_order('id', 'desc')

    async def get_all(self, db: AsyncSession) -> Sequence[HasnAgentRuntimeReports]:
        """
        获取所有HASN Agent Runtime 脱敏摘要上报

        :param db: 数据库会话
        :return:
        """
        return await self.select_models(db)

    async def create(self, db: AsyncSession, obj: CreateHasnAgentRuntimeReportsParam) -> None:
        """
        创建HASN Agent Runtime 脱敏摘要上报

        :param db: 数据库会话
        :param obj: 创建HASN Agent Runtime 脱敏摘要上报参数
        :return:
        """
        await self.create_model(db, obj)

    async def update(self, db: AsyncSession, pk: int, obj: UpdateHasnAgentRuntimeReportsParam) -> int:
        """
        更新HASN Agent Runtime 脱敏摘要上报

        :param db: 数据库会话
        :param pk: HASN Agent Runtime 脱敏摘要上报 ID
        :param obj: 更新 HASN Agent Runtime 脱敏摘要上报参数
        :return:
        """
        return await self.update_model(db, pk, obj)

    async def delete(self, db: AsyncSession, pks: list[int]) -> int:
        """
        批量删除HASN Agent Runtime 脱敏摘要上报

        :param db: 数据库会话
        :param pks: HASN Agent Runtime 脱敏摘要上报 ID 列表
        :return:
        """
        return await self.delete_model_by_column(db, allow_multiple=True, id__in=pks)


hasn_agent_runtime_reports_dao: CRUDHasnAgentRuntimeReports = CRUDHasnAgentRuntimeReports(HasnAgentRuntimeReports)
