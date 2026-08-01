"""合并轮次服务（doc19 §5.5 主脑单点可见 / §5.6 云端合并闸）。

本服务只落存储：登记一轮合并的提交者、基线版本、裁决计数与结果摘要，并给主人侧提供
「上次整理于 X、主脑在哪台设备」的读取口。**advisory lock + `owner_memory.version` CAS +
主脑校验在 S6 的合并闸** `merge_gate_service`，本服务不做任何裁定——`record_run` /
`upsert_run` 都要求调用方把已发生的结论如实传进来。
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
    async def upsert_run(
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
        owner_guard: str | None = None,
    ) -> MergeRun:
        """登记或覆盖一轮合并结果（S6 合并闸唯一写点）。

        为什么要覆盖而不是只 INSERT：同一 ``run_id`` 可能先被拒（version_conflict）再由主脑
        修正基线后重跑成功，也可能连续两次被拒。主键是 ``run_id``，纯 INSERT 会撞 PK 让「拒绝
        留痕」自己变成一次 5xx——留痕的意义正是不让失败静默，不能反过来制造新的失败。

        ``owner_guard`` 非空时校验既有行归属：run_id 撞到别人的轮次一律拒绝改写（身份错乱，
        不是并发）。
        """
        if status not in _VALID_STATUS:
            raise ValueError(f'非法轮次结果: {status}（只允许 applied / rejected）')
        if status == 'rejected' and not reject_reason:
            raise ValueError('rejected 必须给出 reject_reason（主脑下轮重跑要知道为什么被拒）')
        existing = await merge_run_dao.get(db, run_id)
        if existing is None:
            run = await MergeRunService.record_run(
                db,
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
            )
            return run
        if owner_guard is not None and existing.owner_id != owner_guard:
            raise ValueError(f'run_id {run_id} 已属于其他主人的合并轮次')
        existing.owner_id = owner_id
        existing.submitted_node_id = submitted_node_id
        existing.submitted_agent_id = submitted_agent_id
        existing.base_owner_memory_version = base_owner_memory_version
        existing.status = status
        existing.reject_reason = reject_reason
        existing.facts_judged = facts_judged
        existing.facts_merged = facts_merged
        existing.facts_disputed = facts_disputed
        existing.summary = summary
        existing.started_time = timezone.now()
        existing.finished_time = timezone.now()
        await db.flush()
        return existing

    @staticmethod
    async def get(db: AsyncSession, run_id: str) -> MergeRun | None:
        """按轮次 ID 取（不存在返回 None——调用方按业务决定是 404 还是新建）。"""
        return await merge_run_dao.get(db, run_id)

    @staticmethod
    async def latest_applied(db: AsyncSession, owner_id: str) -> MergeRun | None:
        """取某主人最近一轮**成功应用**的合并——§5.5「上次整理于 X，主脑在 <设备> 上」。"""
        return await merge_run_dao.latest_by_owner(db, owner_id, status='applied')

    @staticmethod
    async def latest_rejected(db: AsyncSession, owner_id: str) -> MergeRun | None:
        """取某主人最近一轮**被拒**的合并——§5.6 拒绝可解释，主人能看到「为什么没整理成」。"""
        return await merge_run_dao.latest_by_owner(db, owner_id, status='rejected')

    @staticmethod
    async def list_by_owner(db: AsyncSession, owner_id: str, *, limit: int = 20) -> Sequence[MergeRun]:
        """列某主人的合并轮次（最近在前，含被拒轮次）。"""
        return await merge_run_dao.list_by_owner(db, owner_id, limit=limit)


merge_run_service: MergeRunService = MergeRunService()
