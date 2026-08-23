"""工作流执行历史的权威只读投影（doc98 R3）。

执行记录属于云端账本，不能把仍可审计的 run 绑定到可变的 workflow 定义上：
父定义被清理、尚未同步或无权读取时，仍以 fire 时落下的快照返回历史，并显式标记
``definition_state=missing``。本服务没有任何执行控制入口，远端执行只能在原节点继续。
"""

from __future__ import annotations

import base64
import json

from collections.abc import Sequence
from datetime import datetime
from typing import TYPE_CHECKING, Any
from uuid import UUID

import sqlalchemy as sa

from backend.app.hasn_task.model import HasnWorkflow, HasnWorkflowNodeRun, HasnWorkflowRun
from backend.common.exception import errors

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession


_TERMINAL_DONE_STATUSES = frozenset({'done', 'completed', 'skipped'})
_LIST_STATUSES = frozenset({'all', 'running', 'completed', 'failed', 'blocked', 'cancelled'})
_READ_ONLY_CAPABILITIES = {
    'can_mutate': False,
    'mutation_reason': 'remote_execution',
    'work_session_events': False,
}


def _iso(value: datetime | None) -> str | None:
    """时区时间统一序列化为 ISO 串，空值保持为空。"""
    return value.isoformat() if value is not None else None


def _snapshot_node_keys(graph_snapshot: Any) -> list[str]:
    """读取图快照声明序中的有效节点键，并去重保序。"""
    if not isinstance(graph_snapshot, dict):
        return []
    nodes = graph_snapshot.get('nodes')
    if not isinstance(nodes, list):
        return []
    keys: list[str] = []
    seen: set[str] = set()
    for node in nodes:
        key = node.get('node_key') if isinstance(node, dict) else None
        if isinstance(key, str) and key and key not in seen:
            seen.add(key)
            keys.append(key)
    return keys


def _nodes_brief(snapshot_keys: list[str], node_runs: Sequence[HasnWorkflowNodeRun]) -> list[dict[str, Any]]:
    """按图快照声明序给出 `[{node_key, status}]`——历史卡片画链路点阵要的最小事实。

    只带 `node_key` 与 `status`：展示名归模板（消费端 daemon 用本机模板镜像 join），账本这边
    没有也不该有。快照未声明但账本里存在的节点补在末尾（与 `get_scenario_view` 同一口径），
    否则脱离父定义的历史会漏掉节点。
    """
    by_key = {node_run.node_key: node_run for node_run in node_runs}
    declared = set(snapshot_keys)
    ordered = [key for key in snapshot_keys if key in by_key]
    ordered.extend(node_run.node_key for node_run in sorted(node_runs, key=lambda item: item.id) if node_run.node_key not in declared)
    return [{'node_key': key, 'status': by_key[key].status} for key in ordered]


