from typing import Any, Sequence

from sqlalchemy.ext.asyncio import AsyncSession

from backend.app.llm.crud.crud_subscription_tier import subscription_tier_dao
from backend.app.llm.model import SubscriptionTier
from backend.app.llm.schema.subscription_tier import CreateSubscriptionTierParam, DeleteSubscriptionTierParam, UpdateSubscriptionTierParam
from backend.common.exception import errors
from backend.common.pagination import paging_data


class SubscriptionTierService:
    @staticmethod
    async def get(*, db: AsyncSession, pk: int) -> SubscriptionTier:
        """
        获取订阅等级配置表 - 定义不同订阅等级的权益

        :param db: 数据库会话
        :param pk: 订阅等级配置表 - 定义不同订阅等级的权益 ID
        :return:
        """
        subscription_tier = await subscription_tier_dao.get(db, pk)
        if not subscription_tier:
            raise errors.NotFoundError(msg='订阅等级配置表 - 定义不同订阅等级的权益不存在')
        return subscription_tier

    @staticmethod
    async def get_list(db: AsyncSession) -> dict[str, Any]:
        """
        获取订阅等级配置表 - 定义不同订阅等级的权益列表

        :param db: 数据库会话
        :return:
        """
        subscription_tier_select = await subscription_tier_dao.get_select()
        return await paging_data(db, subscription_tier_select)

    @staticmethod
    async def get_all(*, db: AsyncSession) -> Sequence[SubscriptionTier]:
        """
        获取所有订阅等级配置表 - 定义不同订阅等级的权益

        :param db: 数据库会话
        :return:
        """
        subscription_tiers = await subscription_tier_dao.get_all(db)
        return subscription_tiers

    @staticmethod
    async def create(*, db: AsyncSession, obj: CreateSubscriptionTierParam) -> None:
        """
        创建订阅等级配置表 - 定义不同订阅等级的权益

        :param db: 数据库会话
        :param obj: 创建订阅等级配置表 - 定义不同订阅等级的权益参数
        :return:
        """
        await subscription_tier_dao.create(db, obj)

    @staticmethod
    async def update(*, db: AsyncSession, pk: int, obj: UpdateSubscriptionTierParam) -> int:
        """
        更新订阅等级配置表 - 定义不同订阅等级的权益

        :param db: 数据库会话
        :param pk: 订阅等级配置表 - 定义不同订阅等级的权益 ID
        :param obj: 更新订阅等级配置表 - 定义不同订阅等级的权益参数
        :return:
        """
        count = await subscription_tier_dao.update(db, pk, obj)
        return count

    @staticmethod
    async def delete(*, db: AsyncSession, obj: DeleteSubscriptionTierParam) -> int:
        """
        删除订阅等级配置表 - 定义不同订阅等级的权益

        :param db: 数据库会话
        :param obj: 订阅等级配置表 - 定义不同订阅等级的权益 ID 列表
        :return:
        """
        count = await subscription_tier_dao.delete(db, obj.pks)
        return count


subscription_tier_service: SubscriptionTierService = SubscriptionTierService()
