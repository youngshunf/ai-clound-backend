"""合并轮次服务（doc19 §5.5 主脑单点可见 / §5.6 云端合并闸）。

S3 只落存储：登记一轮合并的提交者、基线版本、裁决计数与结果摘要，并给主人侧提供
「上次整理于 X、主脑在哪台设备」的读取口。**advisory lock + `owner_memory.version` CAS +
主脑校验属 S6 的合并闸**，本服务不假装已经做了裁定——`record_applied` / `record_rejected`
都要求调用方把已发生的结论如实传进来。
"""

from collections.abc import Sequence

from sqlalchemy.ext.asyncio import AsyncSession

from backend.app.hasn_memory.crud.crud_merge_run import merge_run_dao
from backend.app.hasn_memory.model import MergeRun
from backend.app.hasn_memory.schema.merge_run import CreateMergeRunParam
from backend.utils.timezone import timezone

_VALID_STATUS = frozenset({'applied', 'rejected'})


class MergeRunService:
    """合并轮次云端权威读写。"""

    @staticmethod
    async def record_run(
        db: AsyncSession,
        *,
        run_id: str,
        owner_id: str,
        submitted_node_id: str,
        submitted_agent_id: str,
        base_owner_memory_version: int,
        status: str,
        reject_reason: str | None = None,
        facts_judged: int = 0,
        facts_merged: int = 0,
        facts_disputed: int = 0,
        summary: str | None = None,
        finished: bool = True,
    ) -> MergeRun:
        """登记一轮合并结果（applied 与 rejected 都登记——拒绝也必须留痕，§5.6）。"""
        if status not in _VALID_STATUS:
            raise ValueError(f'非法轮次结果: {status}（只允许 applied / rejected）')
        if status == 'rejected' and not reject_reason:
            raise ValueError('rejected 必须给出 reject_reason（主脑下轮重跑要知道为什么被拒）')
        run = await merge_run_dao.create(
            db,
            CreateMergeRunParam(
                run_id=run_id,
                owner_id=owner_id,
                submitted_node_id=submitted_node_id,
                submitted_agent_id=submitted_agent_id,
                base_owner_memory_version=base_owner_memory_version,
                status=status,
                reject_reason=reject_reason,
                facts_judged=facts_judged,
                facts_merged=facts_merged,
                facts_disputed=facts_disputed,
                summary=summary,
            ),
        )
        if finished:
            run.finished_time = timezone.now()
            await db.flush()
        return run

    @staticmethod
    async def get(db: AsyncSession, run_id: str) -> MergeRun | None:
        """按轮次 ID 取（不存在返回 None——调用方按业务决定是 404 还是新建）。"""
        return await merge_run_dao.get(db, run_id)

    @staticmethod
    async def latest_applied(db: AsyncSession, owner_id: str) -> MergeRun | None:
        """取某主人最近一轮**成功应用**的合并——§5.5「上次整理于 X，主脑在 <设备> 上」。"""
        return await merge_run_dao.latest_by_owner(db, owner_id, status='applied')

    @staticmethod
    async def list_by_owner(db: AsyncSession, owner_id: str, *, limit: int = 20) -> Sequence[MergeRun]:
        """列某主人的合并轮次（最近在前，含被拒轮次）。"""
        return await merge_run_dao.list_by_owner(db, owner_id, limit=limit)


merge_run_service: MergeRunService = MergeRunService()
