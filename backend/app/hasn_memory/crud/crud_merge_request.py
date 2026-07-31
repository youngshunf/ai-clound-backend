"""合并待办 DAO（hasn_memory.merge_request，doc19 §5.5）。

`owner_id` 即主键——「去重只留最新一条，不堆积」由主键保证：重复请求走 upsert 覆盖，
永远不会排出一条队列。消化后写 `consumed_time`，行保留作为「上次请求发生过」的证据。
"""

import sqlalchemy as sa

from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy_crud_plus import CRUDPlus

from backend.app.hasn_memory.model import MergeRequest
from backend.app.hasn_memory.schema.merge_request import CreateMergeRequestParam
from backend.utils.timezone import timezone


class CRUDMergeRequest(CRUDPlus[MergeRequest]):
    async def get(self, db: AsyncSession, owner_id: str) -> MergeRequest | None:
        """按主人取待办行（含已消化的历史行；不存在返回 None）。"""
        return await self.select_model(db, owner_id)

    async def upsert(self, db: AsyncSession, obj: CreateMergeRequestParam) -> MergeRequest:
        """登记合并待办：同主人已有行则整体覆盖为最新一次请求并重新置为未消化。"""
        now = timezone.now()
        existing = await self.get(db, obj.owner_id)
        if existing is not None:
            existing.requested_time = now
            existing.requested_by_agent = obj.requested_by_agent
            existing.requested_by_node = obj.requested_by_node
            existing.reason = obj.reason
            existing.consumed_time = None
            await db.flush()
            return existing
        request = MergeRequest(
            owner_id=obj.owner_id,
            requested_time=now,
            requested_by_agent=obj.requested_by_agent,
            requested_by_node=obj.requested_by_node,
            reason=obj.reason,
        )
        db.add(request)
        await db.flush()
        return request

    async def get_pending(self, db: AsyncSession, owner_id: str) -> MergeRequest | None:
        """取尚未被主脑消化的待办（consumed_time IS NULL）。"""
        stmt = sa.select(MergeRequest).where(
            MergeRequest.owner_id == owner_id,
            MergeRequest.consumed_time.is_(None),
        )
        return (await db.execute(stmt)).scalars().first()

    async def mark_consumed(self, db: AsyncSession, owner_id: str) -> bool:
        """标记待办已被主脑消化；返回 True 表示确有一条未消化待办被本次吃掉（重复消化返回 False）。"""
        pending = await self.get_pending(db, owner_id)
        if pending is None:
            return False
        pending.consumed_time = timezone.now()
        await db.flush()
        return True


merge_request_dao: CRUDMergeRequest = CRUDMergeRequest(MergeRequest)
