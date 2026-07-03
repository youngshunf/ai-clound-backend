from collections.abc import Sequence

from sqlalchemy import Select, or_, select, update
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy_crud_plus import CRUDPlus

from backend.app.marketplace.model.marketplace_sop import MarketplaceSop
from backend.app.marketplace.schema.marketplace_sop import CreateMarketplaceSopParam, UpdateMarketplaceSopParam


class CRUDMarketplaceSop(CRUDPlus[MarketplaceSop]):
    async def get(self, db: AsyncSession, pk: int) -> MarketplaceSop | None:
        return await self.select_model(db, pk)

    async def get_select(self) -> Select:
        return await self.select_order('id', 'desc')

    async def get_all(self, db: AsyncSession) -> Sequence[MarketplaceSop]:
        return await self.select_models(db)

    async def create(self, db: AsyncSession, obj: CreateMarketplaceSopParam) -> None:
        await self.create_model(db, obj)

    async def update(self, db: AsyncSession, pk: int, obj: UpdateMarketplaceSopParam) -> int:
        return await self.update_model(db, pk, obj)

    async def delete(self, db: AsyncSession, pks: list[int]) -> int:
        return await self.delete_model_by_column(db, allow_multiple=True, id__in=pks)

    async def get_by_id(self, db: AsyncSession, sop_id: str) -> MarketplaceSop | None:
        return await self.select_model_by_column(db, sop_id=sop_id)

    async def increment_download_count(self, db: AsyncSession, sop_id: str) -> None:
        stmt = (
            update(MarketplaceSop)
            .where(MarketplaceSop.sop_id == sop_id)
            .values(download_count=MarketplaceSop.download_count + 1)
        )
        await db.execute(stmt)

    async def get_select_public(
        self,
        category: str | None = None,
        tags: str | None = None,
        pricing_type: str | None = None,
        is_official: bool | None = None,
    ) -> Select:
        stmt = select(MarketplaceSop).where(not MarketplaceSop.is_private)

        if category:
            stmt = stmt.where(MarketplaceSop.category == category)
        if tags:
            stmt = stmt.where(MarketplaceSop.tags.contains(tags))
        if pricing_type:
            stmt = stmt.where(MarketplaceSop.pricing_type == pricing_type)
        if is_official is not None:
            stmt = stmt.where(MarketplaceSop.is_official == is_official)

        stmt = stmt.order_by(
            MarketplaceSop.is_official.desc(),
            MarketplaceSop.download_count.desc(),
            MarketplaceSop.id.desc(),
        )

        return stmt

    async def search(
        self,
        db: AsyncSession,
        keyword: str,
        category: str | None = None,
        limit: int = 20,
    ) -> list[MarketplaceSop]:
        stmt = select(MarketplaceSop).where(
            not MarketplaceSop.is_private,
            or_(
                MarketplaceSop.name.ilike(f'%{keyword}%'),
                MarketplaceSop.description.ilike(f'%{keyword}%'),
                MarketplaceSop.tags.ilike(f'%{keyword}%'),
                MarketplaceSop.category.ilike(f'%{keyword}%'),
            )
        )

        if category:
            stmt = stmt.where(MarketplaceSop.category == category)

        stmt = stmt.order_by(
            MarketplaceSop.is_official.desc(),
            MarketplaceSop.download_count.desc(),
        ).limit(limit)

        result = await db.execute(stmt)
        return list(result.scalars().all())


marketplace_sop_dao: CRUDMarketplaceSop = CRUDMarketplaceSop(MarketplaceSop)
