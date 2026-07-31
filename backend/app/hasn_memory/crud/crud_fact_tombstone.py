"""事实删除凭证 DAO（hasn_memory.fact_tombstone，doc19 §4.5）。

主键即 `fact_id`：登记天然幂等（purge 广播可能重复到达），墓碑判定是一次主键查。
本 DAO **不提供删除**——墓碑一旦落下就是永久的，删掉它等于让离线节点把已删内容复活。
"""

from collections.abc import Sequence

import sqlalchemy as sa

from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy_crud_plus import CRUDPlus

from backend.app.hasn_memory.model import FactTombstone
from backend.app.hasn_memory.schema.fact_tombstone import CreateFactTombstoneParam


class CRUDFactTombstone(CRUDPlus[FactTombstone]):
    async def get(self, db: AsyncSession, fact_id: str) -> FactTombstone | None:
        """按被删事实 ID 取墓碑（不存在返回 None）。"""
        return await self.select_model(db, fact_id)

    async def is_purged(self, db: AsyncSession, fact_id: str) -> bool:
        """该 fact_id 是否已被永久删除——晚到上行事件的拒绝判据（§4.5 墓碑防复活）。

        方法名不叫 `exists`：`CRUDPlus` 已有同名通用签名，重名会遮蔽父类语义。
        """
        stmt = sa.select(sa.literal(1)).where(FactTombstone.fact_id == fact_id).limit(1)
        return (await db.execute(stmt)).scalar_one_or_none() is not None

    async def filter_purged(self, db: AsyncSession, fact_ids: Sequence[str]) -> set[str]:
        """批量筛出已被永久删除的 fact_id（同步 apply 时一次过滤整批上行事件）。"""
        if not fact_ids:
            return set()
        stmt = sa.select(FactTombstone.fact_id).where(FactTombstone.fact_id.in_(list(fact_ids)))
        return set((await db.execute(stmt)).scalars().all())

    async def record(self, db: AsyncSession, obj: CreateFactTombstoneParam) -> FactTombstone:
        """登记删除凭证；已存在则原样返回（purge 广播重复到达时幂等，不改写首次删除事实）。"""
        existing = await self.get(db, obj.fact_id)
        if existing is not None:
            return existing
        tombstone = FactTombstone(
            fact_id=obj.fact_id,
            owner_id=obj.owner_id,
            purged_by=obj.purged_by,
            cascade_from=obj.cascade_from,
            reason=obj.reason,
        )
        db.add(tombstone)
        await db.flush()
        return tombstone

    async def list_by_owner(self, db: AsyncSession, owner_id: str, *, limit: int = 100) -> Sequence[FactTombstone]:
        """列某主人的删除凭证（最近删除在前）——记忆页「已彻底删除」区。"""
        stmt = (
            sa.select(FactTombstone)
            .where(FactTombstone.owner_id == owner_id)
            .order_by(FactTombstone.purged_time.desc())
            .limit(max(1, limit))
        )
        return (await db.execute(stmt)).scalars().all()


fact_tombstone_dao: CRUDFactTombstone = CRUDFactTombstone(FactTombstone)
