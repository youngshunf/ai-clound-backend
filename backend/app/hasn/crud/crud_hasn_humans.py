from collections.abc import Sequence

from sqlalchemy import Select, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy_crud_plus import CRUDPlus

from backend.app.hasn.model import HasnHumans
from backend.app.hasn.schema.hasn_humans import CreateHasnHumansParam, UpdateHasnHumansParam


class CRUDHasnHumans(CRUDPlus[HasnHumans]):
    async def get(self, db: AsyncSession, pk: int) -> HasnHumans | None:
        return await self.select_model(db, pk)

    async def get_select(self) -> Select:
        return await self.select_order('id', 'desc')

    async def get_all(self, db: AsyncSession) -> Sequence[HasnHumans]:
        return await self.select_models(db)

    async def create(self, db: AsyncSession, obj: CreateHasnHumansParam) -> None:
        await self.create_model(db, obj)

    async def update(self, db: AsyncSession, pk: int, obj: UpdateHasnHumansParam) -> int:
        return await self.update_model(db, pk, obj)

    async def delete(self, db: AsyncSession, pks: list[int]) -> int:
        return await self.delete_model_by_column(db, allow_multiple=True, id__in=pks)

    # ─── 业务查询方法 ───

    @staticmethod
    async def get_by_hasn_id(db: AsyncSession, hasn_id: str) -> HasnHumans | None:
        return (await db.execute(
            select(HasnHumans).where(HasnHumans.hasn_id == hasn_id)
        )).scalars().first()

    @staticmethod
    async def get_by_star_id(db: AsyncSession, star_id: str) -> HasnHumans | None:
        return (await db.execute(
            select(HasnHumans).where(HasnHumans.star_id == star_id)
        )).scalars().first()

    @staticmethod
    async def get_by_user_id(db: AsyncSession, user_id: int) -> HasnHumans | None:
        return (await db.execute(
            select(HasnHumans).where(HasnHumans.user_id == user_id)
        )).scalars().first()

    @staticmethod
    async def search_by_name(
        db: AsyncSession,
        prefix: str,
        limit: int = 20,
        exclude_hasn_id: str | None = None,
    ) -> Sequence[HasnHumans]:
        """按昵称前缀（case-insensitive）模糊匹配 active 用户。

        排除调用方自己；仅返回 status='active'，避免命中 suspended/deleted。
        排序按 nickname 字典序，limit 兜底防止超大返回。
        """
        from sqlalchemy import func

        stmt = (
            select(HasnHumans)
            .where(func.lower(HasnHumans.nickname).like(f"{prefix.lower()}%"))
            .where(HasnHumans.status == 'active')
        )
        if exclude_hasn_id:
            stmt = stmt.where(HasnHumans.hasn_id != exclude_hasn_id)
        stmt = stmt.order_by(HasnHumans.nickname).limit(limit)
        return (await db.execute(stmt)).scalars().all()

    @staticmethod
    async def search_by_phone(
        db: AsyncSession,
        phone: str,
        exclude_hasn_id: str | None = None,
    ) -> HasnHumans | None:
        """按手机号精确匹配 active human（隐私：精确等值，绝不前缀/模糊枚举）。

        手机号存于 admin users 表，HasnHumans.user_id → users.id；命中唯一 active human。
        """
        from backend.app.admin.model import User

        stmt = (
            select(HasnHumans)
            .join(User, HasnHumans.user_id == User.id)
            .where(User.phone == phone)
            .where(HasnHumans.status == 'active')
        )
        if exclude_hasn_id:
            stmt = stmt.where(HasnHumans.hasn_id != exclude_hasn_id)
        return (await db.execute(stmt)).scalars().first()


hasn_humans_dao: CRUDHasnHumans = CRUDHasnHumans(HasnHumans)
