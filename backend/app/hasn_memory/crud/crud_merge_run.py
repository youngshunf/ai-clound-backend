"""合并轮次 DAO（hasn_memory.merge_run，doc19 §5.5 / §5.6）。

主键即 `run_id`（主脑铸造并随整轮结果一起提交），故「同一轮重复提交」在主键上即被识别。
"""

from collections.abc import Sequence
from datetime import datetime

import sqlalchemy as sa

from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy_crud_plus import CRUDPlus

from backend.app.hasn_memory.model import MergeRun
from backend.app.hasn_memory.schema.merge_run import CreateMergeRunParam


class CRUDMergeRun(CRUDPlus[MergeRun]):
    async def get(self, db: AsyncSession, run_id: str) -> MergeRun | None:
        """按轮次 ID 取合并轮次（不存在返回 None）。"""
        return await self.select_model(db, run_id)

    async def create(self, db: AsyncSession, obj: CreateMergeRunParam) -> MergeRun:
        """登记一轮合并（applied 或 rejected 都登记——§5.6 拒绝也必须留痕，不静默停摆）。"""
        run = MergeRun(
            run_id=obj.run_id,
            owner_id=obj.owner_id,
            submitted_node_id=obj.submitted_node_id,
            submitted_agent_id=obj.submitted_agent_id,
            base_owner_memory_version=obj.base_owner_memory_version,
            status=obj.status,
            reject_reason=obj.reject_reason,
            facts_judged=obj.facts_judged,
            facts_merged=obj.facts_merged,
            facts_disputed=obj.facts_disputed,
            summary=obj.summary,
        )
        db.add(run)
        await db.flush()
        return run

    async def mark_finished(self, db: AsyncSession, run_id: str, finished_time: datetime) -> bool:
        """落轮次结束时间（合并闸 apply / reject 完成后调用）；轮次不存在返回 False。"""
        run = await self.get(db, run_id)
        if run is None:
            return False
        run.finished_time = finished_time
        await db.flush()
        return True

    async def latest_by_owner(self, db: AsyncSession, owner_id: str, *, status: str | None = None) -> MergeRun | None:
        """取某主人最近一轮（可按 status 限定 applied）——§5.5「上次整理于 X」。"""
        stmt = sa.select(MergeRun).where(MergeRun.owner_id == owner_id)
        if status:
            stmt = stmt.where(MergeRun.status == status)
        stmt = stmt.order_by(MergeRun.started_time.desc()).limit(1)
        return (await db.execute(stmt)).scalars().first()

    async def list_by_owner(self, db: AsyncSession, owner_id: str, *, limit: int = 20) -> Sequence[MergeRun]:
        """列某主人的合并轮次（最近在前）——记忆页整理历史。"""
        stmt = (
            sa.select(MergeRun)
            .where(MergeRun.owner_id == owner_id)
            .order_by(MergeRun.started_time.desc())
            .limit(max(1, limit))
        )
        return (await db.execute(stmt)).scalars().all()


merge_run_dao: CRUDMergeRun = CRUDMergeRun(MergeRun)
