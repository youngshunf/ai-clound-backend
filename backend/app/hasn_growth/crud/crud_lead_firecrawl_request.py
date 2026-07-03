from collections.abc import Sequence

from sqlalchemy import Select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy_crud_plus import CRUDPlus

from backend.app.hasn_growth.model import LeadFirecrawlRequest
from backend.app.hasn_growth.schema.lead_firecrawl_request import (
    CreateLeadFirecrawlRequestParam,
    UpdateLeadFirecrawlRequestParam,
)


class CRUDLeadFirecrawlRequest(CRUDPlus[LeadFirecrawlRequest]):
    async def get(self, db: AsyncSession, pk: int) -> LeadFirecrawlRequest | None:
        """
        获取Firecrawl request audit for AI lead automation

        :param db: 数据库会话
        :param pk: Firecrawl request audit for AI lead automation ID
        :return:
        """
        return await self.select_model(db, pk)

    async def get_select(self) -> Select:
        """获取Firecrawl request audit for AI lead automation列表查询表达式"""
        return await self.select_order('id', 'desc')

    async def get_all(self, db: AsyncSession) -> Sequence[LeadFirecrawlRequest]:
        """
        获取所有Firecrawl request audit for AI lead automation

        :param db: 数据库会话
        :return:
        """
        return await self.select_models(db)

    async def create(self, db: AsyncSession, obj: CreateLeadFirecrawlRequestParam) -> None:
        """
        创建Firecrawl request audit for AI lead automation

        :param db: 数据库会话
        :param obj: 创建Firecrawl request audit for AI lead automation参数
        :return:
        """
        await self.create_model(db, obj)

    async def update(self, db: AsyncSession, pk: int, obj: UpdateLeadFirecrawlRequestParam) -> int:
        """
        更新Firecrawl request audit for AI lead automation

        :param db: 数据库会话
        :param pk: Firecrawl request audit for AI lead automation ID
        :param obj: 更新 Firecrawl request audit for AI lead automation参数
        :return:
        """
        return await self.update_model(db, pk, obj)

    async def delete(self, db: AsyncSession, pks: list[int]) -> int:
        """
        批量删除Firecrawl request audit for AI lead automation

        :param db: 数据库会话
        :param pks: Firecrawl request audit for AI lead automation ID 列表
        :return:
        """
        return await self.delete_model_by_column(db, allow_multiple=True, id__in=pks)


lead_firecrawl_request_dao: CRUDLeadFirecrawlRequest = CRUDLeadFirecrawlRequest(LeadFirecrawlRequest)
