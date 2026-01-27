from typing import Sequence

from sqlalchemy import Select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy_crud_plus import CRUDPlus

from backend.app.llm.model import UserSubscription
from backend.app.llm.schema.user_subscription import CreateUserSubscriptionParam, UpdateUserSubscriptionParam


class CRUDUserSubscription(CRUDPlus[UserSubscription]):
    async def get(self, db: AsyncSession, pk: int) -> UserSubscription | None:
        """
        获取用户订阅表 - 管理用户的订阅等级和积分余额

        :param db: 数据库会话
        :param pk: 用户订阅表 - 管理用户的订阅等级和积分余额 ID
        :return:
        """
        return await self.select_model(db, pk)

    async def get_select(self) -> Select:
        """获取用户订阅表 - 管理用户的订阅等级和积分余额列表查询表达式"""
        return await self.select_order('id', 'desc')

    async def get_all(self, db: AsyncSession) -> Sequence[UserSubscription]:
        """
        获取所有用户订阅表 - 管理用户的订阅等级和积分余额

        :param db: 数据库会话
        :return:
        """
        return await self.select_models(db)

    async def create(self, db: AsyncSession, obj: CreateUserSubscriptionParam) -> None:
        """
        创建用户订阅表 - 管理用户的订阅等级和积分余额

        :param db: 数据库会话
        :param obj: 创建用户订阅表 - 管理用户的订阅等级和积分余额参数
        :return:
        """
        await self.create_model(db, obj)

    async def update(self, db: AsyncSession, pk: int, obj: UpdateUserSubscriptionParam) -> int:
        """
        更新用户订阅表 - 管理用户的订阅等级和积分余额

        :param db: 数据库会话
        :param pk: 用户订阅表 - 管理用户的订阅等级和积分余额 ID
        :param obj: 更新 用户订阅表 - 管理用户的订阅等级和积分余额参数
        :return:
        """
        return await self.update_model(db, pk, obj)

    async def delete(self, db: AsyncSession, pks: list[int]) -> int:
        """
        批量删除用户订阅表 - 管理用户的订阅等级和积分余额

        :param db: 数据库会话
        :param pks: 用户订阅表 - 管理用户的订阅等级和积分余额 ID 列表
        :return:
        """
        return await self.delete_model_by_column(db, allow_multiple=True, id__in=pks)


user_subscription_dao: CRUDUserSubscription = CRUDUserSubscription(UserSubscription)
