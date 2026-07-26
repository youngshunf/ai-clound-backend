"""工作流（任务图）service（云端权威，设计 07 §5/§9）。

建图校验（07 §9.3）：node_key 唯一 + 边引用 node_key 存在 + 无环（DFS）+ agent 属本 owner
（跨户 NotFound）+ 资源护栏（max_nodes/max_edges/深度）。云端 push 落库前复验无环（07 §5.3）。

节点复用 v3.0 的 hasn_task.task（加 workflow_uuid + node_key，W3）；本 service 做云端权威写入，
节点向各设备的同步沿用 v3.0 task 同步接缝（task.created 事件，由 Agent API 层 N2 触发）。
本层 create_workflow 默认走直接权威插入（owner 路径），返回完整图。

资源护栏常量（07 §9.3）：max_nodes 50 / max_edges 200 / 依赖链深度 ≤10。
"""

from __future__ import annotations

import json
import uuid

from collections import defaultdict, deque
from typing import TYPE_CHECKING, Any

import sqlalchemy as sa

from backend.app.hasn_task.crud.crud_workflow import (
    hasn_workflow_dao,
    hasn_workflow_edge_dao,
    hasn_workflow_node_dao,
    hasn_workflow_run_dao,
)
from backend.app.hasn_task.model import HasnWorkflow, HasnWorkflowEdge, HasnWorkflowNode
from backend.app.hasn_task.service.task_service import calc_next_run_at
from backend.common.exception import errors

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession

    from backend.app.hasn_task.schema.workflow import CreateWorkflowParam, WorkflowEdgeSpec, WorkflowNodeSpec

MAX_NODES = 50
MAX_EDGES = 200
MAX_DEPTH = 10
PERIODIC_TYPES = ('interval', 'cron')


# ============================ 纯图校验（可单测，与 daemon Rust 版语义一致） ============================


def detect_cycle(node_keys: list[str], edges: list[tuple[str, str]]) -> list[str] | None:
    """Kahn 拓扑排序检测环；有环返回参与环的剩余节点（非空），无环返回 None。"""
    adj: dict[str, list[str]] = defaultdict(list)
    indeg: dict[str, int] = dict.fromkeys(node_keys, 0)
    for parent, child in edges:
        adj[parent].append(child)
        indeg[child] = indeg.get(child, 0) + 1
    queue = deque([n for n in node_keys if indeg.get(n, 0) == 0])
    visited = 0
    while queue:
        node = queue.popleft()
        visited += 1
        for nxt in adj[node]:
            indeg[nxt] -= 1
            if indeg[nxt] == 0:
                queue.append(nxt)
    if visited == len(node_keys):
        return None
    return [n for n in node_keys if indeg.get(n, 0) > 0]


def longest_chain_depth(node_keys: list[str], edges: list[tuple[str, str]]) -> int:
    """DAG 最长依赖链深度（节点数）。仅在无环时调用。"""
    adj: dict[str, list[str]] = defaultdict(list)
    indeg: dict[str, int] = dict.fromkeys(node_keys, 0)
    for parent, child in edges:
        adj[parent].append(child)
        indeg[child] = indeg.get(child, 0) + 1
    depth: dict[str, int] = dict.fromkeys(node_keys, 1)
    queue = deque([n for n in node_keys if indeg.get(n, 0) == 0])
    best = 1 if node_keys else 0
    while queue:
        node = queue.popleft()
        for nxt in adj[node]:
            if depth[node] + 1 > depth[nxt]:
                depth[nxt] = depth[node] + 1
                best = max(best, depth[nxt])
            indeg[nxt] -= 1
            if indeg[nxt] == 0:
                queue.append(nxt)
    return best


def _unique_node_keys(nodes: list[WorkflowNodeSpec]) -> set[str]:
    """提取并校验 node_key 唯一（空/重复即拒绝）。"""
    seen: set[str] = set()
    for node in nodes:
        if not node.node_key:
            raise errors.RequestError(msg='node_key 不能为空')
        if node.node_key in seen:
            raise errors.RequestError(msg=f'node_key 重复: {node.node_key}')
        seen.add(node.node_key)
    return seen


