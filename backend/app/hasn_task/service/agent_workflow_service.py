"""工作流 Agent 能力面 service（云端补线，设计 07 §9）。

Agent JWT 通道的工作流读写（owner = agent.owner_hasn_id，跨户恒 NotFound）。
- create：节点缺省 agent = 编排发起分身（W2）；source/created_by_kind=agent；
  agent 建带定时的图 → pending_approval 业务态 + 提醒卡片（D4，非 ask_gate）。
- run/pause/cancel：云端权威状态信号（中心不 tick）——run 置 next_run_at=now 由 driver 节点
  本地 WorkflowScheduler fire；pause 置 status=paused；cancel 标最近未完 workflow_run=cancelled。
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

import sqlalchemy as sa

from backend.app.hasn_task.crud.crud_workflow import (
    hasn_workflow_dao,
    hasn_workflow_node_dao,
    hasn_workflow_node_run_dao,
    hasn_workflow_run_dao,
)
from backend.app.hasn_task.schema.workflow import CreateWorkflowParam, WorkflowEdgeSpec, WorkflowNodeSpec
from backend.app.hasn_task.service.task_service import calc_next_run_at
from backend.app.hasn_task.service.workflow_service import workflow_service
from backend.app.notification.service.notification_service import notification_service
from backend.common.exception import errors
from backend.utils.timezone import timezone

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession

    from backend.app.hasn_task.model import HasnWorkflow
    from backend.common.dataclasses import AgentTokenPayload

PERIODIC_TYPES = ('interval', 'cron')


def _iso(value: Any) -> str | None:
    return value.isoformat() if value is not None else None


def workflow_to_public(wf: HasnWorkflow) -> dict[str, Any]:
    """工作流行 → agent 可见投影（workflow_id = 端云稳定 workflow_uuid）。"""
    return {
        'workflow_id': wf.workflow_uuid,
        'name': wf.name,
        'goal': wf.goal,
        'schedule_type': wf.schedule_type,
        'schedule_config': wf.schedule_config,
        'timezone': wf.timezone,
        'status': wf.status,
        'enabled': wf.enabled,
        'source': wf.source,
        'created_by_kind': wf.created_by_kind,
        'continuation_enabled': wf.continuation_enabled,
        'next_run_at': _iso(wf.next_run_at),
        'last_run_at': _iso(wf.last_run_at),
        'created_at': _iso(wf.created_time),
        'updated_at': _iso(wf.updated_time),
    }


class AgentWorkflowService:
    """Agent JWT 通道的工作流读写。"""

    # ---------- 读 ----------

    @staticmethod
    async def list_agents(db: AsyncSession, *, owner_id: str) -> list[dict[str, Any]]:
        """发现 owner 可用分身（编排前映射节点→分身，对齐 kanban Step 0）。"""
        result = await db.execute(
            sa.text(
                'SELECT hasn_id, display_name, agent_name, description, profession '
                "FROM hasn_agents WHERE owner_id = :o AND status <> 'deleted' ORDER BY created_time DESC"
            ),
            {'o': owner_id},
        )
        return [
            {
                'agent_id': r['hasn_id'],
                'name': r['display_name'] or r['agent_name'],
                'description': r['description'],
                'profession': r['profession'],
            }
            for r in result.mappings().all()
        ]

    @classmethod
    async def get_workflow(cls, db: AsyncSession, *, owner_id: str, workflow_uuid: str) -> dict[str, Any]:
        """查图 + 节点 + 边 + 最近一次执行各节点状态。"""
        detail = await workflow_service.get_workflow(db, owner_id=owner_id, workflow_uuid=workflow_uuid)
        wf = detail['workflow']
        runs = await workflow_service.list_runs(db, owner_id=owner_id, workflow_uuid=workflow_uuid, limit=10)
        node_statuses = await cls._latest_node_statuses(db, workflow_uuid=workflow_uuid)
        nodes = [
            {
                'node_key': n['node_key'],
                'agent_id': n['agent_id'],
                'name': n['name'],
                'prompt': n['prompt'],
                'enable_subagents': n['enable_subagents'],
                'latest_status': node_statuses.get(n['node_key']),
            }
            for n in detail['nodes']
        ]
        return {'workflow': workflow_to_public(wf), 'nodes': nodes, 'edges': detail['edges'], 'runs': runs}

    @staticmethod
    async def _latest_node_statuses(db: AsyncSession, *, workflow_uuid: str) -> dict[str, str]:
        """最近一个 workflow_run 内各节点 run 状态（best-effort）。"""
        latest = await db.execute(
            sa.text(
                'SELECT workflow_run_uuid FROM hasn_task.workflow_run '
                'WHERE workflow_uuid = :wu ORDER BY created_time DESC LIMIT 1'
            ),
            {'wu': workflow_uuid},
        )
        wr = latest.scalar()
        if not wr:
            return {}
        rows = await db.execute(
            sa.text(
                'SELECT node_key, status FROM hasn_task.run '
                'WHERE workflow_run_uuid = :wr AND node_key IS NOT NULL'
            ),
            {'wr': wr},
        )
        return {r['node_key']: r['status'] for r in rows.mappings().all()}

    @classmethod
    async def list_workflows(cls, db: AsyncSession, *, owner_id: str) -> list[dict[str, Any]]:
        workflows = await workflow_service.list_workflows(db, owner_id=owner_id)
        return [workflow_to_public(wf) for wf in workflows]

    @staticmethod
    async def get_node_result(
        db: AsyncSession, *, owner_id: str, workflow_uuid: str, node_key: str
    ) -> dict[str, Any]:
        """取某节点最近一次执行的完整产出（§6 深查出口）。跨户 NotFound。"""
        wf = await hasn_workflow_dao.get_by_uuid(db, workflow_uuid)
        if wf is None or wf.owner_id != owner_id:
            raise errors.NotFoundError(msg='工作流不存在')

        # P1 expand-only：优先读节点执行专属表 workflow_node_run；旧数据未回填时回退 run_summary
        node_run = await hasn_workflow_node_run_dao.latest_by_workflow_node(db, workflow_uuid, node_key)
        if node_run is not None:
            node_def = await hasn_workflow_node_dao.get_by_key(db, workflow_uuid, node_key)
            return {
                'node_key': node_key,
                'name': (node_def.name if node_def is not None else None) or node_key,
                'run_id': node_run.node_run_uuid,
                'status': node_run.status,
                'output_summary': node_run.output_summary,
                'error': node_run.attention_reason,
                'finished_at': _iso(node_run.completed_time),
                'artifacts': node_run.artifacts if isinstance(node_run.artifacts, list) else [],
            }

        # 回退：借道的 task + run_summary（旧数据/尚无节点执行态）
        node = await db.execute(
            sa.text(
                'SELECT task_uuid, name FROM hasn_task.task '
                "WHERE workflow_uuid = :wu AND node_key = :nk AND state <> 'deleted'"
            ),
            {'wu': workflow_uuid, 'nk': node_key},
        )
        node_row = node.mappings().first()
        if node_row is None:
            raise errors.NotFoundError(msg='节点不存在')
        run = await db.execute(
            sa.text(
                'SELECT run_uuid, status, output_summary, error, finished_at, artifacts_json '
                'FROM hasn_task.run_summary WHERE owner_id = :o AND task_uuid = :tu '
                'ORDER BY coalesce(finished_at, started_at, scheduled_fire_at) DESC LIMIT 1'
            ),
            {'o': owner_id, 'tu': node_row['task_uuid']},
        )
        r = run.mappings().first()
        if r is None:
            return {'node_key': node_key, 'name': node_row['name'], 'status': None, 'output_summary': None}
        return {
            'node_key': node_key,
            'name': node_row['name'],
            'run_id': r['run_uuid'],
            'status': r['status'],
            'output_summary': r['output_summary'],
            'error': r['error'],
            'finished_at': _iso(r['finished_at']),
            'artifacts': r['artifacts_json'] if isinstance(r['artifacts_json'], list) else [],
        }

    # ---------- 写 ----------

    @classmethod
    async def create_workflow(
        cls, db: AsyncSession, *, agent: AgentTokenPayload, params: dict[str, Any]
    ) -> dict[str, Any]:
        """agent 建图：节点缺省 agent=发起分身；source/created_by_kind=agent；D4 定时图待审批。"""
        nodes = [
            WorkflowNodeSpec(**{**n, 'agent_id': n.get('agent_id') or agent.agent_hasn_id})
            for n in params.get('nodes') or []
        ]
        edges = [WorkflowEdgeSpec(**e) for e in params.get('edges') or []]
        obj = CreateWorkflowParam(
            name=str(params['name']),
            goal=params.get('goal'),
            schedule_type=str(params.get('schedule_type') or 'once'),
            schedule_config=dict(params.get('schedule_config') or {}),
            timezone=str(params.get('timezone') or 'Asia/Shanghai'),
            continuation_enabled=bool(params.get('continuation_enabled')),
            source='agent',
            created_by_kind='agent',
            nodes=nodes,
            edges=edges,
        )
        wf = await workflow_service.create_workflow(db, owner_id=agent.owner_hasn_id, obj=obj)
        if wf.status == 'pending_approval':
            await cls._notify_pending_approval(db, agent=agent, workflow_uuid=wf.workflow_uuid, name=wf.name)
        return workflow_to_public(wf)

    @classmethod
    async def _notify_pending_approval(
        cls, db: AsyncSession, *, agent: AgentTokenPayload, workflow_uuid: str, name: str
    ) -> None:
        await notification_service.emit(
            db,
            recipient_id=agent.owner_hasn_id,
            source={
                'kind': 'agent',
                'id': agent.agent_hasn_id,
                'display_name': agent.agent_name,
                'on_behalf_of': agent.owner_hasn_id,
            },
            category='reminder',
            type='workflow.pending_approval',
            title=f'分身 {agent.agent_name} 为你创建了定时工作流「{name}」，待你确认',
            payload={
                'target': {'kind': 'workflow', 'id': workflow_uuid},
                'deep_link': f'/tasks/workflows/{workflow_uuid}',
            },
            dedupe_key=f'workflow.pending_approval:{workflow_uuid}',
        )

    @staticmethod
    async def _load_owned(db: AsyncSession, *, owner_id: str, workflow_uuid: str) -> HasnWorkflow:
        wf = await hasn_workflow_dao.get_by_uuid(db, workflow_uuid)
        if wf is None or wf.owner_id != owner_id or wf.deleted_at is not None:
            raise errors.NotFoundError(msg='工作流不存在')
        return wf

    @classmethod
    async def run(cls, db: AsyncSession, *, owner_id: str, workflow_uuid: str) -> dict[str, Any]:
        """立即触发：置 next_run_at=now，由持有 driver 的本地节点 fire（中心不 tick）。"""
        wf = await cls._load_owned(db, owner_id=owner_id, workflow_uuid=workflow_uuid)
        if wf.status not in ('active', 'paused'):
            raise errors.RequestError(msg=f'当前状态 {wf.status} 不允许触发')
        wf.status = 'active'
        wf.enabled = True
        wf.next_run_at = timezone.now()
        wf.workflow_revision = (wf.workflow_revision or 0) + 1
        await db.flush()
        return workflow_to_public(wf)

    @classmethod
    async def pause(cls, db: AsyncSession, *, owner_id: str, workflow_uuid: str) -> dict[str, Any]:
        wf = await cls._load_owned(db, owner_id=owner_id, workflow_uuid=workflow_uuid)
        wf.status = 'paused'
        wf.next_run_at = None
        wf.workflow_revision = (wf.workflow_revision or 0) + 1
        await db.flush()
        return workflow_to_public(wf)

    # ---------- 主人审批（D4 业务态，owner JWT 经 app 面调用） ----------

    @classmethod
    async def approve(cls, db: AsyncSession, *, owner_id: str, workflow_uuid: str) -> dict[str, Any]:
        """主人批准 agent 建的定时工作流（pending_approval → active）。"""
        wf = await cls._load_owned(db, owner_id=owner_id, workflow_uuid=workflow_uuid)
        if wf.status != 'pending_approval':
            raise errors.RequestError(msg=f'当前状态 {wf.status} 无待审批事项')
        wf.status = 'active'
        wf.enabled = True
        wf.next_run_at = calc_next_run_at(wf.schedule_type, wf.schedule_config or {})
        wf.workflow_revision = (wf.workflow_revision or 0) + 1
        await db.flush()
        return workflow_to_public(wf)

    @classmethod
    async def reject(cls, db: AsyncSession, *, owner_id: str, workflow_uuid: str) -> dict[str, Any]:
        """主人拒绝 agent 建的定时工作流（pending_approval → rejected）。"""
        wf = await cls._load_owned(db, owner_id=owner_id, workflow_uuid=workflow_uuid)
        if wf.status != 'pending_approval':
            raise errors.RequestError(msg=f'当前状态 {wf.status} 无待审批事项')
        wf.status = 'rejected'
        wf.enabled = False
        wf.next_run_at = None
        wf.workflow_revision = (wf.workflow_revision or 0) + 1
        await db.flush()
        return workflow_to_public(wf)

    @classmethod
    async def cancel(cls, db: AsyncSession, *, owner_id: str, workflow_uuid: str) -> dict[str, Any]:
        """取消最近一个未完执行实例（标 cancelled，信号由 driver 节点本地停派）。"""
        wf = await cls._load_owned(db, owner_id=owner_id, workflow_uuid=workflow_uuid)
        runs = await hasn_workflow_run_dao.list_by_workflow(db, workflow_uuid, limit=5)
        cancelled = 0
        for wr in runs:
            if wr.status in ('running', 'blocked'):
                wr.status = 'cancelled'
                wr.finished_at = timezone.now()
                cancelled += 1
        await db.flush()
        return {'workflow': workflow_to_public(wf), 'cancelled_runs': cancelled}


agent_workflow_service = AgentWorkflowService()
