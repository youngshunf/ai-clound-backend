"""P0 HASN sync service。

本 service 持有手写的 hasn-node API 边界：hasn-node 用这些脱敏、按 owner 收敛的方法，
而不是通用表 CRUD；codegen 出的 CRUD 仅供管理端查看。

2026-08-10：Runtime 上报写入链路（`save_runtime_report` / `report_runtime` /
`_refresh_task_assignments_for_runtime_report`）随云端 Runtime 形态退役一并摘除。
"""

from __future__ import annotations

import json
import uuid

from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any, Protocol

import sqlalchemy as sa

from backend.app.hasn.model import HasnHumans
from backend.app.hasn.schema.hasn_message_hub import ErrorObject
from backend.app.hasn.schema.hasn_sync import (
    ClientEvent,
    MemorySyncCursor,
    MemorySyncPullRequest,
    MemorySyncPullResponse,
    SyncEventRecord,
    SyncPullRequest,
    SyncPullResponse,
    SyncPushRequest,
    SyncPushResponse,
    TaskRunSummaryRequest,
    TaskRunSummaryResponse,
)
from backend.app.hasn.service._sync_codec import (
    _MEMORY_NAMESPACE_UNKNOWN_ERROR,
    TaskSyncConflictError,
    _advance_memory_cursors,
    _assert_task_revision_not_stale,
    _coerce_datetime,
    _coerce_dict,
    _contains_private_runtime_key,
    _memory_aggregate_id,
    _memory_namespace_allowed,
    _memory_namespace_revision_key,
    _memory_pull_selections,
    _owner_cursor,
    _parse_owner_cursor,
    _parse_task_cursor,
    _required_string,
    _task_cursor,
    _task_payload_for_storage,
    _task_run_summary_event_payload,
    _task_run_summary_for_storage,
    _task_run_summary_response_payload,
    _task_storage_row,
    _task_sync_payload,
)
from backend.app.hasn_sync.adapters.sqlalchemy_appender import SqlAlchemySyncAppender
from backend.app.hasn_sync.ports.dto import SyncEnvelope
from backend.common.exception import errors
from backend.database.schema_names import SCHEMA_NAMES
from backend.utils.timezone import timezone

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession

    from backend.common.dataclasses import AgentTokenPayload

_PRIVATE_METADATA_ERROR = ErrorObject(
    code=8034,
    name='ERR_RUNTIME_PRIVATE_METADATA_REJECTED',
    message='Runtime report contains private local metadata.',
)

_MEMORY_SYNC_SCOPE_ERROR = ErrorObject(
    code=8035,
    name='ERR_MEMORY_SYNC_SCOPE_INVALID',
    message='Memory sync payload missing sync_scope_kind, sync_scope_id, or namespace.',
)


_TASK_EVENT_UNSUPPORTED_ERROR = ErrorObject(
    code=8037,
    name='ERR_TASK_SYNC_EVENT_UNSUPPORTED',
    message='Task sync payload references unsupported event type.',
)

_TASK_SYNC_CONFLICT_ERROR = ErrorObject(
    code=8038,
    name='ERR_TASK_SYNC_CONFLICT',
    message='Task sync payload is based on a stale task revision.',
)

TASK_SYNC_EVENT_TYPES = {'task.created', 'task.updated', 'task.deleted'}

_SYNC_EVENTS = SCHEMA_NAMES.sync_table('hasn_sync_events')
_SYNC_INBOX_EVENTS = SCHEMA_NAMES.sync_table('hasn_sync_inbox_events')


class SyncGateway(Protocol):
    async def pull_events(
        self, db: AsyncSession, *, owner_id: str, after_revision: int, limit: int
    ) -> list[SyncEventRecord]: ...
    async def pull_task_events(
        self, db: AsyncSession, *, owner_id: str, node_id: str | None, after_revision: int, limit: int
    ) -> list[SyncEventRecord]: ...
    async def pull_memory_events(
        self,
        db: AsyncSession,
        *,
        owner_id: str,
        selections: list[MemorySyncCursor],
        limit: int,
    ) -> list[SyncEventRecord]: ...
    async def owns_owner(self, db: AsyncSession, *, owner_id: str, user_id: int) -> bool: ...
    async def save_session(self, db: AsyncSession, session: dict[str, Any]) -> None: ...
    async def save_session_event(self, db: AsyncSession, event: dict[str, Any]) -> None: ...
    async def save_session_artifact(self, db: AsyncSession, artifact: dict[str, Any]) -> None: ...
    async def existing_client_event_revision(
        self, db: AsyncSession, *, owner_id: str, node_id: str, client_event_id: str
    ) -> int | None: ...
    async def save_task_event(self, db: AsyncSession, *, owner_id: str, node_id: str, event: ClientEvent) -> int | None: ...
    async def emit_memory_event(
        self,
        db: AsyncSession,
        *,
        owner_id: str,
        event_type: str,
        namespace: str,
        aggregate_id: str,
        payload: dict[str, Any],
        sync_scope_kind: str = 'owner',
        sync_scope_id: str | None = None,
        hasn_id: str | None = None,
    ) -> tuple[int, str]: ...
    async def save_task_run_summary(
        self,
        db: AsyncSession,
        *,
        owner_id: str,
        agent_hasn_id: str,
        summary: dict[str, Any],
    ) -> dict[str, Any]: ...