def _edge_tuples(edges: list[WorkflowEdgeSpec], node_set: set[str]) -> list[tuple[str, str]]:
    """校验边引用的节点存在、非自环，返回 (parent, child) 元组列表。"""
    tuples: list[tuple[str, str]] = []
    for edge in edges:
        if edge.parent not in node_set:
            raise errors.RequestError(msg=f'边引用的父节点不存在: {edge.parent}')
        if edge.child not in node_set:
            raise errors.RequestError(msg=f'边引用的子节点不存在: {edge.child}')
        if edge.parent == edge.child:
            raise errors.RequestError(msg=f'节点不能依赖自己: {edge.parent}')
        tuples.append((edge.parent, edge.child))
    return tuples


def validate_graph(nodes: list[WorkflowNodeSpec], edges: list[WorkflowEdgeSpec]) -> None:
    """建图静态校验（不触库）：节点非空 + node_key 唯一 + 边引用存在 + 无环 + 资源护栏。"""
    if not nodes:
        raise errors.RequestError(msg='工作流至少需要一个节点')
    if len(nodes) > MAX_NODES:
        raise errors.RequestError(msg=f'节点数超限（max {MAX_NODES}）')
    if len(edges) > MAX_EDGES:
        raise errors.RequestError(msg=f'依赖边数超限（max {MAX_EDGES}）')

    node_set = _unique_node_keys(nodes)
    node_keys = [n.node_key for n in nodes]
    edge_tuples = _edge_tuples(edges, node_set)

    cycle = detect_cycle(node_keys, edge_tuples)
    if cycle is not None:
        raise errors.RequestError(msg=f'依赖图存在环，涉及节点: {", ".join(cycle)}')

    depth = longest_chain_depth(node_keys, edge_tuples)
    if depth > MAX_DEPTH:
        raise errors.RequestError(msg=f'依赖链深度超限（{depth} > {MAX_DEPTH}）')


# ============================ Service ============================