def _decode_cursor(cursor: str | None) -> tuple[datetime, str] | None:
    """解码 `(created_time DESC, workflow_run_uuid DESC)` 的不透明游标。"""
    if cursor is None:
        return None
    try:
        padding = '=' * (-len(cursor) % 4)
        payload = json.loads(base64.urlsafe_b64decode(cursor + padding).decode('utf-8'))
        created_time = datetime.fromisoformat(payload['created_time'])
        workflow_run_uuid = payload['workflow_run_uuid']
    except (KeyError, TypeError, ValueError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise errors.RequestError(msg='历史游标无效') from exc
    if not isinstance(workflow_run_uuid, str) or not workflow_run_uuid:
        raise errors.RequestError(msg='历史游标无效')
    return created_time, workflow_run_uuid


def _encode_cursor(run: HasnWorkflowRun) -> str:
    """将当前页最后一条记录编码为下一页游标。"""
    payload = json.dumps(
        {'created_time': run.created_time.isoformat(), 'workflow_run_uuid': run.workflow_run_uuid},
        separators=(',', ':'),
        sort_keys=True,
    ).encode('utf-8')
    return base64.urlsafe_b64encode(payload).decode('ascii').rstrip('=')


def _coerce_project_id(project_id: str | UUID | None) -> UUID | None:
    """把 API 字符串转换为 PostgreSQL UUID，非法输入明确报请求错误。"""
    if project_id is None:
        return None
    if isinstance(project_id, UUID):
        return project_id
    try:
        return UUID(project_id)
    except (AttributeError, TypeError, ValueError) as exc:
        raise errors.RequestError(msg='项目 ID 无效') from exc


class WorkflowHistoryService:
    """云端执行账本的历史列表与场景只读详情。"""

    @staticmethod
    def _run_projection(
        run: HasnWorkflowRun,
        *,
        definition_available: bool,
        workflow: HasnWorkflow | None,
        node_runs: Sequence[HasnWorkflowNodeRun],
    ) -> dict[str, Any]:
        """构造脱离父定义也完整的单条执行记录投影。"""
        snapshot_keys = _snapshot_node_keys(run.graph_snapshot)
        total = len(snapshot_keys) or len(node_runs)
        done = sum(node_run.status in _TERMINAL_DONE_STATUSES for node_run in node_runs)
        return {
            'workflow_run_id': run.workflow_run_uuid,
            'workflow_id': run.workflow_uuid,
            'workflow_name': run.workflow_name_snapshot or (workflow.name if workflow else None),
            'template_key': run.template_key_snapshot or (workflow.template_key if workflow else None),
            'project_id': str(run.project_id) if run.project_id else None,
            'status': run.status,
            'advance_mode': run.advance_mode,
            'progress': {'done': done, 'total': total},
            'nodes_brief': _nodes_brief(snapshot_keys, node_runs),
            'output_summary': run.output_summary,
            'started_at': _iso(run.started_at),
            'finished_at': _iso(run.finished_at),
            'created_at': _iso(run.created_time),
            'updated_at': _iso(run.updated_time),
            'definition_state': 'available' if definition_available else 'missing',
            'source': 'cloud',
            'capabilities': _READ_ONLY_CAPABILITIES.copy(),
        }

    @staticmethod
    def _current_artifact_ids(node_run: HasnWorkflowNodeRun) -> list[str]:
        """从节点账本提取当前产物 ID，显式非当前版本不向历史卡片暴露。"""
        entries = node_run.artifacts if isinstance(node_run.artifacts, list) else []
        artifact_ids: list[str] = []
        seen: set[str] = set()
        for entry in entries:
            if not isinstance(entry, dict) or not entry.get('is_current', True):
                continue
            artifact_id = entry.get('artifact_id')
            if isinstance(artifact_id, str) and artifact_id and artifact_id not in seen:
                seen.add(artifact_id)
                artifact_ids.append(artifact_id)
        return artifact_ids

    @staticmethod
    async def _load_owned_artifact_metadata(
        db: AsyncSession, *, owner_id: str, artifact_ids: set[str]
    ) -> dict[str, dict[str, Any]]:
        """仅加载当前 Owner 仍处于 active 状态的产物元数据，杜绝跨户 ID 透传。"""
        if not artifact_ids:
            return {}
        # 延迟导入避免任务模块加载期与 Agent 产物模型形成循环依赖。
        from backend.app.hasn.model.hasn_artifacts import HasnArtifacts

        result = await db.execute(
            sa.select(HasnArtifacts).where(
                HasnArtifacts.artifact_id.in_(artifact_ids),
                HasnArtifacts.owner_hasn_id == owner_id,
                HasnArtifacts.status == 'active',
            )
        )
        return {
            artifact.artifact_id: {
                'artifact_id': artifact.artifact_id,
                'title': artifact.title,
                'uri': artifact.resource_uri,
                'resource_kind': artifact.resource_kind,
                'source_app_id': artifact.source_app_id,
                'created_time': _iso(artifact.created_time),
            }
            for artifact in result.scalars().all()
        }

    @staticmethod
    async def _node_runs_by_workflow_run(
        db: AsyncSession, workflow_run_uuids: list[str], *, owner_id: str
    ) -> dict[str, list[HasnWorkflowNodeRun]]:
        """批量读取节点账本，避免列表页随记录数膨胀为 N+1 查询。"""
        if not workflow_run_uuids:
            return {}
        rows = (
            (
                await db.execute(
                    sa.select(HasnWorkflowNodeRun).where(
                        HasnWorkflowNodeRun.owner_id == owner_id,
                        HasnWorkflowNodeRun.workflow_run_uuid.in_(workflow_run_uuids),
                    )
                )
            )
            .scalars()
            .all()
        )
        result: dict[str, list[HasnWorkflowNodeRun]] = {run_uuid: [] for run_uuid in workflow_run_uuids}
        for row in rows:
            result.setdefault(row.workflow_run_uuid, []).append(row)
        return result

    async def list_runs(
        self,
        db: AsyncSession,
        *,
        owner_id: str,
        status: str = 'all',
        project_id: str | UUID | None = None,
        cursor: str | None = None,
        limit: int = 50,
    ) -> dict[str, Any]:
        """按创建时间倒序列主人历史，父定义缺失的记录也不可丢失。"""
        normalized_status = status.strip().lower()
        if normalized_status not in _LIST_STATUSES:
            raise errors.RequestError(msg='执行状态筛选无效')
        bounded_limit = min(max(limit, 1), 100)
        parsed_project_id = _coerce_project_id(project_id)
        parsed_cursor = _decode_cursor(cursor)

        # `deleted_at IS NULL` 只决定定义是否可用，绝不可放入 WHERE，否则会滤掉孤儿执行账本。
        workflow_join = sa.and_(
            HasnWorkflow.workflow_uuid == HasnWorkflowRun.workflow_uuid,
            HasnWorkflow.owner_id == HasnWorkflowRun.owner_id,
            HasnWorkflow.deleted_at.is_(None),
        )
        statement = (
            sa
            .select(HasnWorkflowRun, HasnWorkflow)
            .outerjoin(HasnWorkflow, workflow_join)
            .where(HasnWorkflowRun.owner_id == owner_id)
        )
        if normalized_status != 'all':
            statement = statement.where(HasnWorkflowRun.status == normalized_status)
        if parsed_project_id is not None:
            statement = statement.where(HasnWorkflowRun.project_id == parsed_project_id)
        if parsed_cursor is not None:
            cursor_time, cursor_run_uuid = parsed_cursor
            statement = statement.where(
                sa.or_(
                    HasnWorkflowRun.created_time < cursor_time,
                    sa.and_(
                        HasnWorkflowRun.created_time == cursor_time,
                        HasnWorkflowRun.workflow_run_uuid < cursor_run_uuid,
                    ),
                )
            )
        rows = (
            await db.execute(
                statement.order_by(HasnWorkflowRun.created_time.desc(), HasnWorkflowRun.workflow_run_uuid.desc()).limit(
                    bounded_limit + 1
                )
            )
        ).all()
        has_next = len(rows) > bounded_limit
        page_rows = rows[:bounded_limit]
        runs = [row[0] for row in page_rows]
        node_runs = await self._node_runs_by_workflow_run(
            db, [run.workflow_run_uuid for run in runs], owner_id=owner_id
        )
        items = [
            self._run_projection(
                run,
                definition_available=workflow is not None,
                workflow=workflow,
                node_runs=node_runs.get(run.workflow_run_uuid, []),
            )
            for run, workflow in page_rows
        ]
        return {
            'items': items,
            'next_cursor': _encode_cursor(runs[-1]) if has_next and runs else None,
        }

    async def get_scenario_view(self, db: AsyncSession, *, owner_id: str, workflow_run_uuid: str) -> dict[str, Any]:
        """返回一个执行实例的只读场景视图；越权与不存在均统一为 404。"""
        workflow_join = sa.and_(
            HasnWorkflow.workflow_uuid == HasnWorkflowRun.workflow_uuid,
            HasnWorkflow.owner_id == HasnWorkflowRun.owner_id,
            HasnWorkflow.deleted_at.is_(None),
        )
        row = (
            await db.execute(
                sa
                .select(HasnWorkflowRun, HasnWorkflow)
                .outerjoin(HasnWorkflow, workflow_join)
                .where(
                    HasnWorkflowRun.owner_id == owner_id,
                    HasnWorkflowRun.workflow_run_uuid == workflow_run_uuid,
                )
            )
        ).first()
        if row is None:
            raise errors.NotFoundError(msg='工作流执行实例不存在')
        run, workflow = row
        node_runs = (
            (
                await db.execute(
                    sa.select(HasnWorkflowNodeRun).where(
                        HasnWorkflowNodeRun.owner_id == owner_id,
                        HasnWorkflowNodeRun.workflow_run_uuid == workflow_run_uuid,
                    )
                )
            )
            .scalars()
            .all()
        )
        by_key = {node_run.node_key: node_run for node_run in node_runs}
        declared_keys = _snapshot_node_keys(run.graph_snapshot)
        declared_set = set(declared_keys)
        ordered_keys = [key for key in declared_keys if key in by_key]
        ordered_keys.extend(
            node_run.node_key
            for node_run in sorted(node_runs, key=lambda item: item.id)
            if node_run.node_key not in declared_set
        )
        artifact_ids = {artifact_id for node_run in node_runs for artifact_id in self._current_artifact_ids(node_run)}
        artifacts = await self._load_owned_artifact_metadata(db, owner_id=owner_id, artifact_ids=artifact_ids)
        nodes = [
            {
                'node_run_id': by_key[key].node_run_uuid,
                'node_key': key,
                'status': by_key[key].status,
                'work_session_id': by_key[key].work_session_id,
                'output_summary': by_key[key].output_summary,
                'attention_reason': by_key[key].attention_reason,
                'started_at': _iso(by_key[key].started_time),
                'completed_at': _iso(by_key[key].completed_time),
                'artifacts': [
                    artifacts[artifact_id]
                    for artifact_id in self._current_artifact_ids(by_key[key])
                    if artifact_id in artifacts
                ],
            }
            for key in ordered_keys
        ]
        return {
            'run': self._run_projection(
                run,
                definition_available=workflow is not None,
                workflow=workflow,
                node_runs=node_runs,
            ),
            'graph_snapshot': run.graph_snapshot if isinstance(run.graph_snapshot, dict) else {},
            'nodes': nodes,
            'source': 'cloud',
            'capabilities': _READ_ONLY_CAPABILITIES.copy(),
            'availability': {'work_session_events': 'unavailable_on_this_node'},
        }


workflow_history_service = WorkflowHistoryService()
