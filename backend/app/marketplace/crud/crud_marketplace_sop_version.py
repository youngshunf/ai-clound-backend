from collections.abc import Sequence
from typing import cast

from sqlalchemy import Select, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy_crud_plus import CRUDPlus

from backend.app.marketplace.model.marketplace_sop_version import MarketplaceSopVersion
from backend.app.marketplace.schema.marketplace_sop_version import (
    CreateMarketplaceSopVersionParam,
    UpdateMarketplaceSopVersionParam,
)


class CRUDMarketplaceSopVersion(CRUDPlus[MarketplaceSopVersion]):
    @staticmethod
    def _single_sop_version(result: object) -> MarketplaceSopVersion | None:
        """将不带关联加载的查询结果收紧为单个 SOP 版本。"""
        if result is not None and not isinstance(result, MarketplaceSopVersion):
            raise TypeError('SOP 版本单模型查询返回了关联结果')
        return cast(MarketplaceSopVersion | None, result)

    @staticmethod
    def _sop_version_sequence(result: object) -> Sequence[MarketplaceSopVersion]:
        """将不带关联加载的查询结果收紧为 SOP 版本序列。"""
        if not isinstance(result, Sequence) or not all(isinstance(item, MarketplaceSopVersion) for item in result):
            raise TypeError('SOP 版本列表查询返回了关联结果')
        return cast(Sequence[MarketplaceSopVersion], result)

    async def get(self, db: AsyncSession, pk: int) -> MarketplaceSopVersion | None:
        return self._single_sop_version(await self.select_model(db, pk))

    async def get_select(self) -> Select:
        return await self.select_order('id', 'desc')

    async def get_all(self, db: AsyncSession) -> Sequence[MarketplaceSopVersion]:
        return self._sop_version_sequence(await self.select_models(db))

    async def create(self, db: AsyncSession, obj: CreateMarketplaceSopVersionParam) -> None:
        await self.create_model(db, obj)

    async def update(self, db: AsyncSession, pk: int, obj: UpdateMarketplaceSopVersionParam) -> int:
        return await self.update_model(db, pk, obj)

    async def delete(self, db: AsyncSession, pks: list[int]) -> int:
        return await self.delete_model_by_column(db, allow_multiple=True, id__in=pks)

    async def get_by_sop_and_version(
        self, db: AsyncSession, sop_id: str, version: str
    ) -> MarketplaceSopVersion | None:
        return self._single_sop_version(await self.select_model_by_column(db, sop_id=sop_id, version=version))

    async def get_latest_by_sop(
        self, db: AsyncSession, sop_id: str
    ) -> MarketplaceSopVersion | None:
        return self._single_sop_version(await self.select_model_by_column(db, sop_id=sop_id, is_latest=True))

    async def get_versions_by_sop(
        self, db: AsyncSession, sop_id: str
    ) -> Sequence[MarketplaceSopVersion]:
        stmt = select(MarketplaceSopVersion).where(
            MarketplaceSopVersion.sop_id == sop_id
        ).order_by(MarketplaceSopVersion.id.desc())
        result = await db.execute(stmt)
        return list(result.scalars().all())

    get_by_sop = get_versions_by_sop


marketplace_sop_version_dao: CRUDMarketplaceSopVersion = CRUDMarketplaceSopVersion(MarketplaceSopVersion)