class WorkflowService:
    """工作流云端权威读写（owner = 调用身份；跨户恒 NotFound）。"""

    @staticmethod
    async def _assert_agents_owned(db: AsyncSession, *, owner_id: str, agent_ids: set[str]) -> None:
        """校验一批 agent_id 全部属于 owner（跨户 → NotFound，不泄露）。"""
        real = {a for a in agent_ids if a}
        if not real:
            return
        result = await db.execute(
            sa.text('SELECT hasn_id FROM hasn_agents WHERE owner_id = :o AND hasn_id = ANY(:ids)'),
            {'o': owner_id, 'ids': list(real)},
        )
        owned = {r[0] for r in result}
        missing = real - owned
        if missing:
            raise errors.NotFoundError(msg=f'分身不存在或不属于你: {", ".join(sorted(missing))}')

    @classmethod
    async def create_workflow(
        cls,
        db: AsyncSession,
        *,
        owner_id: str,
        obj: CreateWorkflowParam,
    ) -> HasnWorkflow:
        """建图：校验 → 插入 workflow + 节点 task + 边（一个事务，云端权威）。"""
        validate_graph(obj.nodes, obj.edges)
        if obj.schedule_type not in ('once', *PERIODIC_TYPES):
            raise errors.RequestError(msg=f'未知调度类型: {obj.schedule_type}')

        # 节点 agent 默认 = 编排发起分身（仅 source=agent 才有意义；owner 路径必须显式指定）
        agent_ids = {n.agent_id for n in obj.nodes if n.agent_id}
        await cls._assert_agents_owned(db, owner_id=owner_id, agent_ids=agent_ids)
        for node in obj.nodes:
            if not node.agent_id:
                raise errors.RequestError(msg=f'节点 {node.node_key} 缺少目标分身 agent_id')

        # Owner 场景实例化是唯一要求稳定重放的建图路径。事务级 advisory lock 把「先查再建」
        # 收敛为同一 owner+key 的串行临界区，避免并发重试穿透 partial unique index 后才报 500。
        idempotency_key = obj.instantiation_idempotency_key
        if idempotency_key:
            lock_key = f'{owner_id}:{idempotency_key}'
            await db.execute(
                sa.text('SELECT pg_advisory_xact_lock(hashtextextended(:lock_key, 0))'), {'lock_key': lock_key}
            )
            existing = (
                await db.execute(
                    sa.select(HasnWorkflow).where(
                        HasnWorkflow.owner_id == owner_id,
                        HasnWorkflow.instantiation_idempotency_key == idempotency_key,
                    )
                )
            ).scalar_one_or_none()
            if existing is not None:
                return existing

        workflow_uuid = obj.workflow_uuid or f'wf_{uuid.uuid4().hex}'
        # 唯一性：同 uuid 不重复建
        if await hasn_workflow_dao.get_by_uuid(db, workflow_uuid):
            raise errors.RequestError(msg='工作流已存在（workflow_uuid 冲突）')

        is_periodic = obj.schedule_type in PERIODIC_TYPES
        status = obj.status or ('pending_approval' if (obj.created_by_kind == 'agent' and is_periodic) else 'active')
        next_run_at = None if status == 'pending_approval' else calc_next_run_at(obj.schedule_type, obj.schedule_config)

        workflow = HasnWorkflow(
            workflow_uuid=workflow_uuid,
            owner_id=owner_id,
            name=obj.name,
            template_key=obj.template_key,
            instantiation_idempotency_key=idempotency_key,
            goal=obj.goal,
            schedule_type=obj.schedule_type,
            schedule_config=obj.schedule_config,
            schedule_display=obj.schedule_display,
            timezone=obj.timezone,
            misfire_policy=obj.misfire_policy,
            catchup_limit=obj.catchup_limit,
            enabled=True,
            status=status,
            source=obj.source,
            created_by_kind=obj.created_by_kind,
            continuation_enabled=obj.continuation_enabled,
            next_run_at=next_run_at,
            workflow_revision=1,
            project_id=obj.project_id,
        )
        db.add(workflow)
        await db.flush()

        # 节点 = task（workflow_uuid + node_key；调度由 WorkflowScheduler 触发，节点本身不自调度）
        for node in obj.nodes:
            task_uuid = f'tsk_{uuid.uuid4().hex}'
            await db.execute(
                sa.text(
                    'INSERT INTO hasn_task.task '
                    '(owner_id, agent_id, name, description, prompt, system_prompt, '
                    'skill_bundle_ids, skill_bundle_refs, skill_ids, skill_refs, workflow, '
                    'enabled_toolsets, schedule_type, schedule_config, timezone, misfire_policy, '
                    'enabled, state, task_uuid, executor_policy, created_by, created_by_kind, '
                    'enable_subagents, workflow_uuid, node_key, created_time) '
                    'VALUES (:o, :a, :n, :d, :p, :sp, '
                    "CAST(:sbi AS jsonb), CAST(:sbr AS jsonb), '[]'::jsonb, '[]'::jsonb, '{}'::jsonb, "
                    'CAST(:ets AS jsonb), :st, CAST(:sc AS jsonb), :tz, :mp, '
                    "true, 'scheduled', :tu, 'local_node', :cb, :cbk, "
                    ':esa, :wu, :nk, now())'
                ),
                {
                    'o': owner_id,
                    'a': node.agent_id,
                    'n': node.name or node.node_key,
                    'd': node.description,
                    'p': node.prompt,
                    'sp': node.system_prompt,
                    'sbi': _json(node.skill_bundle_ids),
                    'sbr': _json(node.skill_bundle_refs),
                    'ets': _json(node.enabled_toolsets) if node.enabled_toolsets is not None else None,
                    'st': obj.schedule_type,
                    'sc': _json(obj.schedule_config),
                    'tz': obj.timezone,
                    'mp': obj.misfire_policy,
                    'tu': task_uuid,
                    'cb': owner_id if obj.created_by_kind == 'owner' else None,
                    'cbk': obj.created_by_kind,
                    'esa': node.enable_subagents,
                    'wu': workflow_uuid,
                    'nk': node.node_key,
                },
            )
            # 双写节点专属表 workflow_node（P1 expand-only：读侧优先本表，task 节点行保留兼容）
            db.add(
                HasnWorkflowNode(
                    node_uuid=f'nd_{uuid.uuid4().hex}',
                    workflow_uuid=workflow_uuid,
                    owner_id=owner_id,
                    node_key=node.node_key,
                    name=node.name or node.node_key,
                    description=node.description,
                    agent_id=node.agent_id,
                    prompt=node.prompt,
                    system_prompt=node.system_prompt,
                    apps=node.apps,
                    skills=node.skills,
                    enabled_toolsets=node.enabled_toolsets,
                    # doc35 B1：这三个曾被硬编码成空值，把模板声明的应用绑定/起点标记/产出闸
                    # 在实例化时整段丢掉；节点表列一直在，只是从没被写进去过。
                    is_origin=node.is_origin,
                    output_spec=node.output_spec,
                    review_policy=node.review_policy,
                    display={},
                    max_retries=4,
                    enable_subagents=node.enable_subagents,
                )
            )

        # 边
        for edge in obj.edges:
            db.add(
                HasnWorkflowEdge(
                    workflow_uuid=workflow_uuid,
                    parent_node_key=edge.parent,
                    child_node_key=edge.child,
                )
            )
        await db.flush()
        return workflow

    @staticmethod
    async def definition_snapshot(db: AsyncSession, *, owner_id: str, workflow_uuid: str) -> dict[str, list[dict]]:
        """按已持久化的定义行返回稳定图快照，幂等重放绝不回显新请求里的可变参数。"""
        detail = await WorkflowService.get_workflow(db, owner_id=owner_id, workflow_uuid=workflow_uuid)
        return {'nodes': detail['nodes'], 'edges': detail['edges']}

    @staticmethod
    async def get_workflow(db: AsyncSession, *, owner_id: str, workflow_uuid: str) -> dict[str, Any]:
        """查图：workflow 定义 + 节点（优先专属表 workflow_node，旧数据回退 task 投影）+ 边。跨户 NotFound。"""
        workflow = await hasn_workflow_dao.get_by_uuid(db, workflow_uuid)
        if workflow is None or workflow.owner_id != owner_id or workflow.deleted_at is not None:
            raise errors.NotFoundError(msg='工作流不存在')

        # P1 expand-only：读侧优先节点专属表；未回填（旧数据）时回退 task 投影
        node_rows = await hasn_workflow_node_dao.list_by_workflow(db, workflow_uuid)
        if node_rows:
            nodes = [
                {
                    'node_key': n.node_key,
                    'agent_id': n.agent_id,
                    'name': n.name,
                    'prompt': n.prompt,
                    'system_prompt': n.system_prompt,
                    'enable_subagents': n.enable_subagents,
                    'description': n.description,
                    'is_origin': n.is_origin,
                    'apps': n.apps,
                    'skills': n.skills,
                    'enabled_toolsets': n.enabled_toolsets,
                    'output_spec': n.output_spec,
                    'review_policy': n.review_policy,
                    'max_retries': n.max_retries,
                    'display': n.display,
                }
                for n in node_rows
            ]
        else:
            nodes_result = await db.execute(
                sa.text(
                    'SELECT node_key, agent_id, name, prompt, system_prompt, enable_subagents, task_uuid '
                    "FROM hasn_task.task WHERE workflow_uuid = :wu AND state <> 'deleted' ORDER BY node_key"
                ),
                {'wu': workflow_uuid},
            )
            nodes = [
                {
                    **dict(row),
                    'description': None,
                    'is_origin': False,
                    'apps': [],
                    'skills': [],
                    'enabled_toolsets': None,
                    'output_spec': None,
                    'review_policy': None,
                    'max_retries': None,
                    'display': {},
                }
                for row in nodes_result.mappings().all()
            ]
        edges_rows = await hasn_workflow_edge_dao.list_by_workflow(db, workflow_uuid)
        edges = [{'parent': e.parent_node_key, 'child': e.child_node_key} for e in edges_rows]
        return {'workflow': workflow, 'nodes': nodes, 'edges': edges}

    @staticmethod
    async def list_workflows(db: AsyncSession, *, owner_id: str, project_id: str | None = None) -> list[HasnWorkflow]:
        """列某 owner 的工作流（未删除）；`project_id` 给值则只返挂在该项目下的（P9-D 项目侧聚合读）。"""
        return list(await hasn_workflow_dao.list_by_owner(db, owner_id, project_id=project_id))

    @staticmethod
    async def list_runs(db: AsyncSession, *, owner_id: str, workflow_uuid: str, limit: int = 50) -> list[dict]:
        """列某工作流的执行历史（跨户 NotFound）。"""
        workflow = await hasn_workflow_dao.get_by_uuid(db, workflow_uuid)
        if workflow is None or workflow.owner_id != owner_id:
            raise errors.NotFoundError(msg='工作流不存在')
        runs = await hasn_workflow_run_dao.list_by_workflow(db, workflow_uuid, limit=limit)
        return [
            {
                'workflow_run_id': r.workflow_run_uuid,
                'status': r.status,
                'advance_mode': r.advance_mode,
                'scheduled_fire_at': r.scheduled_fire_at.isoformat() if r.scheduled_fire_at else None,
                'started_at': r.started_at.isoformat() if r.started_at else None,
                'finished_at': r.finished_at.isoformat() if r.finished_at else None,
                'output_summary': r.output_summary,
                'created_at': r.created_time.isoformat() if r.created_time else None,
            }
            for r in runs
        ]


def _json(value: Any) -> str:
    return json.dumps(value if value is not None else [], ensure_ascii=False)


workflow_service = WorkflowService()
