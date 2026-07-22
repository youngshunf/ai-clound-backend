from collections.abc import Sequence
from typing import Any, cast

from sqlalchemy import Select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy_crud_plus import CRUDPlus

from backend.app.billing.model import SubscriptionTier
from backend.app.billing.schema.subscription_tier import CreateSubscriptionTierParam, UpdateSubscriptionTierParam


class CRUDSubscriptionTier(CRUDPlus[SubscriptionTier]):
    async def get_by_tier_name(
        self,
        db: AsyncSession,
        tier_name: str,
        *,
        app_code: str,
        enabled: bool | None = None,
    ) -> SubscriptionTier | None:
        """按等级标识读取单个订阅配置，不支持关联查询结果。"""
        filters: dict[str, Any] = {'tier_name': tier_name, 'app_code': app_code}
        if enabled is not None:
            filters['enabled'] = enabled
        result = await self.select_model_by_column(db, **filters)
        if result is not None and not isinstance(result, SubscriptionTier):
            raise TypeError('订阅等级单模型查询返回了关联结果')
        return cast(SubscriptionTier | None, result)

    async def get(self, db: AsyncSession, pk: int) -> SubscriptionTier | None:
        """
        获取订阅等级配置

        :param db: 数据库会话
        :param pk: 订阅等级配置 ID
        :return:
        """
        result = await self.select_model(db, pk)
        if result is not None and not isinstance(result, SubscriptionTier):
            raise TypeError('订阅等级主键查询返回了关联结果')
        return cast(SubscriptionTier | None, result)

    async def get_select(self) -> Select:
        """获取订阅等级配置列表查询表达式"""
        return await self.select_order('id', 'desc')

    async def get_all(self, db: AsyncSession) -> Sequence[SubscriptionTier]:
        """
        获取所有订阅等级配置

        :param db: 数据库会话
        :return:
        """
        result = await self.select_models(db)
        if not all(isinstance(item, SubscriptionTier) for item in result):
            raise TypeError('订阅等级列表查询返回了关联结果')
        return cast(Sequence[SubscriptionTier], result)

    async def create(self, db: AsyncSession, obj: CreateSubscriptionTierParam) -> None:
        """
        创建订阅等级配置

        :param db: 数据库会话
        :param obj: 创建订阅等级配置参数
        :return:
        """
        await self.create_model(db, obj)

    async def update(self, db: AsyncSession, pk: int, obj: UpdateSubscriptionTierParam) -> int:
        """
        更新订阅等级配置

        :param db: 数据库会话
        :param pk: 订阅等级配置 ID
        :param obj: 更新 订阅等级配置参数
        :return:
        """
        return await self.update_model(db, pk, obj)

    async def delete(self, db: AsyncSession, pks: list[int]) -> int:
        """
        批量删除订阅等级配置

        :param db: 数据库会话
        :param pks: 订阅等级配置 ID 列表
        :return:
        """
        return await self.delete_model_by_column(db, allow_multiple=True, id__in=pks)


subscription_tier_dao: CRUDSubscriptionTier = CRUDSubscriptionTier(SubscriptionTier)
