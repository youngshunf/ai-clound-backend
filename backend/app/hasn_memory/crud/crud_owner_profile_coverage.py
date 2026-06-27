"""主人画像完整度 DAO（hasn_memory.owner_profile_coverage）。

owner × dimension 唯一，判定按 (owner_id, dimension) upsert。
"""

from datetime import datetime
from decimal import Decimal
from typing import Sequence

import sqlalchemy as sa

from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy_crud_plus import CRUDPlus

from backend.app.hasn_memory.model import OwnerProfileCoverage


class CRUDOwnerProfileCoverage(CRUDPlus[OwnerProfileCoverage]):
    async def get_by_owner(self, db: AsyncSession, owner_id: str) -> Sequence[OwnerProfileCoverage]:
        """取某主人的全部维度覆盖行（缺的维度由 service 补默认 missing）。"""
        return await self.select_models(db, owner_id=owner_id)

    async def get_one(
        self, db: AsyncSession, owner_id: str, dimension: str
    ) -> OwnerProfileCoverage | None:
        """取某主人某维度行。"""
        return await self.select_model_by_column(db, owner_id=owner_id, dimension=dimension)

    async def upsert(
        self,
        db: AsyncSession,
        *,
        owner_id: str,
        dimension: str,
        status: str,
        confidence: Decimal,
        summary: str | None,
        missing_hint: str | None,
        evidence_version: int,
        assessed_time: datetime,
    ) -> None:
        """按 (owner_id, dimension) upsert：存在则更新，不存在则插入（再判=更新同一行）。"""
        existing = await self.get_one(db, owner_id, dimension)
        if existing is not None:
            existing.status = status
            existing.confidence = confidence
            existing.summary = summary
            existing.missing_hint = missing_hint
            existing.evidence_version = evidence_version
            existing.assessed_time = assessed_time
        else:
            db.add(
                OwnerProfileCoverage(
                    owner_id=owner_id,
                    dimension=dimension,
                    status=status,
                    confidence=confidence,
                    summary=summary,
                    missing_hint=missing_hint,
                    evidence_version=evidence_version,
                    assessed_time=assessed_time,
                )
            )
        await db.flush()

    async def min_evidence_version(self, db: AsyncSession, owner_id: str) -> int | None:
        """取该主人各维度判定依据版本的最小值（None=尚无任何判定行）。"""
        result = await db.execute(
            sa.select(sa.func.min(OwnerProfileCoverage.evidence_version)).where(
                OwnerProfileCoverage.owner_id == owner_id
            )
        )
        return result.scalar_one_or_none()


owner_profile_coverage_dao: CRUDOwnerProfileCoverage = CRUDOwnerProfileCoverage(OwnerProfileCoverage)
