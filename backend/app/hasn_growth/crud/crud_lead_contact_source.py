from collections.abc import Sequence

from sqlalchemy import Select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy_crud_plus import CRUDPlus

from backend.app.hasn_growth.model import LeadContactSource
from backend.app.hasn_growth.schema.lead_contact_source import (
    CreateLeadContactSourceParam,
    UpdateLeadContactSourceParam,
)


class CRUDLeadContactSource(CRUDPlus[LeadContactSource]):
    async def get(self, db: AsyncSession, pk: int) -> LeadContactSource | None:
        """
        获取Lead multi-source evidence

        :param db: 数据库会话
        :param pk: Lead multi-source evidence ID
        :return:
        """
        return await self.select_model(db, pk)

    async def get_select(self) -> Select:
        """获取Lead multi-source evidence列表查询表达式"""
        return await self.select_order('id', 'desc')

    async def get_all(self, db: AsyncSession) -> Sequence[LeadContactSource]:
        """
        获取所有Lead multi-source evidence

        :param db: 数据库会话
        :return:
        """
        return await self.select_models(db)

    async def create(self, db: AsyncSession, obj: CreateLeadContactSourceParam) -> None:
        """
        创建Lead multi-source evidence

        :param db: 数据库会话
        :param obj: 创建Lead multi-source evidence参数
        :return:
        """
        await self.create_model(db, obj)

    async def update(self, db: AsyncSession, pk: int, obj: UpdateLeadContactSourceParam) -> int:
        """
        更新Lead multi-source evidence

        :param db: 数据库会话
        :param pk: Lead multi-source evidence ID
        :param obj: 更新 Lead multi-source evidence参数
        :return:
        """
        return await self.update_model(db, pk, obj)

    async def delete(self, db: AsyncSession, pks: list[int]) -> int:
        """
        批量删除Lead multi-source evidence

        :param db: 数据库会话
        :param pks: Lead multi-source evidence ID 列表
        :return:
        """
        return await self.delete_model_by_column(db, allow_multiple=True, id__in=pks)


lead_contact_source_dao: CRUDLeadContactSource = CRUDLeadContactSource(LeadContactSource)
