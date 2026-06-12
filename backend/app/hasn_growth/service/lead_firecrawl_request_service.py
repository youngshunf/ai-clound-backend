from typing import Any, Sequence

from sqlalchemy.ext.asyncio import AsyncSession

from backend.app.hasn_growth.crud.crud_lead_firecrawl_request import lead_firecrawl_request_dao
from backend.app.hasn_growth.model import LeadFirecrawlRequest
from backend.app.hasn_growth.schema.lead_firecrawl_request import CreateLeadFirecrawlRequestParam, DeleteLeadFirecrawlRequestParam, UpdateLeadFirecrawlRequestParam
from backend.common.exception import errors
from backend.common.pagination import paging_data


class LeadFirecrawlRequestService:
    @staticmethod
    async def get(*, db: AsyncSession, pk: int) -> LeadFirecrawlRequest:
        """
        获取Firecrawl request audit for AI lead automation

        :param db: 数据库会话
        :param pk: Firecrawl request audit for AI lead automation ID
        :return:
        """
        lead_firecrawl_request = await lead_firecrawl_request_dao.get(db, pk)
        if not lead_firecrawl_request:
            raise errors.NotFoundError(msg='Firecrawl request audit for AI lead automation不存在')
        return lead_firecrawl_request

    @staticmethod
    async def get_list(db: AsyncSession) -> dict[str, Any]:
        """
        获取Firecrawl request audit for AI lead automation列表

        :param db: 数据库会话
        :return:
        """
        lead_firecrawl_request_select = await lead_firecrawl_request_dao.get_select()
        return await paging_data(db, lead_firecrawl_request_select)

    @staticmethod
    async def get_all(*, db: AsyncSession) -> Sequence[LeadFirecrawlRequest]:
        """
        获取所有Firecrawl request audit for AI lead automation

        :param db: 数据库会话
        :return:
        """
        lead_firecrawl_requests = await lead_firecrawl_request_dao.get_all(db)
        return lead_firecrawl_requests

    @staticmethod
    async def create(*, db: AsyncSession, obj: CreateLeadFirecrawlRequestParam) -> None:
        """
        创建Firecrawl request audit for AI lead automation

        :param db: 数据库会话
        :param obj: 创建Firecrawl request audit for AI lead automation参数
        :return:
        """
        await lead_firecrawl_request_dao.create(db, obj)

    @staticmethod
    async def update(*, db: AsyncSession, pk: int, obj: UpdateLeadFirecrawlRequestParam) -> int:
        """
        更新Firecrawl request audit for AI lead automation

        :param db: 数据库会话
        :param pk: Firecrawl request audit for AI lead automation ID
        :param obj: 更新Firecrawl request audit for AI lead automation参数
        :return:
        """
        count = await lead_firecrawl_request_dao.update(db, pk, obj)
        return count

    @staticmethod
    async def delete(*, db: AsyncSession, obj: DeleteLeadFirecrawlRequestParam) -> int:
        """
        删除Firecrawl request audit for AI lead automation

        :param db: 数据库会话
        :param obj: Firecrawl request audit for AI lead automation ID 列表
        :return:
        """
        count = await lead_firecrawl_request_dao.delete(db, obj.pks)
        return count


lead_firecrawl_request_service: LeadFirecrawlRequestService = LeadFirecrawlRequestService()
