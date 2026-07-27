"""工作流执行态上行同步服务（daemon → cloud，doc36 §6.3 · U5a · D11-A）。

## 这条通道补的是什么洞

云端 `workflow_run` / `workflow_node_run` 两表由 2026-07-14 P1 expand-only 迁移建好，DAO 写了
`list_by_run` / `get_by_run_node` / `latest_by_workflow_node` 三个读方法——**但全仓没有一个写者**：
无模型构造点、无 INSERT、同步管线不含它，model docstring 自称的「与借道 run 行并存双写」里那个
「双写」在云端代码里根本不存在。表是空壳，权威数据只在 daemon 本地 SQLite（调度在 daemon）。
doc36 §6.2 要的「按场景 run 聚合全部节点产物」因此查的是空表——这正是本切片的存在理由。

## 为什么用 Owner JWT，而不是像 `run_summary` 那样用 Agent JWT

doc36 §6.3 说「对齐 `run_summary` 上行范式」，指的是**幂等 UPSERT + 越权校验**这两点，凭据按
真实写者选：

- `run_summary` 的写者是**执行中的那个分身**（一次 run 一个 agent），Agent JWT 天然合身；
- 工作流节点执行态的写者是 **daemon 的调度器**，一次整图 fire 覆盖**多个不同分身**的节点。用
  Agent JWT 就得按 agent 拆批、并为可能已离线的分身取 token，而 `run_summary` 的越权校验恰恰
  写着「agent cannot report another agent task run」——同批多 agent 直接撞墙。

工作流实例本就是 owner 所有（`workflow_run.owner_id`），daemon 是该 owner 的设备，Owner JWT 才是
对的身份。owner 一律取 **JWT 权威身份**盖章，不信 payload（`owner_id` 入参只做一致性校验）。

## 端云状态域不一致（施工核实挖出的地雷）

本地 `workflow_node_runs` 的 CHECK 是**超集**——十态 ∪ 调度器过渡态 `success`/`error` ∪ 质量门
临时态 `pending_review`（V074 迁移注释自陈「放宽为超集，使各阶段不破坏既有调度器」）；云端
`chk_workflow_node_run_status` 只认**十态**。daemon 调度器至今仍在写 `success`/`error`
（`workflow_scheduler.rs` 的 `NODE_SUCCESS`/`NODE_ERROR`），原样上推必撞 CHECK → IntegrityError。
故在云端边界归一（`_NODE_STATUS_ALIAS`），映射沿用 P1 迁移回填自己的 CASE（success→done、
error→failed），两处保持同一口径。
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any

import sqlalchemy as sa

from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.exc import IntegrityError

from backend.app.hasn_task.model import HasnWorkflow, HasnWorkflowNodeRun, HasnWorkflowRun
from backend.app.hasn_task.schema.workflow_sync import WorkflowRunUpstream
from backend.utils.timezone import timezone

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession

    from backend.app.hasn_task.schema.workflow_sync import (
        WorkflowNodeRunUpstream,
        WorkflowNodeRunsSyncRequest,
        WorkflowNodeRunsSyncResponse,
    )

# 云端 chk_workflow_run_status 允许集
_RUN_STATUSES = frozenset({'running', 'completed', 'failed', 'blocked', 'cancelled'})
# 云端 chk_workflow_node_run_status 允许集（十态）
_NODE_STATUSES = frozenset({
    'pending',
    'ready',
    'running',
    'waiting',
    'needs_attention',
    'done',
    'failed',
    'skipped',
    'stale',
    'cancelled',
})
# daemon 调度器过渡态 → 云端十态（口径同 P1 迁移回填的 CASE，见模块 docstring）。
# pending_review = 结构产出闸已过、待质量门评审的本地临时态，仍在飞 → running。
_NODE_STATUS_ALIAS = {'success': 'done', 'error': 'failed', 'pending_review': 'running'}
# 云端 chk_workflow_run_advance_mode 允许集
_ADVANCE_MODES = frozenset({'manual', 'auto'})


def _as_datetime(value: datetime | float | str | None) -> datetime | None:
    """`int`(Unix 秒) / ISO 串 / `datetime` → aware datetime；认不出的当没给（`None`）。

    **别指望 Pydantic 替你转**：入参声明是 `datetime | int | float | str | None` 这样的联合类型，
    smart union 下 daemon 推来的 int 会匹配到 `int` 分支**原样留着**，根本不进 datetime 解析。
    （`TaskRunSummaryRequest` 同款声明、同样在 codec 里手转，是既有先例。）
    """
    if value is None or isinstance(value, datetime):
        return value
    if isinstance(value, int | float):
        # daemon SQLite 时间列一律 INTEGER Unix 秒（本地约定），按 UTC 还原
        return datetime.fromtimestamp(value, tz=UTC)
    if isinstance(value, str):
        if not value:
            return None
        try:
            return datetime.fromisoformat(value.replace('Z', '+00:00'))
        except ValueError:
            return None
    return None


def normalize_node_status(status: str) -> str | None:
    """本地节点态 → 云端十态；认不出返回 `None`（调用方拒收，不硬塞进 CHECK 里炸）。"""
    value = (status or '').strip()
    value = _NODE_STATUS_ALIAS.get(value, value)
    return value if value in _NODE_STATUSES else None


def build_workflow_run_upsert(*, run: WorkflowRunUpstream, owner_id: str, now: datetime) -> Any:
    """构造执行记录 UPSERT，并保护已持久化的历史快照不被旧协议清空。"""
    values = {
        'workflow_run_uuid': run.workflow_run_uuid,
        'workflow_uuid': run.workflow_uuid,
        'owner_id': owner_id,
        'workflow_name_snapshot': run.workflow_name_snapshot,
        'template_key_snapshot': run.template_key_snapshot,
        'project_id': run.project_id,
        'dedupe_key': run.dedupe_key or run.workflow_run_uuid,
        'status': (run.status or 'running').strip(),
        'advance_mode': (run.advance_mode or 'manual').strip(),
        'scheduled_fire_at': _as_datetime(run.scheduled_fire_at),
        'graph_snapshot': run.graph_snapshot if isinstance(run.graph_snapshot, dict) else {},
        'output_summary': run.output_summary,
        'started_at': _as_datetime(run.started_at),
        'finished_at': _as_datetime(run.finished_at),
        'created_time': now,
        'updated_time': now,
    }
    statement = pg_insert(HasnWorkflowRun).values(**values)
    update_values: dict[str, Any] = {
        key: value for key, value in values.items() if key not in ('workflow_run_uuid', 'created_time')
    }
    for field in ('workflow_name_snapshot', 'template_key_snapshot', 'project_id'):
        update_values[field] = sa.func.coalesce(getattr(HasnWorkflowRun, field), getattr(statement.excluded, field))

    return statement.on_conflict_do_update(
        index_elements=[HasnWorkflowRun.workflow_run_uuid],
        set_=update_values,
        where=HasnWorkflowRun.owner_id == owner_id,
    ).returning(HasnWorkflowRun.id)


class WorkflowSyncService:
    """daemon 上推的执行态落云端权威表（幂等 UPSERT + owner 越权校验）。"""

    async def sync_node_runs(
        self,
        db: AsyncSession,
        request: WorkflowNodeRunsSyncRequest,
        *,
        owner_id: str,
    ) -> WorkflowNodeRunsSyncResponse:
        """一次上行批：先落 run 再落 node_run（顺序要紧，见下）。

        `runs` 先于 `node_runs` 处理：同批推上来时节点行的父 run 必须先在，否则 owner 归属
        校验（读父 run 的 owner）拿不到行，只能放行——留下无主孤儿。先落 run 就没这问题。

        逐条成败独立（坏行进 `rejected`，好行照落）——理由见 `WorkflowNodeRunsSyncResponse`。
        """
        from backend.app.hasn_task.schema.workflow_sync import WorkflowNodeRunsSyncResponse

        rejected: list[dict[str, Any]] = []
        deferred: list[dict[str, Any]] = []

        accepted_runs = 0
        for run in request.runs:
            reason, is_deferred = await self._upsert_run(
                db,
                run,
                owner_id=owner_id,
                sync_protocol_version=request.sync_protocol_version,
            )
            if reason is None:
                accepted_runs += 1
            elif is_deferred:
                deferred.append({'uuid': run.workflow_run_uuid, 'reason': reason})
            else:
                rejected.append({'workflow_run_uuid': run.workflow_run_uuid, 'reason': reason})

        accepted_node_runs = 0
        for node_run in request.node_runs:
            reason, is_deferred = await self._upsert_node_run(
                db,
                node_run,
                owner_id=owner_id,
                sync_protocol_version=request.sync_protocol_version,
            )
            if reason is None:
                accepted_node_runs += 1
            elif is_deferred:
                deferred.append({'uuid': node_run.node_run_uuid, 'reason': reason})
            else:
                rejected.append({'node_run_uuid': node_run.node_run_uuid, 'reason': reason})

        return WorkflowNodeRunsSyncResponse(
            accepted_runs=accepted_runs,
            accepted_node_runs=accepted_node_runs,
            rejected=rejected,
            deferred=deferred,
        )

    async def _upsert_run(
        self,
        db: AsyncSession,
        run: WorkflowRunUpstream,
        *,
        owner_id: str,
        sync_protocol_version: int,
    ) -> tuple[str | None, bool]:
        """UPSERT 一条执行实例。返回 ``(原因, 是否 deferred)``。"""
        status = (run.status or 'running').strip()
        if status not in _RUN_STATUSES:
            return f'unknown run status: {status}', False
        advance_mode = (run.advance_mode or 'manual').strip()
        if advance_mode not in _ADVANCE_MODES:
            return f'unknown advance_mode: {advance_mode}', False

        parent_owner = (
            await db.execute(
                sa.select(HasnWorkflow.owner_id).where(HasnWorkflow.workflow_uuid == run.workflow_uuid)
            )
        ).scalar_one_or_none()
        if parent_owner is not None and parent_owner != owner_id:
            return 'workflow belongs to another owner', False
        if parent_owner is None and sync_protocol_version >= 2:
            # v2 要求先把旧定义导入云端，才允许同步执行态。暂缓而非拒收，daemon 可在导入成功后
            # 以同一稳定 UUID 重推；v1 保留早期孤儿历史回灌的兼容窗口。
            return 'PARENT_WORKFLOW_MISSING', True

        # 冲突键 workflow_run_uuid = 端云稳定同步主键（本地 workflow_runs.workflow_run_id 即此值）。
        # `where` 挂 owner 相等：别人的 run 撞进来时**不改行也不报错**，RETURNING 空 → 判定越权。
        # 比「先 SELECT 再判」少一次往返，且天然无 TOCTOU。
        stmt = build_workflow_run_upsert(run=run, owner_id=owner_id, now=timezone.now())
        return await self._execute_upsert(db, stmt, conflict_hint='workflow_run'), False

    async def _upsert_node_run(
        self,
        db: AsyncSession,
        node_run: WorkflowNodeRunUpstream,
        *,
        owner_id: str,
        sync_protocol_version: int,
    ) -> tuple[str | None, bool]:
        """UPSERT 一条节点执行态。返回 ``(原因, 是否 deferred)``。"""
        status = normalize_node_status(node_run.status)
        if status is None:
            return f'unknown node status: {node_run.status}', False

        # 父 run 若已在云端且属于别人 → 拒（防止把节点行挂到别人的 run 下）。
        # v1 父 run 不在时仍可放行，以兼容发布前的孤儿历史；v2 则暂缓，等待父定义和父 run
        # 先落云端，避免新数据再制造不可聚合的孤儿节点。
        parent_owner = (
            await db.execute(
                sa.select(HasnWorkflowRun.owner_id).where(
                    HasnWorkflowRun.workflow_run_uuid == node_run.workflow_run_uuid
                )
            )
        ).scalar_one_or_none()
        if parent_owner is not None and parent_owner != owner_id:
            return 'workflow run belongs to another owner', False
        if parent_owner is None and sync_protocol_version >= 2:
            # 新协议要求 daemon 先完成旧定义导入，再上行执行态。这里绝不能回 rejected：那会让
            # daemon 把可恢复历史永久停推；也不能像旧协议一样接受新孤儿。
            return 'PARENT_WORKFLOW_RUN_MISSING', True

        now = timezone.now()
        values = {
            'node_run_uuid': node_run.node_run_uuid,
            'workflow_run_uuid': node_run.workflow_run_uuid,
            'workflow_uuid': node_run.workflow_uuid,
            'owner_id': owner_id,
            'node_key': node_run.node_key,
            'status': status,
            'work_session_id': node_run.work_session_id,
            'artifacts': node_run.artifacts if isinstance(node_run.artifacts, list) else [],
            'output_summary': node_run.output_summary,
            'output_gate_retries': node_run.output_gate_retries or 0,
            'review_rejects': node_run.review_rejects or 0,
            'attention_reason': node_run.attention_reason,
            'started_time': _as_datetime(node_run.started_at),
            'completed_time': _as_datetime(node_run.completed_at),
            'created_time': now,
            'updated_time': now,
        }
        # 冲突键取**语义键** (workflow_run_uuid, node_key) 而非 node_run_uuid：P1 迁移回填时给存量
        # 行现生成过 `ndr_<random>` uuid（daemon 并不知道这些值），只按 node_run_uuid 冲突会走 INSERT
        # 分支、再撞 uq_workflow_node_run_key 炸掉。按语义键冲突则会把回填的占位 uuid 更新成 daemon
        # 的权威值，存量行自动收敛。两端都保证一个 (run, node) 只有一条执行行，故语义键是安全的。
        stmt = (
            pg_insert(HasnWorkflowNodeRun)
            .values(**values)
            .on_conflict_do_update(
                constraint='uq_workflow_node_run_key',
                set_={k: v for k, v in values.items() if k != 'created_time'},
                where=HasnWorkflowNodeRun.owner_id == owner_id,
            )
            .returning(HasnWorkflowNodeRun.id)
        )
        return await self._execute_upsert(db, stmt, conflict_hint='workflow_node_run'), False

    @staticmethod
    async def _execute_upsert(db: AsyncSession, stmt: Any, *, conflict_hint: str) -> str | None:
        """跑一条 UPSERT。RETURNING 空 = `where` 没过 = 越权；IntegrityError = 撞别的唯一键。

        每条包在 SAVEPOINT 里：一条坏行触发的 IntegrityError 会把整个事务打成 aborted，同批后面
        的好行连 SELECT 都跑不了——嵌套事务让坏行只回滚自己。
        """
        try:
            async with db.begin_nested():
                result = await db.execute(stmt)
                if result.scalar_one_or_none() is None:
                    return f'{conflict_hint} belongs to another owner'
        except IntegrityError as exc:
            return f'{conflict_hint} conflicts with an existing row: {exc.orig}'
        return None


workflow_sync_service: WorkflowSyncService = WorkflowSyncService()