class SqlAlchemySyncGateway:
    async def owns_owner(self, db: AsyncSession, *, owner_id: str, user_id: int) -> bool:
        result = await db.execute(
            sa.select(HasnHumans.id)
            .where(
                HasnHumans.hasn_id == owner_id,
                HasnHumans.user_id == user_id,
                HasnHumans.status == 'active',
            )
            .limit(1)
        )
        return result.scalar_one_or_none() is not None

    async def pull_events(
        self, db: AsyncSession, *, owner_id: str, after_revision: int, limit: int
    ) -> list[SyncEventRecord]:
        result = await db.execute(
            sa.text(
                f"""
                SELECT event_id, event_type, revision, occurred_at, payload
                FROM {_SYNC_EVENTS}
                WHERE owner_id = :owner_id
                  AND event_type NOT LIKE 'task.%'
                  AND event_type <> 'task_run.summary_reported'
                  AND revision > :after_revision
                ORDER BY revision ASC
                LIMIT :limit
                """
            ),
            {'owner_id': owner_id, 'after_revision': after_revision, 'limit': limit},
        )
        return [
            SyncEventRecord(
                event_id=row['event_id'],
                event_type=row['event_type'],
                revision=int(row['revision']),
                created_at=_coerce_datetime(row['occurred_at']),
                payload=_coerce_dict(row['payload']),
            )
            for row in result.mappings().all()
        ]

    async def pull_task_events(
        self, db: AsyncSession, *, owner_id: str, node_id: str | None, after_revision: int, limit: int
    ) -> list[SyncEventRecord]:
        result = await db.execute(
            sa.text(
                f"""
                SELECT event_id, event_type, revision, occurred_at, payload
                FROM {_SYNC_EVENTS} e
                WHERE e.owner_id = :owner_id
                  AND revision > :after_revision
                  AND (
                    event_type LIKE 'task.%'
                    OR event_type = 'task_run.summary_reported'
                  )
                  AND (
                    CAST(:node_id AS text) IS NULL
                    OR :node_id = ''
                    OR event_type = 'task_run.summary_reported'
                    -- 内置任务（cloud seed）按 owner 广播到所有节点：云端播种时尚不知道哪个
                    -- 节点承载承接分身的 runtime，故 created_by_kind='builtin' 的 task 事件对
                    -- owner 名下每个节点可见，由本地调度器按 runtime 是否在位决定是否真正派发。
                    -- （普通任务仍按 executor-pinned 模型路由，此分支仅放宽内置任务可见性。）
                    OR e.payload->>'created_by_kind' = 'builtin'
                    OR (
                      jsonb_typeof(e.payload->'visible_node_ids') = 'array'
                      AND jsonb_exists(e.payload->'visible_node_ids', :node_id)
                    )
                    OR (
                      NOT (e.payload ? 'visible_node_ids')
                      AND COALESCE(
                        NULLIF(e.payload->>'executor_node_id', ''),
                        NULLIF(e.payload->>'node_id', '')
                      ) = :node_id
                    )
                  )
                ORDER BY revision ASC
                LIMIT :limit
                """
            ),
            {'owner_id': owner_id, 'node_id': node_id, 'after_revision': after_revision, 'limit': limit},
        )
        return [
            SyncEventRecord(
                event_id=row['event_id'],
                event_type=row['event_type'],
                revision=int(row['revision']),
                created_at=_coerce_datetime(row['occurred_at']),
                payload=_coerce_dict(row['payload']),
            )
            for row in result.mappings().all()
        ]

    async def pull_memory_events(
        self,
        db: AsyncSession,
        *,
        owner_id: str,
        selections: list[MemorySyncCursor],
        limit: int,
    ) -> list[SyncEventRecord]:
        if not selections:
            return []
        selection_values = [
            {
                'sync_scope_kind': cursor.sync_scope_kind,
                'sync_scope_id': cursor.sync_scope_id,
                'namespace': cursor.namespace,
                'last_pulled_revision': cursor.last_pulled_revision,
            }
            for cursor in selections
        ]
        result = await db.execute(
            sa.text(
                f"""
                WITH requested(sync_scope_kind, sync_scope_id, namespace, last_pulled_revision) AS (
                    SELECT *
                    FROM jsonb_to_recordset(CAST(:selections AS jsonb))
                    AS x(sync_scope_kind text, sync_scope_id text, namespace text, last_pulled_revision bigint)
                )
                SELECT event_id, event_type, revision, occurred_at, payload
                FROM {_SYNC_EVENTS} e
                JOIN requested r
                  ON e.payload->>'sync_scope_kind' = r.sync_scope_kind
                 AND e.payload->>'sync_scope_id' = r.sync_scope_id
                 AND e.payload->>'namespace' = r.namespace
                WHERE e.owner_id = :owner_id
                  AND e.event_type LIKE 'memory.%'
                  AND COALESCE((e.payload->>'namespace_revision')::bigint, 0) > r.last_pulled_revision
                ORDER BY e.revision ASC
                LIMIT :limit
                """
            ),
            {
                'owner_id': owner_id,
                'selections': json.dumps(selection_values, ensure_ascii=False, sort_keys=True, default=str),
                'limit': limit,
            },
        )
        return [
            SyncEventRecord(
                event_id=row['event_id'],
                event_type=row['event_type'],
                revision=int(row['revision']),
                created_at=_coerce_datetime(row['occurred_at']),
                payload=_coerce_dict(row['payload']),
            )
            for row in result.mappings().all()
        ]

    async def save_client_event(
        self,
        db: AsyncSession,
        *,
        owner_id: str,
        node_id: str,
        event: ClientEvent,
        manage_inbox: bool = True,
    ) -> int | None:
        if manage_inbox:
            existing_revision = await self.existing_client_event_revision(
                db,
                owner_id=owner_id,
                node_id=node_id,
                client_event_id=event.client_event_id,
            )
            if existing_revision is not None:
                return existing_revision

        server_revision = None
        if event.event_type.startswith('memory.'):
            sync_scope_kind, sync_scope_id, namespace = _memory_namespace_revision_key(event)
            if not _memory_namespace_allowed(sync_scope_kind, namespace):
                raise errors.RequestError(
                    msg=_MEMORY_NAMESPACE_UNKNOWN_ERROR.name,
                    data=_MEMORY_NAMESPACE_UNKNOWN_ERROR.model_dump(),
                )
            namespace_revision = await self._advance_memory_namespace_revision(
                db,
                sync_scope_kind=sync_scope_kind,
                sync_scope_id=sync_scope_id,
                namespace=namespace,
            )
            server_revision, event_id, _deduped = await self._append_sync_event_with_id(
                db,
                owner_id=owner_id,
                hasn_id=event.hasn_id or owner_id,
                event_type=event.event_type,
                aggregate_type='memory',
                aggregate_id=_memory_aggregate_id(event),
                payload={
                    **event.payload,
                    'client_event_id': event.client_event_id,
                    'node_id': node_id,
                    'namespace_revision': namespace_revision,
                },
            )
            await self._set_memory_namespace_last_event(
                db,
                sync_scope_kind=sync_scope_kind,
                sync_scope_id=sync_scope_id,
                namespace=namespace,
                event_id=event_id,
            )
        else:
            raise errors.RequestError(msg='ERR_SYNC_EVENT_UNSUPPORTED')
        if manage_inbox:
            await db.execute(
                sa.text(
                    f"""
                    INSERT INTO {_SYNC_INBOX_EVENTS} (
                        client_event_id,
                        owner_id,
                        hasn_id,
                        node_id,
                        event_type,
                        payload,
                        dedupe_key,
                        status,
                        server_revision,
                        received_at,
                        created_time,
                        updated_time
                    ) VALUES (
                        :client_event_id,
                        :owner_id,
                        :hasn_id,
                        :node_id,
                        :event_type,
                        CAST(:payload AS jsonb),
                        :dedupe_key,
                        :status,
                        :server_revision,
                        now(),
                        now(),
                        now()
                    )
                    ON CONFLICT (owner_id, node_id, client_event_id) DO NOTHING
                    """
                ),
                {
                    'client_event_id': event.client_event_id,
                    'owner_id': owner_id,
                    'hasn_id': event.hasn_id or owner_id,
                    'node_id': node_id,
                    'event_type': event.event_type,
                    'payload': json.dumps(event.payload, ensure_ascii=False, sort_keys=True, default=str),
                    'dedupe_key': event.dedupe_key,
                    'status': 'applied' if server_revision is not None else 'accepted',
                    'server_revision': server_revision,
                },
            )
        return server_revision

    async def save_task_event(
        self,
        db: AsyncSession,
        *,
        owner_id: str,
        node_id: str,
        event: ClientEvent,
        manage_inbox: bool = True,
    ) -> int | None:
        if manage_inbox:
            existing_revision = await self.existing_client_event_revision(
                db,
                owner_id=owner_id,
                node_id=node_id,
                client_event_id=event.client_event_id,
            )
            if existing_revision is not None:
                return existing_revision

        task_payload = _task_payload_for_storage(owner_id, event)
        task_uuid = _required_string(task_payload, 'task_id', 'ERR_TASK_ID_REQUIRED')
        current_task = await self._current_task_revision(db, owner_id=owner_id, task_uuid=task_uuid)
        _assert_task_revision_not_stale(event, task_payload, current_task)
        now = timezone.now()
        stored_task = _task_storage_row(owner_id, task_uuid, task_payload, event, now)
        revision = await self._upsert_task_and_append_event(
            db,
            owner_id=owner_id,
            node_id=node_id,
            event=event,
            task_uuid=task_uuid,
            stored_task=stored_task,
            event_payload=_task_sync_payload(task_uuid, stored_task, task_payload, event),
            manage_inbox=manage_inbox,
        )
        return revision

    async def _upsert_current_assignment(
        self,
        db: AsyncSession,
        *,
        task_uuid: str,
        owner_id: str,
        agent_id: str,
        assignment: dict[str, Any],
    ) -> None:
        await db.execute(
            sa.text(
                """
                DELETE FROM hasn_task.assignment
                WHERE owner_id = :owner_id
                  AND task_uuid = :task_uuid
                """
            ),
            {'owner_id': owner_id, 'task_uuid': task_uuid},
        )
        await db.execute(
            sa.text(
                """
                INSERT INTO hasn_task.assignment (
                    task_uuid,
                    owner_id,
                    agent_id,
                    executor_kind,
                    executor_node_id,
                    binding_id,
                    assignment_state,
                    resolved_at,
                    stale_after,
                    created_time,
                    updated_time
                ) VALUES (
                    :task_uuid,
                    :owner_id,
                    :agent_id,
                    :executor_kind,
                    :executor_node_id,
                    :binding_id,
                    :assignment_state,
                    :resolved_at,
                    NULL,
                    now(),
                    now()
                )
                """
            ),
            {
                'task_uuid': task_uuid,
                'owner_id': owner_id,
                'agent_id': agent_id,
                **assignment,
            },
        )

    async def _current_task_revision(
        self,
        db: AsyncSession,
        *,
        owner_id: str,
        task_uuid: str,
    ) -> dict[str, Any] | None:
        result = await db.execute(
            sa.text(
                """
                SELECT task_revision, state
                FROM hasn_task.task
                WHERE owner_id = :owner_id
                  AND task_uuid = :task_uuid
                LIMIT 1
                """
            ),
            {'owner_id': owner_id, 'task_uuid': task_uuid},
        )
        row = result.mappings().first()
        return dict(row) if row is not None else None

    async def _upsert_task_and_append_event(
        self,
        db: AsyncSession,
        *,
        owner_id: str,
        node_id: str,
        event: ClientEvent,
        task_uuid: str,
        stored_task: dict[str, Any],
        event_payload: dict[str, Any],
        manage_inbox: bool = True,
    ) -> int:
        skill_bundle_refs = json.dumps(
            stored_task['skill_bundle_refs'],
            ensure_ascii=False,
            sort_keys=True,
            default=str,
        )
        skill_refs = json.dumps(stored_task['skill_refs'], ensure_ascii=False, sort_keys=True, default=str)
        workflow = json.dumps(stored_task['workflow'], ensure_ascii=False, sort_keys=True, default=str)
        schedule_config = json.dumps(
            stored_task['schedule_config'],
            ensure_ascii=False,
            sort_keys=True,
            default=str,
        )
        # 任务中心三轴四列（doc12 §6.1）：execution_spec 是 jsonb，照 schedule_config 先 json.dumps 再 CAST；
        # project_id 是 uuid 列——绑「规范化字符串 + VALUES 处 CAST(:project_id AS uuid)」（asyncpg+SQLAlchemy 下
        # 绑 uuid.UUID 对象到无类型上下文会 DataError，绑字符串经 CAST 才稳）；非法 uuid 降级为 NULL 保任务写入韧性
        # （doc38：project_id 只是「为了哪件事」的业务归属标签，非权限边界，摘/删项目不中断执行）。
        execution_spec = json.dumps(
            stored_task['execution_spec'],
            ensure_ascii=False,
            sort_keys=True,
            default=str,
        )
        raw_project_id = stored_task.get('project_id')
        try:
            project_id_param = str(uuid.UUID(str(raw_project_id))) if raw_project_id else None
        except ValueError:
            project_id_param = None
        task_upsert_result = await db.execute(
            sa.text(
                """
                INSERT INTO hasn_task.task (
                    owner_id,
                    agent_id,
                    name,
                    description,
                    prompt,
                    system_prompt,
                    input_template,
                    skill_bundle_ids,
                    skill_bundle_refs,
                    skill_ids,
                    skill_refs,
                    workflow_id,
                    workflow,
                    enabled_toolsets,
                    context_from_task_id,
                    schedule_type,
                    schedule_config,
                    schedule_display,
                    risk_level,
                    timezone,
                    misfire_policy,
                    catchup_limit,
                    enabled,
                    state,
                    next_run_at,
                    run_count,
                    repeat_times,
                    repeat_completed,
                    task_uuid,
                    executor_policy,
                    executor_node_id,
                    task_revision,
                    deleted_at,
                    created_by,
                    continuation_enabled,
                    enable_subagents,
                    created_by_kind,
                    builtin_key,
                    builtin_synced_revision,
                    target_scope,
                    project_id,
                    app_id,
                    execution_kind,
                    execution_spec,
                    created_time,
                    updated_time
                ) VALUES (
                    :owner_id,
                    :agent_id,
                    :name,
                    :description,
                    :prompt,
                    :system_prompt,
                    :input_template,
                    CAST(:skill_bundle_ids AS jsonb),
                    CAST(:skill_bundle_refs AS jsonb),
                    CAST(:skill_ids AS jsonb),
                    CAST(:skill_refs AS jsonb),
                    :workflow_id,
                    CAST(:workflow AS jsonb),
                    CAST(:enabled_toolsets AS jsonb),
                    :context_from_task_id,
                    :schedule_type,
                    CAST(:schedule_config AS jsonb),
                    :schedule_display,
                    :risk_level,
                    :timezone,
                    :misfire_policy,
                    :catchup_limit,
                    :enabled,
                    :state,
                    :next_run_at,
                    :run_count,
                    :repeat_times,
                    :repeat_completed,
                    :task_uuid,
                    :executor_policy,
                    :executor_node_id,
                    1,
                    :deleted_at,
                    :created_by,
                    :continuation_enabled,
                    :enable_subagents,
                    :created_by_kind,
                    :builtin_key,
                    :builtin_synced_revision,
                    :target_scope,
                    CAST(:project_id AS uuid),
                    :app_id,
                    :execution_kind,
                    CAST(:execution_spec AS jsonb),
                    :created_time,
                    :updated_time
                )
                ON CONFLICT (task_uuid) DO UPDATE SET
                    agent_id = EXCLUDED.agent_id,
                    name = EXCLUDED.name,
                    description = EXCLUDED.description,
                    prompt = EXCLUDED.prompt,
                    system_prompt = EXCLUDED.system_prompt,
                    input_template = EXCLUDED.input_template,
                    skill_bundle_ids = EXCLUDED.skill_bundle_ids,
                    skill_bundle_refs = EXCLUDED.skill_bundle_refs,
                    skill_ids = EXCLUDED.skill_ids,
                    skill_refs = EXCLUDED.skill_refs,
                    workflow_id = EXCLUDED.workflow_id,
                    workflow = EXCLUDED.workflow,
                    enabled_toolsets = EXCLUDED.enabled_toolsets,
                    context_from_task_id = EXCLUDED.context_from_task_id,
                    schedule_type = EXCLUDED.schedule_type,
                    schedule_config = EXCLUDED.schedule_config,
                    schedule_display = EXCLUDED.schedule_display,
                    risk_level = EXCLUDED.risk_level,
                    timezone = EXCLUDED.timezone,
                    misfire_policy = EXCLUDED.misfire_policy,
                    catchup_limit = EXCLUDED.catchup_limit,
                    enabled = EXCLUDED.enabled,
                    state = CASE
                        WHEN hasn_task.task.state = 'deleted' AND EXCLUDED.state <> 'deleted' THEN hasn_task.task.state
                        ELSE EXCLUDED.state
                    END,
                    next_run_at = EXCLUDED.next_run_at,
                    run_count = EXCLUDED.run_count,
                    repeat_times = EXCLUDED.repeat_times,
                    repeat_completed = EXCLUDED.repeat_completed,
                    executor_policy = EXCLUDED.executor_policy,
                    executor_node_id = EXCLUDED.executor_node_id,
                    task_revision = hasn_task.task.task_revision + 1,
                    deleted_at = EXCLUDED.deleted_at,
                    continuation_enabled = EXCLUDED.continuation_enabled,
                    enable_subagents = EXCLUDED.enable_subagents,
                    created_by_kind = EXCLUDED.created_by_kind,
                    builtin_key = COALESCE(EXCLUDED.builtin_key, hasn_task.task.builtin_key),
                    builtin_synced_revision = COALESCE(
                        EXCLUDED.builtin_synced_revision, hasn_task.task.builtin_synced_revision
                    ),
                    -- 内置任务广播语义（doc19 §9 / D-24）：随 catalog 定义更新（refresh-builtin 也走这里）
                    target_scope = EXCLUDED.target_scope,
                    project_id = EXCLUDED.project_id,
                    app_id = EXCLUDED.app_id,
                    execution_kind = EXCLUDED.execution_kind,
                    execution_spec = EXCLUDED.execution_spec,
                    updated_time = EXCLUDED.updated_time
                RETURNING id
                """
            ),
            {
                **stored_task,
                'skill_bundle_ids': json.dumps(stored_task['skill_bundle_ids'], ensure_ascii=False),
                'skill_bundle_refs': skill_bundle_refs,
                'skill_ids': json.dumps(stored_task['skill_ids'], ensure_ascii=False),
                'skill_refs': skill_refs,
                'workflow': workflow,
                'enabled_toolsets': json.dumps(stored_task['enabled_toolsets'], ensure_ascii=False, default=str)
                if stored_task['enabled_toolsets'] is not None
                else None,
                'schedule_config': schedule_config,
                # 三轴四列：project_id 绑规范化 uuid 字符串（VALUES 处 CAST AS uuid），execution_spec 绑 json 字符串（CAST AS jsonb）
                'project_id': project_id_param,
                'execution_spec': execution_spec,
            },
        )
        # 把云端整型主键（bigserial id）回填进同步事件 payload 的 server_id。
        # 云端原生任务（内置任务 cloud seed / 跨设备下行）在播种前不知道这个数字 id，
        # 因此下行节点本地一直存 server_id=None，导致「立即执行」run-now 与 §6.6 手动
        # 更新（refresh-builtin）都因「task id 非数字」失败。upsert 后 RETURNING id 拿到
        # 权威整型主键，注入 payload（普通任务亦无害——值即该行权威 id，幂等）。
        task_server_id = int(task_upsert_result.scalar_one())
        event_executor_node_id = stored_task['executor_node_id'] or node_id
        event_payload = {
            **event_payload,
            'server_id': task_server_id,
            'visible_node_ids': [event_executor_node_id],
        }
        await self._upsert_current_assignment(
            db,
            task_uuid=task_uuid,
            owner_id=owner_id,
            agent_id=stored_task['agent_id'],
            assignment={
                'executor_kind': stored_task['executor_policy'],
                'executor_node_id': event_executor_node_id,
                'binding_id': stored_task.get('binding_id'),
                'assignment_state': 'unresolved' if event_payload.get('state') == 'deleted' else 'assigned',
                'resolved_at': stored_task['updated_time'],
            },
        )
        revision, _event_id, _deduped = await self._append_sync_event_with_id(
            db,
            owner_id=owner_id,
            hasn_id=stored_task['agent_id'] or owner_id,
            event_type=event.event_type,
            aggregate_type='task',
            aggregate_id=task_uuid,
            payload={
                **event_payload,
                'client_event_id': event.client_event_id,
                'node_id': node_id,
            },
        )
        if manage_inbox:
            await db.execute(
                sa.text(
                    f"""
                    INSERT INTO {_SYNC_INBOX_EVENTS} (
                        client_event_id,
                        owner_id,
                        hasn_id,
                        node_id,
                        event_type,
                        payload,
                        dedupe_key,
                        status,
                        server_revision,
                        received_at,
                        created_time,
                        updated_time
                    ) VALUES (
                        :client_event_id,
                        :owner_id,
                        :hasn_id,
                        :node_id,
                        :event_type,
                        CAST(:payload AS jsonb),
                        :dedupe_key,
                        'applied',
                        :server_revision,
                        now(),
                        now(),
                        now()
                    )
                    ON CONFLICT (owner_id, node_id, client_event_id) DO NOTHING
                    """
                ),
                {
                    'client_event_id': event.client_event_id,
                    'owner_id': owner_id,
                    'hasn_id': event.hasn_id or owner_id,
                    'node_id': node_id,
                    'event_type': event.event_type,
                    'payload': json.dumps(event_payload, ensure_ascii=False, sort_keys=True, default=str),
                    'dedupe_key': event.dedupe_key,
                    'server_revision': revision,
                },
            )
        return revision

    async def save_task_run_summary(
        self,
        db: AsyncSession,
        *,
        owner_id: str,
        agent_hasn_id: str,
        summary: dict[str, Any],
    ) -> dict[str, Any]:
        task_uuid = _required_string(summary, 'task_uuid', 'ERR_TASK_ID_REQUIRED')
        result = await db.execute(
            sa.text(
                """
                SELECT
                    task.owner_id,
                    task.agent_id,
                    task.target_scope,
                    EXISTS (
                        SELECT 1
                        FROM hasn_agents AS agent
                        WHERE agent.hasn_id = :reporter_agent_id
                          AND agent.owner_id = task.owner_id
                          AND agent.status = 'active'
                    ) AS reporter_owned
                FROM hasn_task.task AS task
                WHERE task.task_uuid = :task_uuid
                LIMIT 1
                """
            ),
            {'task_uuid': task_uuid, 'reporter_agent_id': agent_hasn_id},
        )
        task_row = result.mappings().first()
        if task_row is not None:
            # 普通任务仍只允许绑定分身上报；all_agents 由 daemon 在本节点逐分身扇出，
            # 每条 run 必须允许实际执行且仍归属同一主人的分身上报自己的摘要。
            reporter_allowed = task_row['agent_id'] == agent_hasn_id or (
                task_row['target_scope'] == 'all_agents' and bool(task_row['reporter_owned'])
            )
            if task_row['owner_id'] != owner_id or not reporter_allowed:
                raise errors.ForbiddenError(msg='agent cannot report this task run')

        payload_json = {
            'token_usage': json.dumps(summary.get('token_usage'), ensure_ascii=False, sort_keys=True, default=str)
            if summary.get('token_usage') is not None
            else None
        }
        result = await db.execute(
            sa.text(
                """
                INSERT INTO hasn_task.run_summary (
                    run_uuid,
                    task_uuid,
                    owner_id,
                    agent_id,
                    executor_node_id,
                    session_id,
                    scheduled_fire_at,
                    dedupe_key,
                    status,
                    output_summary,
                    error,
                    deep_link,
                    model,
                    token_usage,
                    duration_ms,
                    started_at,
                    finished_at,
                    created_time,
                    updated_time
                ) VALUES (
                    :run_uuid,
                    :task_uuid,
                    :owner_id,
                    :agent_id,
                    :executor_node_id,
                    :session_id,
                    :scheduled_fire_at,
                    :dedupe_key,
                    :status,
                    :output_summary,
                    :error,
                    :deep_link,
                    :model,
                    CAST(:token_usage AS jsonb),
                    :duration_ms,
                    :started_at,
                    :finished_at,
                    now(),
                    now()
                )
                ON CONFLICT (dedupe_key) DO UPDATE SET
                    updated_time = hasn_task.run_summary.updated_time
                RETURNING
                    run_uuid,
                    task_uuid,
                    owner_id,
                    agent_id,
                    executor_node_id,
                    session_id,
                    scheduled_fire_at,
                    dedupe_key,
                    status,
                    output_summary,
                    error,
                    deep_link,
                    model,
                    token_usage,
                    duration_ms,
                    started_at,
                    finished_at
                """
            ),
            {
                **summary,
                **payload_json,
            },
        )
        stored = dict(result.mappings().one())
        existing_event = await db.execute(
            sa.text(
                f"""
                SELECT event_id
                FROM {_SYNC_EVENTS}
                WHERE owner_id = :owner_id
                  AND event_type = 'task_run.summary_reported'
                  AND payload->>'dedupe_key' = :dedupe_key
                LIMIT 1
                """
            ),
            {'owner_id': owner_id, 'dedupe_key': stored['dedupe_key']},
        )
        if existing_event.mappings().first() is None:
            await self._append_sync_event(
                db,
                owner_id=owner_id,
                hasn_id=agent_hasn_id,
                event_type='task_run.summary_reported',
                aggregate_type='task_run',
                aggregate_id=stored['run_uuid'],
                payload=_task_run_summary_event_payload(stored),
            )
        return _task_run_summary_response_payload(stored)

    async def existing_client_event_revision(
        self, db: AsyncSession, *, owner_id: str, node_id: str, client_event_id: str
    ) -> int | None:
        result = await db.execute(
            sa.text(
                f"""
                SELECT server_revision
                FROM {_SYNC_INBOX_EVENTS}
                WHERE owner_id = :owner_id
                  AND node_id = :node_id
                  AND client_event_id = :client_event_id
                LIMIT 1
                """
            ),
            {
                'owner_id': owner_id,
                'node_id': node_id,
                'client_event_id': client_event_id,
            },
        )
        row = result.mappings().first()
        if row is None or row['server_revision'] is None:
            return None
        return int(row['server_revision'])

    async def emit_memory_event(
        self,
        db: AsyncSession,
        *,
        owner_id: str,
        event_type: str,
        namespace: str,
        aggregate_id: str,
        payload: dict[str, Any],
        sync_scope_kind: str = 'owner',
        sync_scope_id: str | None = None,
        hasn_id: str | None = None,
    ) -> tuple[int, str]:
        """服务端源发一条 memory.* 下行同步事件（区别于 client-originated 的 save_client_event）。

        供云端服务器**主动合成/写入**后向 daemon 下行的记忆事件使用（如 peer 画像合成）：
        推进命名空间权威 revision → 追加 hasn_sync_events（payload 注入 sync_scope_* / namespace /
        namespace_revision，供 daemon `pull_memory_events` 增量拉取与 `parse_*_payload` 校验）→
        回填该命名空间 last_event_id。返回 (server_revision, event_id)。

        payload 由调用方给记忆本体字段；本方法只补齐同步信封（sync_scope_kind/sync_scope_id/
        namespace/record_id/namespace_revision）。namespace 必须在允许集合内（owner→portraits/
        facts/… 等），否则 ValueError（防写坏下行游标）。
        """
        scope_id = sync_scope_id or owner_id
        if not _memory_namespace_allowed(sync_scope_kind, namespace):
            raise ValueError(f'memory namespace not allowed: {sync_scope_kind}/{namespace}')
        namespace_revision = await self._advance_memory_namespace_revision(
            db,
            sync_scope_kind=sync_scope_kind,
            sync_scope_id=scope_id,
            namespace=namespace,
        )
        merged_payload = {
            **payload,
            'owner_id': owner_id,
            'sync_scope_kind': sync_scope_kind,
            'sync_scope_id': scope_id,
            'namespace': namespace,
            'record_id': aggregate_id,
            'namespace_revision': namespace_revision,
        }
        server_revision, event_id, _deduped = await self._append_sync_event_with_id(
            db,
            owner_id=owner_id,
            hasn_id=hasn_id or owner_id,
            event_type=event_type,
            aggregate_type='memory',
            aggregate_id=aggregate_id,
            payload=merged_payload,
        )
        await self._set_memory_namespace_last_event(
            db,
            sync_scope_kind=sync_scope_kind,
            sync_scope_id=scope_id,
            namespace=namespace,
            event_id=event_id,
        )
        return server_revision, event_id

    async def _advance_memory_namespace_revision(
        self,
        db: AsyncSession,
        *,
        sync_scope_kind: str,
        sync_scope_id: str,
        namespace: str,
    ) -> int:
        result = await db.execute(
            sa.text(
                """
                INSERT INTO hasn_memory.namespace_revision (
                    sync_scope_kind,
                    sync_scope_id,
                    namespace,
                    revision,
                    updated_at,
                    created_time,
                    updated_time
                ) VALUES (
                    :sync_scope_kind,
                    :sync_scope_id,
                    :namespace,
                    1,
                    now(),
                    now(),
                    now()
                )
                ON CONFLICT (sync_scope_kind, sync_scope_id, namespace)
                DO UPDATE SET
                    revision = hasn_memory.namespace_revision.revision + 1,
                    updated_at = now(),
                    updated_time = now()
                RETURNING revision
                """
            ),
            {
                'sync_scope_kind': sync_scope_kind,
                'sync_scope_id': sync_scope_id,
                'namespace': namespace,
            },
        )
        return int(result.mappings().one()['revision'])

    async def _set_memory_namespace_last_event(
        self,
        db: AsyncSession,
        *,
        sync_scope_kind: str,
        sync_scope_id: str,
        namespace: str,
        event_id: str,
    ) -> None:
        await db.execute(
            sa.text(
                """
                UPDATE hasn_memory.namespace_revision
                SET last_event_id = :event_id,
                    updated_time = now()
                WHERE sync_scope_kind = :sync_scope_kind
                  AND sync_scope_id = :sync_scope_id
                  AND namespace = :namespace
                """
            ),
            {
                'sync_scope_kind': sync_scope_kind,
                'sync_scope_id': sync_scope_id,
                'namespace': namespace,
                'event_id': event_id,
            },
        )

    async def _append_sync_event(
        self,
        db: AsyncSession,
        *,
        owner_id: str,
        hasn_id: str,
        event_type: str,
        aggregate_type: str,
        aggregate_id: str,
        payload: dict[str, Any],
        producer: str | None = None,
        source_event_id: str | None = None,
        occurred_at: Any = None,
    ) -> int:
        revision, _event_id, _deduped = await self._append_sync_event_with_id(
            db,
            owner_id=owner_id,
            hasn_id=hasn_id,
            event_type=event_type,
            aggregate_type=aggregate_type,
            aggregate_id=aggregate_id,
            payload=payload,
            producer=producer,
            source_event_id=source_event_id,
            occurred_at=occurred_at,
        )
        return revision

    async def _append_sync_event_with_id(
        self,
        db: AsyncSession,
        *,
        owner_id: str,
        hasn_id: str,
        event_type: str,
        aggregate_type: str,
        aggregate_id: str,
        payload: dict[str, Any],
        producer: str | None = None,
        source_event_id: str | None = None,
        occurred_at: Any = None,
    ) -> tuple[int, str, bool]:
        """兼容旧 service 内部调用；实际写入委托唯一 SyncAppender。"""
        ref = await SqlAlchemySyncAppender().append(
            db,
            SyncEnvelope(
                owner_id=owner_id,
                hasn_id=hasn_id,
                event_type=event_type,
                aggregate_type=aggregate_type,
                aggregate_id=aggregate_id,
                payload=payload,
                producer=producer,
                source_event_id=source_event_id,
                occurred_at=occurred_at,
            ),
        )
        return ref.revision, ref.event_id, ref.deduped

    async def save_session(self, db: AsyncSession, session: dict[str, Any]) -> None:
        """保存或更新 session 到云端投影表"""
        await db.execute(
            sa.text(
                """
                INSERT INTO public.hasn_sessions (
                    id,
                    conversation_id,
                    session_kind,
                    session_scope,
                    session_status,
                    origin_type,
                    origin_ref,
                    parent_session_id,
                    fork_point_message_id,
                    summary_checkpoint_json,
                    last_message_id,
                    last_message_at,
                    message_count,
                    created_time,
                    updated_time
                ) VALUES (
                    :id,
                    :conversation_id,
                    :session_kind,
                    :session_scope,
                    :session_status,
                    :origin_type,
                    :origin_ref,
                    :parent_session_id,
                    :fork_point_message_id,
                    :summary_checkpoint_json,
                    :last_message_id,
                    :last_message_at,
                    :message_count,
                    now(),
                    now()
                )
                ON CONFLICT (id) DO UPDATE SET
                    session_status = EXCLUDED.session_status,
                    summary_checkpoint_json = EXCLUDED.summary_checkpoint_json,
                    last_message_id = EXCLUDED.last_message_id,
                    last_message_at = EXCLUDED.last_message_at,
                    message_count = EXCLUDED.message_count,
                    updated_time = now()
                """
            ),
            session,
        )
        # 只有 conversation_visible 和 summary_only 的 session 才发送同步事件
        if session.get('session_scope') in ('conversation_visible', 'summary_only'):
            await self._append_sync_event(
                db,
                owner_id=session.get('owner_id', ''),
                hasn_id=session.get('owner_id', ''),
                event_type='session.updated',
                aggregate_type='session',
                aggregate_id=session['id'],
                payload={
                    'session_id': session['id'],
                    'conversation_id': str(session.get('conversation_id')) if session.get('conversation_id') else None,
                    'session_kind': session.get('session_kind'),
                    'session_status': session.get('session_status'),
                },
            )

    async def save_session_event(self, db: AsyncSession, event: dict[str, Any]) -> None:
        """保存 session event 到云端投影表（仅 summary_only 和 conversation_visible）"""
        await db.execute(
            sa.text(
                """
                INSERT INTO public.hasn_session_events (
                    session_id,
                    event_type,
                    event_seq,
                    payload_json,
                    occurred_at,
                    created_time
                ) VALUES (
                    :session_id,
                    :event_type,
                    :event_seq,
                    :payload_json,
                    :occurred_at,
                    now()
                )
                """
            ),
            event,
        )

    async def save_session_artifact(self, db: AsyncSession, artifact: dict[str, Any]) -> None:
        """保存 session artifact 到云端投影表（按 sync_policy 决定）"""
        await db.execute(
            sa.text(
                """
                INSERT INTO public.hasn_session_artifacts (
                    session_id,
                    artifact_kind,
                    artifact_name,
                    artifact_path,
                    summary_json,
                    sync_policy,
                    created_time
                ) VALUES (
                    :session_id,
                    :artifact_kind,
                    :artifact_name,
                    :artifact_path,
                    :summary_json,
                    :sync_policy,
                    now()
                )
                """
            ),
            artifact,
        )


