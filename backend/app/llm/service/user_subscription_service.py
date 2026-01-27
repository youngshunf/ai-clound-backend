from typing import Any, Sequence

from sqlalchemy.ext.asyncio import AsyncSession

from backend.app.llm.crud.crud_user_subscription import user_subscription_dao
from backend.app.llm.model import UserSubscription
from backend.app.llm.schema.user_subscription import CreateUserSubscriptionParam, DeleteUserSubscriptionParam, UpdateUserSubscriptionParam
from backend.common.exception import errors
from backend.common.pagination import paging_data


class UserSubscriptionService:
    @staticmethod
    async def get(*, db: AsyncSession, pk: int) -> UserSubscription:
        """
        获取用户订阅表 - 管理用户的订阅等级和积分余额

        :param db: 数据库会话
        :param pk: 用户订阅表 - 管理用户的订阅等级和积分余额 ID
        :return:
        """
        user_subscription = await user_subscription_dao.get(db, pk)
        if not user_subscription:
            raise errors.NotFoundError(msg='用户订阅表 - 管理用户的订阅等级和积分余额不存在')
        return user_subscription

    @staticmethod
    async def get_list(db: AsyncSession) -> dict[str, Any]:
        """
        获取用户订阅表 - 管理用户的订阅等级和积分余额列表

        :param db: 数据库会话
        :return:
        """
        user_subscription_select = await user_subscription_dao.get_select()
        return await paging_data(db, user_subscription_select)

    @staticmethod
    async def get_all(*, db: AsyncSession) -> Sequence[UserSubscription]:
        """
        获取所有用户订阅表 - 管理用户的订阅等级和积分余额

        :param db: 数据库会话
        :return:
        """
        user_subscriptions = await user_subscription_dao.get_all(db)
        return user_subscriptions

    @staticmethod
    async def create(*, db: AsyncSession, obj: CreateUserSubscriptionParam) -> None:
        """
        创建用户订阅表 - 管理用户的订阅等级和积分余额

        :param db: 数据库会话
        :param obj: 创建用户订阅表 - 管理用户的订阅等级和积分余额参数
        :return:
        """
        await user_subscription_dao.create(db, obj)

    @staticmethod
    async def update(*, db: AsyncSession, pk: int, obj: UpdateUserSubscriptionParam) -> int:
        """
        更新用户订阅表 - 管理用户的订阅等级和积分余额

        :param db: 数据库会话
        :param pk: 用户订阅表 - 管理用户的订阅等级和积分余额 ID
        :param obj: 更新用户订阅表 - 管理用户的订阅等级和积分余额参数
        :return:
        """
        count = await user_subscription_dao.update(db, pk, obj)
        return count

    @staticmethod
    async def delete(*, db: AsyncSession, obj: DeleteUserSubscriptionParam) -> int:
        """
        删除用户订阅表 - 管理用户的订阅等级和积分余额

        :param db: 数据库会话
        :param obj: 用户订阅表 - 管理用户的订阅等级和积分余额 ID 列表
        :return:
        """
        count = await user_subscription_dao.delete(db, obj.pks)
        return count


user_subscription_service: UserSubscriptionService = UserSubscriptionService()
