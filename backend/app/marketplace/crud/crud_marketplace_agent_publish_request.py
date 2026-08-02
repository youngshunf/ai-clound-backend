from typing import Sequence

from sqlalchemy import Select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy_crud_plus import CRUDPlus

from backend.app.marketplace.model import MarketplaceAgentPublishRequest
from backend.app.marketplace.schema.marketplace_agent_publish_request import CreateMarketplaceAgentPublishRequestParam, UpdateMarketplaceAgentPublishRequestParam


class CRUDMarketplaceAgentPublishRequest(CRUDPlus[MarketplaceAgentPublishRequest]):
    async def get(self, db: AsyncSession, pk: int) -> MarketplaceAgentPublishRequest | None:
        """
        获取Agent 市场发布幂等请求

        :param db: 数据库会话
        :param pk: Agent 市场发布幂等请求 ID
        :return:
        """
        return await self.select_model(db, pk)

    async def get_select(self) -> Select:
        """获取Agent 市场发布幂等请求列表查询表达式"""
        return await self.select_order('id', 'desc')

    async def get_all(self, db: AsyncSession) -> Sequence[MarketplaceAgentPublishRequest]:
        """
        获取所有Agent 市场发布幂等请求

        :param db: 数据库会话
        :return:
        """
        return await self.select_models(db)

    async def create(self, db: AsyncSession, obj: CreateMarketplaceAgentPublishRequestParam) -> None:
        """
        创建Agent 市场发布幂等请求

        :param db: 数据库会话
        :param obj: 创建Agent 市场发布幂等请求参数
        :return:
        """
        await self.create_model(db, obj)

    async def update(self, db: AsyncSession, pk: int, obj: UpdateMarketplaceAgentPublishRequestParam) -> int:
        """
        更新Agent 市场发布幂等请求

        :param db: 数据库会话
        :param pk: Agent 市场发布幂等请求 ID
        :param obj: 更新 Agent 市场发布幂等请求参数
        :return:
        """
        return await self.update_model(db, pk, obj)

    async def delete(self, db: AsyncSession, pks: list[int]) -> int:
        """
        批量删除Agent 市场发布幂等请求

        :param db: 数据库会话
        :param pks: Agent 市场发布幂等请求 ID 列表
        :return:
        """
        return await self.delete_model_by_column(db, allow_multiple=True, id__in=pks)


marketplace_agent_publish_request_dao: CRUDMarketplaceAgentPublishRequest = CRUDMarketplaceAgentPublishRequest(MarketplaceAgentPublishRequest)