@dataclass(slots=True)
class HasnSyncService:
    gateway: SyncGateway = field(default_factory=SqlAlchemySyncGateway)

    async def pull(self, db: AsyncSession, request: SyncPullRequest, *, user_id: int | None = None) -> SyncPullResponse:
        await self._assert_owner_access(db, owner_id=request.owner_id, user_id=user_id)
        after_revision = _parse_owner_cursor(request.cursor)
        events = await self.gateway.pull_events(
            db,
            owner_id=request.owner_id,
            after_revision=after_revision,
            limit=request.limit + 1,
        )
        limited = events[: request.limit]
        has_more = len(events) > request.limit
        next_revision = limited[-1].revision if limited else after_revision
        return SyncPullResponse(
            events=limited,
            next_cursor=_owner_cursor(request.owner_id, next_revision),
            has_more=has_more,
        )

    async def push(self, db: AsyncSession, request: SyncPushRequest, *, user_id: int | None = None) -> SyncPushResponse:
        await self._assert_owner_access(db, owner_id=request.owner_id, user_id=user_id)
        rejected: list[ErrorObject] = []
        node_id = request.node_id or 'unknown'
        max_server_revision = 0
        for event in request.events:
            if _contains_private_runtime_key(event.payload):
                rejected.append(_PRIVATE_METADATA_ERROR)
                continue

            # 处理 session 相关事件
            if event.event_type == 'session.sync':
                save_session = getattr(self.gateway, 'save_session', None)
                if save_session and event.payload:
                    session_data = dict(event.payload)
                    session_data['owner_id'] = request.owner_id
                    await save_session(db, session_data)
                continue
            if event.event_type == 'session_event.sync':
                save_session_event = getattr(self.gateway, 'save_session_event', None)
                if save_session_event and event.payload:
                    await save_session_event(db, event.payload)
                continue
            if event.event_type == 'session_artifact.sync':
                save_session_artifact = getattr(self.gateway, 'save_session_artifact', None)
                if save_session_artifact and event.payload:
                    await save_session_artifact(db, event.payload)
                continue
            if event.event_type.startswith('memory.'):
                try:
                    sync_scope_kind, _, namespace = _memory_namespace_revision_key(event)
                    if not _memory_namespace_allowed(sync_scope_kind, namespace):
                        raise errors.RequestError(
                            msg=_MEMORY_NAMESPACE_UNKNOWN_ERROR.name,
                            data=_MEMORY_NAMESPACE_UNKNOWN_ERROR.model_dump(),
                        )
                except errors.RequestError as exc:
                    if getattr(exc, 'msg', None) == _MEMORY_SYNC_SCOPE_ERROR.name:
                        rejected.append(_MEMORY_SYNC_SCOPE_ERROR)
                        continue
                    if getattr(exc, 'msg', None) == _MEMORY_NAMESPACE_UNKNOWN_ERROR.name:
                        rejected.append(_MEMORY_NAMESPACE_UNKNOWN_ERROR)
                        continue
                    raise
            save_client_event = getattr(self.gateway, 'save_client_event', None)
            if save_client_event:
                try:
                    server_revision = await save_client_event(
                        db, owner_id=request.owner_id, node_id=node_id, event=event
                    )
                except errors.RequestError as exc:
                    if event.event_type.startswith('memory.'):
                        if getattr(exc, 'msg', None) == _MEMORY_SYNC_SCOPE_ERROR.name:
                            rejected.append(_MEMORY_SYNC_SCOPE_ERROR)
                            continue
                        if getattr(exc, 'msg', None) == _MEMORY_NAMESPACE_UNKNOWN_ERROR.name:
                            rejected.append(_MEMORY_NAMESPACE_UNKNOWN_ERROR)
                            continue
                    raise
                if server_revision is not None:
                    max_server_revision = max(max_server_revision, int(server_revision))
        accepted = len(request.events) - len(rejected)
        return SyncPushResponse(
            accepted=accepted,
            rejected=rejected,
            next_cursor=_owner_cursor(request.owner_id, max_server_revision),
        )

    async def pull_tasks(
        self, db: AsyncSession, request: SyncPullRequest, *, user_id: int | None = None
    ) -> SyncPullResponse:
        await self._assert_owner_access(db, owner_id=request.owner_id, user_id=user_id)
        after_revision = _parse_task_cursor(request.cursor)
        events = await self.gateway.pull_task_events(
            db,
            owner_id=request.owner_id,
            node_id=request.node_id,
            after_revision=after_revision,
            limit=request.limit + 1,
        )
        limited = events[: request.limit]
        has_more = len(events) > request.limit
        next_revision = limited[-1].revision if limited else after_revision
        return SyncPullResponse(
            events=limited,
            next_cursor=_task_cursor(request.owner_id, next_revision),
            has_more=has_more,
        )

    async def push_tasks(
        self, db: AsyncSession, request: SyncPushRequest, *, user_id: int | None = None
    ) -> SyncPushResponse:
        await self._assert_owner_access(db, owner_id=request.owner_id, user_id=user_id)
        rejected: list[ErrorObject] = []
        node_id = request.node_id or 'unknown'
        max_server_revision = 0
        for event in request.events:
            if event.event_type not in TASK_SYNC_EVENT_TYPES:
                rejected.append(_TASK_EVENT_UNSUPPORTED_ERROR)
                continue
            if _contains_private_runtime_key(event.payload):
                rejected.append(_PRIVATE_METADATA_ERROR)
                continue
            try:
                server_revision = await self.gateway.save_task_event(
                    db,
                    owner_id=request.owner_id,
                    node_id=node_id,
                    event=event,
                )
            except TaskSyncConflictError:
                rejected.append(_TASK_SYNC_CONFLICT_ERROR)
                continue
            if server_revision is not None:
                max_server_revision = max(max_server_revision, int(server_revision))
        accepted = len(request.events) - len(rejected)
        # LF-P3：本设备推上来的任务变更被接受后，向该 owner 其它在线节点 push
        # hasn.sync.invalidate{kind:tasks}，让它们秒级对账任务镜像（跨设备即时刷新）。
        if accepted > 0:
            from backend.app.hasn.service.sync_invalidate_service import KIND_TASKS, bump_owner

            await bump_owner(KIND_TASKS, db, owner_id=request.owner_id)
        return SyncPushResponse(
            accepted=accepted,
            rejected=rejected,
            next_cursor=_task_cursor(request.owner_id, max_server_revision),
        )

    async def report_task_run_summary(
        self,
        db: AsyncSession,
        request: TaskRunSummaryRequest,
        *,
        agent: AgentTokenPayload,
    ) -> TaskRunSummaryResponse:
        owner_id = request.owner_id or agent.owner_hasn_id
        if owner_id != agent.owner_hasn_id:
            raise errors.ForbiddenError(msg='agent cannot report another owner task run')
        if request.agent_id and request.agent_id != agent.agent_hasn_id:
            raise errors.ForbiddenError(msg='agent cannot report another agent task run')

        summary = _task_run_summary_for_storage(request, owner_id=owner_id, agent_hasn_id=agent.agent_hasn_id)
        try:
            stored = await self.gateway.save_task_run_summary(
                db,
                owner_id=owner_id,
                agent_hasn_id=agent.agent_hasn_id,
                summary=summary,
            )
        except PermissionError as exc:
            raise errors.ForbiddenError(msg=str(exc)) from exc
        return TaskRunSummaryResponse(**_task_run_summary_response_payload(stored))

    async def pull_memory(
        self, db: AsyncSession, request: MemorySyncPullRequest, *, user_id: int | None = None
    ) -> MemorySyncPullResponse:
        await self._assert_owner_access(db, owner_id=request.owner_id, user_id=user_id)
        selections = _memory_pull_selections(request)
        events = await self.gateway.pull_memory_events(
            db,
            owner_id=request.owner_id,
            selections=selections,
            limit=request.max_events + 1,
        )
        limited = events[: request.max_events]
        has_more = len(events) > request.max_events
        next_cursors = _advance_memory_cursors(selections, limited)
        return MemorySyncPullResponse(events=limited, next_cursors=next_cursors, has_more=has_more)

    async def _assert_owner_access(self, db: AsyncSession, *, owner_id: str, user_id: int | None) -> None:
        if user_id is None:
            return
        owns_owner = getattr(self.gateway, 'owns_owner', None)
        if owns_owner is None:
            raise errors.AuthorizationError(msg='ERR_HASN_OWNER_ACCESS_DENIED')
        if not await owns_owner(db, owner_id=owner_id, user_id=user_id):
            raise errors.AuthorizationError(msg='ERR_HASN_OWNER_ACCESS_DENIED')


hasn_sync_service = HasnSyncService()
