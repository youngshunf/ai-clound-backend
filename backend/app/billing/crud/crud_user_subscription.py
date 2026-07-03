from collections.abc import Sequence
from datetime import date, datetime

from sqlalchemy import Select, or_, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy_crud_plus import CRUDPlus

from backend.app.admin.model import User
from backend.app.billing.model import UserSubscription
from backend.app.billing.schema.user_subscription import CreateUserSubscriptionParam, UpdateUserSubscriptionParam


class CRUDUserSubscription(CRUDPlus[UserSubscription]):
    async def get(self, db: AsyncSession, pk: int) -> UserSubscription | None:
        """
        获取用户订阅

        :param db: 数据库会话
        :param pk: 用户订阅 ID
        :return:
        """
        return await self.select_model(db, pk)

    async def get_select(
        self,
        *,
        user_keyword: str | None = None,
        billing_cycle_start: list[date] | None = None,
        billing_cycle_end: list[date] | None = None,
        status: str | None = None,
    ) -> Select:
        """获取用户订阅列表查询表达式，支持用户昵称/手机号搜索"""
        stmt = select(
            UserSubscription.id,
            UserSubscription.user_id,
            User.nickname.label('user_nickname'),
            User.phone.label('user_phone'),
            UserSubscription.tier,
            UserSubscription.subscription_type,
            UserSubscription.monthly_credits,
            UserSubscription.current_credits,
            UserSubscription.used_credits,
            UserSubscription.purchased_credits,
            UserSubscription.billing_cycle_start,
            UserSubscription.billing_cycle_end,
            UserSubscription.subscription_start_date,
            UserSubscription.subscription_end_date,
            UserSubscription.next_grant_date,
            UserSubscription.status,
            UserSubscription.auto_renew,
            UserSubscription.created_time,
            UserSubscription.updated_time,
        ).outerjoin(User, UserSubscription.user_id == User.id)

        if user_keyword is not None:
            stmt = stmt.where(
                or_(
                    User.nickname.ilike(f'%{user_keyword}%'),
                    User.phone.ilike(f'%{user_keyword}%'),
                )
            )
        if billing_cycle_start is not None and len(billing_cycle_start) == 2:
            stmt = stmt.where(UserSubscription.billing_cycle_start >= datetime.combine(billing_cycle_start[0], datetime.min.time()))
            stmt = stmt.where(UserSubscription.billing_cycle_start <= datetime.combine(billing_cycle_start[1], datetime.max.time()))
        if billing_cycle_end is not None and len(billing_cycle_end) == 2:
            stmt = stmt.where(UserSubscription.billing_cycle_end >= datetime.combine(billing_cycle_end[0], datetime.min.time()))
            stmt = stmt.where(UserSubscription.billing_cycle_end <= datetime.combine(billing_cycle_end[1], datetime.max.time()))
        if status is not None:
            stmt = stmt.where(UserSubscription.status == status)

        stmt = stmt.order_by(UserSubscription.id.desc())
        return stmt

    async def get_all(self, db: AsyncSession) -> Sequence[UserSubscription]:
        """
        获取所有用户订阅

        :param db: 数据库会话
        :return:
        """
        return await self.select_models(db)

    async def create(self, db: AsyncSession, obj: CreateUserSubscriptionParam) -> None:
        """
        创建用户订阅

        :param db: 数据库会话
        :param obj: 创建用户订阅参数
        :return:
        """
        await self.create_model(db, obj)

    async def update(self, db: AsyncSession, pk: int, obj: UpdateUserSubscriptionParam) -> int:
        """
        更新用户订阅

        :param db: 数据库会话
        :param pk: 用户订阅 ID
        :param obj: 更新 用户订阅参数
        :return:
        """
        return await self.update_model(db, pk, obj)

    async def delete(self, db: AsyncSession, pks: list[int]) -> int:
        """
        批量删除用户订阅

        :param db: 数据库会话
        :param pks: 用户订阅 ID 列表
        :return:
        """
        return await self.delete_model_by_column(db, allow_multiple=True, id__in=pks)


user_subscription_dao: CRUDUserSubscription = CRUDUserSubscription(UserSubscription)
