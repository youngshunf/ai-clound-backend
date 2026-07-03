from collections.abc import Sequence
from datetime import date, datetime

from sqlalchemy import Select, or_, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy_crud_plus import CRUDPlus

from backend.app.admin.model import User
from backend.app.billing.model import UserCreditBalance
from backend.app.billing.schema.user_credit_balance import CreateUserCreditBalanceParam, UpdateUserCreditBalanceParam


class CRUDUserCreditBalance(CRUDPlus[UserCreditBalance]):
    async def get(self, db: AsyncSession, pk: int) -> UserCreditBalance | None:
        """
        获取用户积分余额

        :param db: 数据库会话
        :param pk: 用户积分余额 ID
        :return:
        """
        return await self.select_model(db, pk)

    async def get_select(
        self,
        *,
        user_keyword: str | None = None,
        credit_type: str | None = None,
        expires_at: list[date] | None = None,
        granted_at: list[date] | None = None,
        source_type: str | None = None,
        source_reference_id: str | None = None,
    ) -> Select:
        """获取用户积分余额列表查询表达式，支持用户昵称/手机号搜索"""
        stmt = select(
            UserCreditBalance.id,
            UserCreditBalance.user_id,
            User.nickname.label('user_nickname'),
            User.phone.label('user_phone'),
            UserCreditBalance.credit_type,
            UserCreditBalance.original_amount,
            UserCreditBalance.used_amount,
            UserCreditBalance.remaining_amount,
            UserCreditBalance.expires_at,
            UserCreditBalance.granted_at,
            UserCreditBalance.source_type,
            UserCreditBalance.source_reference_id,
            UserCreditBalance.description,
            UserCreditBalance.created_time,
            UserCreditBalance.updated_time,
        ).outerjoin(User, UserCreditBalance.user_id == User.id)

        if user_keyword is not None:
            stmt = stmt.where(
                or_(
                    User.nickname.ilike(f'%{user_keyword}%'),
                    User.phone.ilike(f'%{user_keyword}%'),
                )
            )
        if credit_type is not None:
            stmt = stmt.where(UserCreditBalance.credit_type == credit_type)
        if expires_at is not None and len(expires_at) == 2:
            stmt = stmt.where(UserCreditBalance.expires_at >= datetime.combine(expires_at[0], datetime.min.time()))
            stmt = stmt.where(UserCreditBalance.expires_at <= datetime.combine(expires_at[1], datetime.max.time()))
        if granted_at is not None and len(granted_at) == 2:
            stmt = stmt.where(UserCreditBalance.granted_at >= datetime.combine(granted_at[0], datetime.min.time()))
            stmt = stmt.where(UserCreditBalance.granted_at <= datetime.combine(granted_at[1], datetime.max.time()))
        if source_type is not None:
            stmt = stmt.where(UserCreditBalance.source_type == source_type)
        if source_reference_id is not None:
            stmt = stmt.where(UserCreditBalance.source_reference_id.ilike(f'%{source_reference_id}%'))

        stmt = stmt.order_by(UserCreditBalance.id.desc())
        return stmt

    async def get_all(self, db: AsyncSession) -> Sequence[UserCreditBalance]:
        """
        获取所有用户积分余额

        :param db: 数据库会话
        :return:
        """
        return await self.select_models(db)

    async def create(self, db: AsyncSession, obj: CreateUserCreditBalanceParam) -> None:
        """
        创建用户积分余额

        :param db: 数据库会话
        :param obj: 创建用户积分余额参数
        :return:
        """
        await self.create_model(db, obj)

    async def update(self, db: AsyncSession, pk: int, obj: UpdateUserCreditBalanceParam) -> int:
        """
        更新用户积分余额

        :param db: 数据库会话
        :param pk: 用户积分余额 ID
        :param obj: 更新 用户积分余额参数
        :return:
        """
        return await self.update_model(db, pk, obj)

    async def delete(self, db: AsyncSession, pks: list[int]) -> int:
        """
        批量删除用户积分余额

        :param db: 数据库会话
        :param pks: 用户积分余额 ID 列表
        :return:
        """
        return await self.delete_model_by_column(db, allow_multiple=True, id__in=pks)


user_credit_balance_dao: CRUDUserCreditBalance = CRUDUserCreditBalance(UserCreditBalance)
