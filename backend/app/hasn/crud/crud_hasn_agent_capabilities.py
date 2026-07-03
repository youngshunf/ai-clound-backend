from collections.abc import Sequence

from sqlalchemy import Select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy_crud_plus import CRUDPlus

from backend.app.hasn.model import HasnAgentCapabilities
from backend.app.hasn.schema.hasn_agent_capabilities import (
    CreateHasnAgentCapabilitiesParam,
    UpdateHasnAgentCapabilitiesParam,
)


class CRUDHasnAgentCapabilities(CRUDPlus[HasnAgentCapabilities]):
    async def get(self, db: AsyncSession, pk: int) -> HasnAgentCapabilities | None:
        """
        获取HASN Agent 能力声明

        :param db: 数据库会话
        :param pk: HASN Agent 能力声明 ID
        :return:
        """
        return await self.select_model(db, pk)

    async def get_select(self) -> Select:
        """获取HASN Agent 能力声明列表查询表达式"""
        return await self.select_order('id', 'desc')

    async def get_all(self, db: AsyncSession) -> Sequence[HasnAgentCapabilities]:
        """
        获取所有HASN Agent 能力声明

        :param db: 数据库会话
        :return:
        """
        return await self.select_models(db)

    async def create(self, db: AsyncSession, obj: CreateHasnAgentCapabilitiesParam) -> None:
        """
        创建HASN Agent 能力声明

        :param db: 数据库会话
        :param obj: 创建HASN Agent 能力声明参数
        :return:
        """
        await self.create_model(db, obj)

    async def update(self, db: AsyncSession, pk: int, obj: UpdateHasnAgentCapabilitiesParam) -> int:
        """
        更新HASN Agent 能力声明

        :param db: 数据库会话
        :param pk: HASN Agent 能力声明 ID
        :param obj: 更新 HASN Agent 能力声明参数
        :return:
        """
        return await self.update_model(db, pk, obj)

    async def delete(self, db: AsyncSession, pks: list[int]) -> int:
        """
        批量删除HASN Agent 能力声明

        :param db: 数据库会话
        :param pks: HASN Agent 能力声明 ID 列表
        :return:
        """
        return await self.delete_model_by_column(db, allow_multiple=True, id__in=pks)


hasn_agent_capabilities_dao: CRUDHasnAgentCapabilities = CRUDHasnAgentCapabilities(HasnAgentCapabilities)
