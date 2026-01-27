from typing import Sequence

from sqlalchemy import Select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy_crud_plus import CRUDPlus

from backend.app.llm.model import SubscriptionTier
from backend.app.llm.schema.subscription_tier import CreateSubscriptionTierParam, UpdateSubscriptionTierParam


class CRUDSubscriptionTier(CRUDPlus[SubscriptionTier]):
    async def get(self, db: AsyncSession, pk: int) -> SubscriptionTier | None:
        """
        获取订阅等级配置表 - 定义不同订阅等级的权益

        :param db: 数据库会话
        :param pk: 订阅等级配置表 - 定义不同订阅等级的权益 ID
        :return:
        """
        return await self.select_model(db, pk)

    async def get_select(self) -> Select:
        """获取订阅等级配置表 - 定义不同订阅等级的权益列表查询表达式"""
        return await self.select_order('id', 'desc')

    async def get_all(self, db: AsyncSession) -> Sequence[SubscriptionTier]:
        """
        获取所有订阅等级配置表 - 定义不同订阅等级的权益

        :param db: 数据库会话
        :return:
        """
        return await self.select_models(db)

    async def create(self, db: AsyncSession, obj: CreateSubscriptionTierParam) -> None:
        """
        创建订阅等级配置表 - 定义不同订阅等级的权益

        :param db: 数据库会话
        :param obj: 创建订阅等级配置表 - 定义不同订阅等级的权益参数
        :return:
        """
        await self.create_model(db, obj)

    async def update(self, db: AsyncSession, pk: int, obj: UpdateSubscriptionTierParam) -> int:
        """
        更新订阅等级配置表 - 定义不同订阅等级的权益

        :param db: 数据库会话
        :param pk: 订阅等级配置表 - 定义不同订阅等级的权益 ID
        :param obj: 更新 订阅等级配置表 - 定义不同订阅等级的权益参数
        :return:
        """
        return await self.update_model(db, pk, obj)

    async def delete(self, db: AsyncSession, pks: list[int]) -> int:
        """
        批量删除订阅等级配置表 - 定义不同订阅等级的权益

        :param db: 数据库会话
        :param pks: 订阅等级配置表 - 定义不同订阅等级的权益 ID 列表
        :return:
        """
        return await self.delete_model_by_column(db, allow_multiple=True, id__in=pks)


subscription_tier_dao: CRUDSubscriptionTier = CRUDSubscriptionTier(SubscriptionTier)
