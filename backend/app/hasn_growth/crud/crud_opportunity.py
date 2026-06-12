from typing import Sequence

from sqlalchemy import Select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy_crud_plus import CRUDPlus

from backend.app.hasn_growth.model import Opportunity
from backend.app.hasn_growth.schema.opportunity import CreateOpportunityParam, UpdateOpportunityParam


class CRUDOpportunity(CRUDPlus[Opportunity]):
    async def get(self, db: AsyncSession, pk: int) -> Opportunity | None:
        """
        获取获客商机（阶段推进 + 金额 + 成交/败因登记）

        :param db: 数据库会话
        :param pk: 获客商机（阶段推进 + 金额 + 成交/败因登记） ID
        :return:
        """
        return await self.select_model(db, pk)

    async def get_select(self) -> Select:
        """获取获客商机（阶段推进 + 金额 + 成交/败因登记）列表查询表达式"""
        return await self.select_order('id', 'desc')

    async def get_all(self, db: AsyncSession) -> Sequence[Opportunity]:
        """
        获取所有获客商机（阶段推进 + 金额 + 成交/败因登记）

        :param db: 数据库会话
        :return:
        """
        return await self.select_models(db)

    async def create(self, db: AsyncSession, obj: CreateOpportunityParam) -> None:
        """
        创建获客商机（阶段推进 + 金额 + 成交/败因登记）

        :param db: 数据库会话
        :param obj: 创建获客商机（阶段推进 + 金额 + 成交/败因登记）参数
        :return:
        """
        await self.create_model(db, obj)

    async def update(self, db: AsyncSession, pk: int, obj: UpdateOpportunityParam) -> int:
        """
        更新获客商机（阶段推进 + 金额 + 成交/败因登记）

        :param db: 数据库会话
        :param pk: 获客商机（阶段推进 + 金额 + 成交/败因登记） ID
        :param obj: 更新 获客商机（阶段推进 + 金额 + 成交/败因登记）参数
        :return:
        """
        return await self.update_model(db, pk, obj)

    async def delete(self, db: AsyncSession, pks: list[int]) -> int:
        """
        批量删除获客商机（阶段推进 + 金额 + 成交/败因登记）

        :param db: 数据库会话
        :param pks: 获客商机（阶段推进 + 金额 + 成交/败因登记） ID 列表
        :return:
        """
        return await self.delete_model_by_column(db, allow_multiple=True, id__in=pks)


opportunity_dao: CRUDOpportunity = CRUDOpportunity(Opportunity)
