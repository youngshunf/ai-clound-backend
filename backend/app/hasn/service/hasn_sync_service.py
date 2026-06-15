"""P0 HASN sync/runtime report service.

The service owns the hand-written hasn-node API boundary. Generated CRUD remains
available for admin inspection, but hasn-node uses these redacted, owner-scoped
methods instead of generic table CRUD.
"""
from __future__ import annotations

import json
import uuid
from dataclasses import dataclass, field
from datetime import datetime
from datetime import timezone as datetime_timezone
from typing import Any, Protocol

import sqlalchemy as sa
from sqlalchemy.ext.asyncio import AsyncSession

from backend.app.hasn.model import HasnHumans
from backend.app.hasn.schema.hasn_message_hub import ErrorObject
from backend.app.hasn.schema.hasn_sync import (
    ClientEvent,
    MemorySyncCursor,
    MemorySyncPullRequest,
    MemorySyncPullResponse,
    RuntimeReportRequest,
    RuntimeReportResponse,
    RuntimeSummary,
    SyncEventRecord,
    SyncPullRequest,
    SyncPullResponse,
    SyncPushRequest,
    SyncPushResponse,
    TaskRunSummaryRequest,
    TaskRunSummaryResponse,
)
from backend.common.dataclasses import AgentTokenPayload
from backend.common.exception import errors
from backend.utils.timezone import timezone

MEMORY_SYNC_SCOPE_KINDS = {'owner', 'agent'}

OWNER_MEMORY_NAMESPACES = {'portraits', 'facts', 'events', 'procedures', 'work_state', 'summaries', 'audits'}
AGENT_MEMORY_NAMESPACES = {
    'episodic',
    'agent_portraits',
    'agent_facts',
    'agent_events',
    'agent_procedures',
    'tasks',
    'extract_jobs',
}

PRIVATE_RUNTIME_KEYS = {
    'workspace',
    'workspace_path',
    'endpoint',
    'local_endpoint',
    'pid',
    'process_id',
    'cli_args',
    'oauth_path',
    'session_cache',
    'token',
    'access_token',
    'refresh_token',
    'raw_binding_metadata',
}

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

_MEMORY_NAMESPACE_UNKNOWN_ERROR = ErrorObject(
    code=8036,
    name='ERR_MEMORY_NAMESPACE_UNKNOWN',
    message='Memory sync payload references unknown namespace.',
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

# 会话消息事件：客户端推上来后必须落入权威 feed（hasn_sync_events），供换设备 sync/pull
# 还原完整会话历史。owner↔自己分身的对话本地短路执行、不经 route_message，唯一上云路径
# 就是这里（见 docs/hasn-node设计文档/02-数据与同步/06-owner与分身会话跨设备同步修复设计.md）。
# h↔h / 跨 owner 消息由 message_router.route_message 直接写 feed，daemon 侧不重复镜像（去重边界）。
FEED_MESSAGE_EVENT_TYPES = {'message.sent', 'message.received', 'message.agent_reply'}


class TaskSyncConflictError(Exception):
    """Raised when optimistic task revision conflict detection rejects an event."""


class SyncGateway(Protocol):
    async def save_runtime_report(self, db: AsyncSession, report: dict[str, Any]) -> None: ...
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

    async def save_runtime_report(self, db: AsyncSession, report: dict[str, Any]) -> None:
        summary_json = json.dumps(report['summary_json'], ensure_ascii=False, sort_keys=True, default=str)
        await db.execute(
            sa.text(
                '''
                INSERT INTO public.hasn_agent_runtime_reports (
                    report_id,
                    owner_id,
                    agent_hasn_id,
                    node_id,
                    runtime_type,
                    runtime_status,
                    adapter_registered,
                    handle_available,
                    binding_id,
                    runtime_revision,
                    summary_json,
                    last_seen_at,
                    reported_at,
                    created_time,
                    updated_time
                ) VALUES (
                    :report_id,
                    :owner_id,
                    :agent_hasn_id,
                    :node_id,
                    :runtime_type,
                    :runtime_status,
                    :adapter_registered,
                    :handle_available,
                    :binding_id,
                    :runtime_revision,
                    CAST(:summary_json AS jsonb),
                    :last_seen_at,
                    :reported_at,
                    now(),
                    now()
                )
                ON CONFLICT (report_id) DO UPDATE SET
                    runtime_status = EXCLUDED.runtime_status,
                    adapter_registered = EXCLUDED.adapter_registered,
                    handle_available = EXCLUDED.handle_available,
                    binding_id = EXCLUDED.binding_id,
                    runtime_revision = EXCLUDED.runtime_revision,
                    summary_json = EXCLUDED.summary_json,
                    last_seen_at = EXCLUDED.last_seen_at,
                    reported_at = EXCLUDED.reported_at,
                    updated_time = now()
                '''
            ),
            {**report, 'summary_json': summary_json},
        )
        await self._append_sync_event(
            db,
            owner_id=report['owner_id'],
            hasn_id=report['agent_hasn_id'],
            event_type='runtime.reported',
            aggregate_type='runtime',
            aggregate_id=report['agent_hasn_id'],
            payload={
                'agent_id': report['agent_hasn_id'],
                'node_id': report['node_id'],
                'runtime_type': report['runtime_type'],
                'runtime_status': report['runtime_status'],
                'binding_id': report['binding_id'],
            },
        )
        await self._refresh_task_assignments_for_runtime_report(db, report)

    async def pull_events(
        self, db: AsyncSession, *, owner_id: str, after_revision: int, limit: int
    ) -> list[SyncEventRecord]:
        result = await db.execute(
            sa.text(
                '''
                SELECT event_id, event_type, revision, occurred_at, payload
                FROM public.hasn_sync_events
                WHERE owner_id = :owner_id
                  AND event_type NOT LIKE 'task.%'
                  AND event_type <> 'task_run.summary_reported'
                  AND revision > :after_revision
                ORDER BY revision ASC
                LIMIT :limit
                '''
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
                '''
                SELECT event_id, event_type, revision, occurred_at, payload
                FROM public.hasn_sync_events e
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
                    OR (
                      jsonb_typeof(e.payload->'visible_node_ids') = 'array'
                      AND jsonb_exists(e.payload->'visible_node_ids', :node_id)
                    )
                    OR (
                      NOT (e.payload ? 'visible_node_ids')
                      AND EXISTS (
                        SELECT 1
                        FROM hasn_task.assignment a
                        WHERE a.owner_id = e.owner_id
                          AND a.task_uuid = COALESCE(e.payload->>'task_uuid', e.payload->>'task_id', e.aggregate_id)
                          AND a.assignment_state = 'assigned'
                          AND a.executor_node_id = :node_id
                      )
                    )
                    OR (
                      NOT (e.payload ? 'visible_node_ids')
                      AND NOT EXISTS (
                        SELECT 1
                        FROM hasn_task.assignment a
                        WHERE a.owner_id = e.owner_id
                          AND a.task_uuid = COALESCE(e.payload->>'task_uuid', e.payload->>'task_id', e.aggregate_id)
                          AND a.assignment_state = 'assigned'
                      )
                      AND (
                        COALESCE(e.payload->>'node_id', '') = :node_id
                        OR COALESCE(e.payload->>'executor_node_id', '') = :node_id
                        OR COALESCE(e.payload->>'node_id', e.payload->>'executor_node_id') IS NULL
                      )
                    )
                  )
                ORDER BY revision ASC
                LIMIT :limit
                '''
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
                '''
                WITH requested(sync_scope_kind, sync_scope_id, namespace, last_pulled_revision) AS (
                    SELECT *
                    FROM jsonb_to_recordset(CAST(:selections AS jsonb))
                    AS x(sync_scope_kind text, sync_scope_id text, namespace text, last_pulled_revision bigint)
                )
                SELECT event_id, event_type, revision, occurred_at, payload
                FROM public.hasn_sync_events e
                JOIN requested r
                  ON e.payload->>'sync_scope_kind' = r.sync_scope_kind
                 AND e.payload->>'sync_scope_id' = r.sync_scope_id
                 AND e.payload->>'namespace' = r.namespace
                WHERE e.owner_id = :owner_id
                  AND e.event_type LIKE 'memory.%'
                  AND COALESCE((e.payload->>'namespace_revision')::bigint, 0) > r.last_pulled_revision
                ORDER BY e.revision ASC
                LIMIT :limit
                '''
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
        self, db: AsyncSession, *, owner_id: str, node_id: str, event: ClientEvent
    ) -> int | None:
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
            server_revision, event_id = await self._append_sync_event_with_id(
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
        elif event.event_type in FEED_MESSAGE_EVENT_TYPES:
            # owner↔自己分身的会话消息（主人提问 message.sent / 分身回复 message.agent_reply）
            # 本地短路执行、不经 route_message，必须在此落入权威 feed，否则换设备 sync/pull
            # 永远拉不回（历史 bug：这里只写 inbox 死信表）。message.received 一并支持。
            server_revision = await self._append_message_feed_event_idempotent(
                db, owner_id=owner_id, event=event
            )
        await db.execute(
            sa.text(
                '''
                INSERT INTO public.hasn_sync_inbox_events (
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
                '''
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

    async def _append_message_feed_event_idempotent(
        self, db: AsyncSession, *, owner_id: str, event: ClientEvent
    ) -> int | None:
        '''将会话消息事件幂等地追加到权威 feed（hasn_sync_events）。

        幂等键 = (owner_id, aggregate_id, event_type)，其中 aggregate_id 取 payload.message_id
        （回退 dedupe_key）。同一条镜像消息重复 push 时返回既有 revision、不重复追加。
        h↔h / 跨 owner 消息由 route_message 用云端数字 message_id 写 feed，与本地 ULID
        天然不撞键；daemon 侧也不镜像它们，双重保证不双写。
        '''
        message_id = event.payload.get('message_id') or event.dedupe_key
        if not message_id:
            raise errors.RequestError(msg='ERR_MESSAGE_ID_REQUIRED')
        existing = await db.execute(
            sa.text(
                '''
                SELECT revision
                FROM public.hasn_sync_events
                WHERE owner_id = :owner_id
                  AND aggregate_type = 'message'
                  AND aggregate_id = :aggregate_id
                  AND event_type = :event_type
                LIMIT 1
                '''
            ),
            {
                'owner_id': owner_id,
                'aggregate_id': str(message_id),
                'event_type': event.event_type,
            },
        )
        existing_row = existing.mappings().first()
        if existing_row is not None:
            return int(existing_row['revision'])
        return await self._append_sync_event(
            db,
            owner_id=owner_id,
            hasn_id=event.hasn_id or owner_id,
            event_type=event.event_type,
            aggregate_type='message',
            aggregate_id=str(message_id),
            payload={**event.payload, 'client_event_id': event.client_event_id},
        )

    async def save_task_event(self, db: AsyncSession, *, owner_id: str, node_id: str, event: ClientEvent) -> int | None:
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
        )
        return revision

    async def _refresh_task_assignments_for_runtime_report(self, db: AsyncSession, report: dict[str, Any]) -> None:
        assignment = _assignment_from_runtime_report(report)
        task_rows = await self._task_rows_for_assignment_refresh(
            db,
            owner_id=report['owner_id'],
            agent_id=report['agent_hasn_id'],
        )
        for task in task_rows:
            task_uuid = str(task.get('task_uuid') or '')
            if not task_uuid:
                continue
            previous = await self._current_assignment(db, owner_id=report['owner_id'], task_uuid=task_uuid)
            old_node_id = (previous or {}).get('executor_node_id') or ''
            changed = (
                previous is None
                or previous.get('executor_kind') != assignment['executor_kind']
                or previous.get('executor_node_id') != assignment['executor_node_id']
                or previous.get('binding_id') != assignment['binding_id']
                or previous.get('assignment_state') != assignment['assignment_state']
            )
            if not changed:
                continue
            await self._upsert_current_assignment(
                db,
                task_uuid=task_uuid,
                owner_id=report['owner_id'],
                agent_id=report['agent_hasn_id'],
                assignment=assignment,
            )
            await self._append_assignment_change_events(
                db,
                task=task,
                assignment=assignment,
                old_node_id=old_node_id,
            )

    async def _task_rows_for_assignment_refresh(
        self,
        db: AsyncSession,
        *,
        owner_id: str,
        agent_id: str,
    ) -> list[dict[str, Any]]:
        result = await db.execute(
            sa.text(
                '''
                SELECT
                    task_uuid,
                    owner_id,
                    agent_id,
                    name,
                    description,
                    prompt,
                    system_prompt,
                    skill_bundle_ids,
                    skill_bundle_refs,
                    skill_ids,
                    schedule_type,
                    schedule_config,
                    schedule_display,
                    enabled,
                    state,
                    continuation_enabled,
                    enable_subagents,
                    created_by_kind,
                    next_run_at,
                    run_count,
                    repeat_times,
                    repeat_completed,
                    created_time,
                    updated_time
                FROM hasn_task.task
                WHERE owner_id = :owner_id
                  AND agent_id = :agent_id
                  AND task_uuid IS NOT NULL
                  AND state <> 'deleted'
                '''
            ),
            {'owner_id': owner_id, 'agent_id': agent_id},
        )
        return [dict(row) for row in result.mappings().all()]

    async def _current_assignment(
        self,
        db: AsyncSession,
        *,
        owner_id: str,
        task_uuid: str,
    ) -> dict[str, Any] | None:
        result = await db.execute(
            sa.text(
                '''
                SELECT executor_kind, executor_node_id, binding_id, assignment_state
                FROM hasn_task.assignment
                WHERE owner_id = :owner_id
                  AND task_uuid = :task_uuid
                ORDER BY updated_time DESC NULLS LAST, id DESC
                LIMIT 1
                '''
            ),
            {'owner_id': owner_id, 'task_uuid': task_uuid},
        )
        row = result.mappings().first()
        return dict(row) if row is not None else None

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
                '''
                DELETE FROM hasn_task.assignment
                WHERE owner_id = :owner_id
                  AND task_uuid = :task_uuid
                '''
            ),
            {'owner_id': owner_id, 'task_uuid': task_uuid},
        )
        await db.execute(
            sa.text(
                '''
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
                '''
            ),
            {
                'task_uuid': task_uuid,
                'owner_id': owner_id,
                'agent_id': agent_id,
                **assignment,
            },
        )

    async def _append_assignment_change_events(
        self,
        db: AsyncSession,
        *,
        task: dict[str, Any],
        assignment: dict[str, Any],
        old_node_id: str,
    ) -> None:
        new_node_id = assignment['executor_node_id']
        assignment_payload = _task_assignment_event_payload(task, assignment, old_node_id)
        await self._append_sync_event(
            db,
            owner_id=task['owner_id'],
            hasn_id=task['agent_id'],
            event_type='task.assignment_updated',
            aggregate_type='task',
            aggregate_id=task['task_uuid'],
            payload=assignment_payload,
        )
        if old_node_id and old_node_id != new_node_id:
            await self._append_sync_event(
                db,
                owner_id=task['owner_id'],
                hasn_id=task['agent_id'],
                event_type='task.updated',
                aggregate_type='task',
                aggregate_id=task['task_uuid'],
                payload={
                    **_task_sync_payload_from_row(task),
                    'state': 'waiting_for_runtime',
                    'executor_policy': assignment['executor_kind'],
                    'executor_node_id': new_node_id,
                    'assignment_state': assignment['assignment_state'],
                    'visible_node_ids': [old_node_id],
                },
            )
        if new_node_id:
            await self._append_sync_event(
                db,
                owner_id=task['owner_id'],
                hasn_id=task['agent_id'],
                event_type='task.updated',
                aggregate_type='task',
                aggregate_id=task['task_uuid'],
                payload={
                    **_task_sync_payload_from_row(task),
                    'executor_policy': assignment['executor_kind'],
                    'executor_node_id': new_node_id,
                    'assignment_state': assignment['assignment_state'],
                    'visible_node_ids': [new_node_id],
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
                '''
                SELECT task_revision, state
                FROM hasn_task.task
                WHERE owner_id = :owner_id
                  AND task_uuid = :task_uuid
                LIMIT 1
                '''
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
        await db.execute(
            sa.text(
                '''
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
                    updated_time = EXCLUDED.updated_time
                '''
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
            },
        )
        await self._upsert_current_assignment(
            db,
            task_uuid=task_uuid,
            owner_id=owner_id,
            agent_id=stored_task['agent_id'],
            assignment={
                'executor_kind': stored_task['executor_policy'],
                'executor_node_id': stored_task['executor_node_id'] or node_id,
                'binding_id': stored_task.get('binding_id'),
                'assignment_state': 'unresolved' if event_payload.get('state') == 'deleted' else 'assigned',
                'resolved_at': stored_task['updated_time'],
            },
        )
        revision, event_id = await self._append_sync_event_with_id(
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
        await db.execute(
            sa.text(
                '''
                INSERT INTO public.hasn_sync_inbox_events (
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
                '''
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
                '''
                SELECT owner_id, agent_id
                FROM hasn_task.task
                WHERE task_uuid = :task_uuid
                LIMIT 1
                '''
            ),
            {'task_uuid': task_uuid},
        )
        task_row = result.mappings().first()
        if task_row is not None and (
            task_row['owner_id'] != owner_id
            or task_row['agent_id'] != agent_hasn_id
        ):
            raise errors.ForbiddenError(msg='agent cannot report this task run')

        payload_json = {
            'token_usage': json.dumps(summary.get('token_usage'), ensure_ascii=False, sort_keys=True, default=str)
            if summary.get('token_usage') is not None
            else None
        }
        result = await db.execute(
            sa.text(
                '''
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
                '''
            ),
            {
                **summary,
                **payload_json,
            },
        )
        stored = dict(result.mappings().one())
        existing_event = await db.execute(
            sa.text(
                '''
                SELECT event_id
                FROM public.hasn_sync_events
                WHERE owner_id = :owner_id
                  AND event_type = 'task_run.summary_reported'
                  AND payload->>'dedupe_key' = :dedupe_key
                LIMIT 1
                '''
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
                '''
                SELECT server_revision
                FROM public.hasn_sync_inbox_events
                WHERE owner_id = :owner_id
                  AND node_id = :node_id
                  AND client_event_id = :client_event_id
                LIMIT 1
                '''
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
                '''
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
                '''
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
                '''
                UPDATE hasn_memory.namespace_revision
                SET last_event_id = :event_id,
                    updated_time = now()
                WHERE sync_scope_kind = :sync_scope_kind
                  AND sync_scope_id = :sync_scope_id
                  AND namespace = :namespace
                '''
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
    ) -> int:
        revision, _event_id = await self._append_sync_event_with_id(
            db,
            owner_id=owner_id,
            hasn_id=hasn_id,
            event_type=event_type,
            aggregate_type=aggregate_type,
            aggregate_id=aggregate_id,
            payload=payload,
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
    ) -> tuple[int, str]:
        # 串行化同一 owner 的 revision 分配。hasn_sync_events 的
        # uq_hasn_sync_events_owner_revision (owner_id, revision) 唯一约束要求每个 owner 的
        # revision 连续无冲突，但 "SELECT MAX(revision)+1" 看不到其它事务尚未提交的行：两个
        # 并发写（如同一 owner 的两个 memory 抽取任务 push）会都读到 N、都算出 N+1、都
        # INSERT，触发 UniqueViolationError。用事务级 advisory lock 按 owner 分桶把 revision
        # 分配串起来——后到的事务阻塞到前一个提交后再读 MAX，拿到正确的下一个值；锁在事务
        # 结束（commit/rollback）时自动释放。这是 Postgres 生成 gapless 序列的标准做法，无需
        # 新表 / 迁移播种，MAX 推导与现存数据天然一致。hashtext 哈希分桶极少量跨 owner 串行
        # 亦无害。所有写入方都经 _append_sync_event(_with_id)，此处加锁即覆盖全部 callsite。
        await db.execute(
            sa.text('SELECT pg_advisory_xact_lock(hashtext(CAST(:owner_id AS text)))'),
            {'owner_id': owner_id},
        )
        revision_result = await db.execute(
            sa.text(
                '''
                SELECT COALESCE(MAX(revision), 0) + 1 AS revision
                FROM public.hasn_sync_events
                WHERE owner_id = :owner_id
                '''
            ),
            {'owner_id': owner_id},
        )
        revision = int(revision_result.mappings().one()['revision'])
        event_id = f'se_{uuid.uuid4().hex[:24]}'
        await db.execute(
            sa.text(
                '''
                INSERT INTO public.hasn_sync_events (
                    event_id,
                    owner_id,
                    hasn_id,
                    event_type,
                    aggregate_type,
                    aggregate_id,
                    payload,
                    revision,
                    occurred_at,
                    created_time,
                    updated_time
                ) VALUES (
                    :event_id,
                    :owner_id,
                    :hasn_id,
                    :event_type,
                    :aggregate_type,
                    :aggregate_id,
                    CAST(:payload AS jsonb),
                    :revision,
                    now(),
                    now(),
                    now()
                )
                '''
            ),
            {
                'event_id': event_id,
                'owner_id': owner_id,
                'hasn_id': hasn_id,
                'event_type': event_type,
                'aggregate_type': aggregate_type,
                'aggregate_id': aggregate_id,
                'payload': json.dumps(payload, ensure_ascii=False, sort_keys=True, default=str),
                'revision': revision,
            },
        )
        return revision, event_id

    async def save_session(self, db: AsyncSession, session: dict[str, Any]) -> None:
        """保存或更新 session 到云端投影表"""
        await db.execute(
            sa.text(
                '''
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
                '''
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
                '''
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
                '''
            ),
            event,
        )

    async def save_session_artifact(self, db: AsyncSession, artifact: dict[str, Any]) -> None:
        """保存 session artifact 到云端投影表（按 sync_policy 决定）"""
        await db.execute(
            sa.text(
                '''
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
                '''
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
            elif event.event_type == 'session_event.sync':
                save_session_event = getattr(self.gateway, 'save_session_event', None)
                if save_session_event and event.payload:
                    await save_session_event(db, event.payload)
                continue
            elif event.event_type == 'session_artifact.sync':
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

    async def report_runtime(
        self, db: AsyncSession, request: RuntimeReportRequest, *, user_id: int | None = None
    ) -> RuntimeReportResponse:
        await self._assert_owner_access(db, owner_id=request.owner_id, user_id=user_id)
        for summary in request.runtime_summaries:
            if _contains_private_runtime_key(summary.summary_json):
                raise errors.RequestError(msg=_PRIVATE_METADATA_ERROR.name, data=_PRIVATE_METADATA_ERROR.model_dump())

        for summary in request.runtime_summaries:
            await self.gateway.save_runtime_report(
                db,
                {
                    'report_id': _report_id(request.owner_id, request.node_id, summary),
                    'owner_id': request.owner_id,
                    'agent_hasn_id': summary.agent_id,
                    'node_id': request.node_id,
                    'runtime_type': summary.runtime_type,
                    'runtime_status': _runtime_status_for_storage(summary.status),
                    'adapter_registered': summary.adapter_registered,
                    'handle_available': summary.handle_available,
                    'binding_id': summary.binding_id,
                    'runtime_revision': summary.runtime_revision,
                    'summary_json': _redact_runtime_summary(summary.summary_json),
                    'last_seen_at': summary.last_seen_at,
                    'reported_at': timezone.now(),
                },
            )
        return RuntimeReportResponse(
            accepted=len(request.runtime_summaries),
            rejected=[],
            next_cursor=_owner_cursor(request.owner_id, 0),
        )

    async def _assert_owner_access(self, db: AsyncSession, *, owner_id: str, user_id: int | None) -> None:
        if user_id is None:
            return
        owns_owner = getattr(self.gateway, 'owns_owner', None)
        if owns_owner is None:
            raise errors.AuthorizationError(msg='ERR_HASN_OWNER_ACCESS_DENIED')
        if not await owns_owner(db, owner_id=owner_id, user_id=user_id):
            raise errors.AuthorizationError(msg='ERR_HASN_OWNER_ACCESS_DENIED')


def _parse_owner_cursor(cursor: str | None) -> int:
    if not cursor:
        return 0
    parts = str(cursor).rsplit(':', maxsplit=1)
    try:
        return max(int(parts[-1]), 0)
    except (TypeError, ValueError) as exc:
        raise errors.RequestError(msg='ERR_SYNC_CURSOR_INVALID') from exc


def _owner_cursor(owner_id: str, revision: int) -> str:
    return f'owner:{owner_id}:{max(int(revision), 0)}'


def _parse_task_cursor(cursor: str | None) -> int:
    if not cursor:
        return 0
    parts = str(cursor).rsplit(':', maxsplit=1)
    try:
        return max(int(parts[-1]), 0)
    except (TypeError, ValueError) as exc:
        raise errors.RequestError(msg='ERR_TASK_SYNC_CURSOR_INVALID') from exc


def _task_cursor(owner_id: str, revision: int) -> str:
    return f'owner:{owner_id}:task:{max(int(revision), 0)}'


def _task_payload_for_storage(owner_id: str, event: ClientEvent) -> dict[str, Any]:
    task_payload = event.payload.get('task')
    payload = dict(task_payload) if isinstance(task_payload, dict) else dict(event.payload)
    payload['owner_id'] = owner_id
    return payload


def _assert_task_revision_not_stale(
    event: ClientEvent,
    payload: dict[str, Any],
    current_task: dict[str, Any] | None,
) -> None:
    if current_task is None:
        return
    if event.event_type != 'task.deleted' and current_task.get('state') == 'deleted':
        raise TaskSyncConflictError
    base_revision = _optional_int(payload.get('base_revision'))
    if base_revision is None:
        return
    current_revision = _optional_int(current_task.get('task_revision')) or 0
    if base_revision < current_revision:
        raise TaskSyncConflictError


def _task_storage_row(
    owner_id: str,
    task_uuid: str,
    payload: dict[str, Any],
    event: ClientEvent,
    now: datetime,
) -> dict[str, Any]:
    timestamp = _coerce_datetime_or_none(payload.get('updated_at')) or now
    created_time = _coerce_datetime_or_none(payload.get('created_at')) or timestamp
    state = str(payload.get('state') or 'scheduled')
    if event.event_type == 'task.deleted':
        state = 'deleted'
    executor_node_id = _optional_string(payload.get('executor_node_id') or payload.get('node_id'))
    executor_policy = _optional_string(payload.get('executor_policy') or payload.get('executor_kind')) or 'local_node'
    return {
        'owner_id': owner_id,
        'agent_id': str(payload.get('agent_id') or event.hasn_id or ''),
        'name': str(payload.get('name') or ''),
        'description': _optional_string(payload.get('description')),
        'prompt': str(payload.get('prompt') or ''),
        'system_prompt': _optional_string(payload.get('system_prompt')),
        'input_template': _optional_string(payload.get('input_template')),
        'skill_bundle_ids': _coerce_list(payload.get('skill_bundle_ids')),
        'skill_bundle_refs': _coerce_list(payload.get('skill_bundle_refs')),
        'skill_ids': _coerce_list(payload.get('skill_ids')),
        'skill_refs': _coerce_list(payload.get('skill_refs')),
        'workflow_id': _optional_int(payload.get('workflow_id')),
        'workflow': _coerce_dict(payload.get('workflow')),
        'enabled_toolsets': _coerce_list_or_none(payload.get('enabled_toolsets')),
        'context_from_task_id': _optional_int(payload.get('context_from_task_id')),
        'schedule_type': str(payload.get('schedule_type') or 'once'),
        'schedule_config': _coerce_dict(payload.get('schedule_config')),
        'schedule_display': _optional_string(payload.get('schedule_display')),
        'timezone': str(payload.get('timezone') or 'Asia/Shanghai'),
        'misfire_policy': str(payload.get('misfire_policy') or 'skip'),
        'catchup_limit': _optional_int(payload.get('catchup_limit')),
        'enabled': bool(payload.get('enabled', True)),
        'state': state,
        'next_run_at': _coerce_datetime_or_none(payload.get('next_run_at')),
        'run_count': _optional_int(payload.get('run_count')) or 0,
        'repeat_times': _optional_int(payload.get('repeat_times')),
        'repeat_completed': _optional_int(payload.get('repeat_completed')) or 0,
        'task_uuid': task_uuid,
        'executor_policy': executor_policy,
        'executor_node_id': executor_node_id,
        'binding_id': _optional_string(payload.get('binding_id')),
        'deleted_at': _coerce_datetime_or_none(payload.get('deleted_at')) if state == 'deleted' else None,
        'created_by': _optional_string(payload.get('created_by')),
        'continuation_enabled': bool(payload.get('continuation_enabled', False)),
        'enable_subagents': bool(payload.get('enable_subagents', False)),
        'created_by_kind': str(payload.get('created_by_kind') or 'owner'),
        'created_time': created_time,
        'updated_time': timestamp,
    }


def _task_sync_payload(
    task_uuid: str,
    stored_task: dict[str, Any],
    payload: dict[str, Any],
    event: ClientEvent,
) -> dict[str, Any]:
    return {
        'task_id': task_uuid,
        'server_id': payload.get('server_id'),
        'owner_id': stored_task['owner_id'],
        'agent_id': stored_task['agent_id'],
        'name': stored_task['name'],
        'description': stored_task['description'],
        'prompt': stored_task['prompt'],
        'system_prompt': stored_task['system_prompt'],
        'skill_bundle_ids': stored_task['skill_bundle_ids'],
        'skill_bundle_refs': stored_task['skill_bundle_refs'],
        'skill_ids': stored_task['skill_ids'],
        'schedule_type': stored_task['schedule_type'],
        'schedule_config': stored_task['schedule_config'],
        'schedule_display': stored_task['schedule_display'],
        'enabled': stored_task['enabled'],
        'state': stored_task['state'],
        'continuation_enabled': stored_task['continuation_enabled'],
        'enable_subagents': stored_task['enable_subagents'],
        'created_by_kind': stored_task['created_by_kind'],
        'next_run_at': payload.get('next_run_at'),
        'run_count': stored_task['run_count'],
        'repeat_times': stored_task['repeat_times'],
        'repeat_completed': stored_task['repeat_completed'],
        'sync_status': payload.get('sync_status') or 'synced',
        'created_at': payload.get('created_at'),
        'updated_at': payload.get('updated_at'),
        'deleted_at': payload.get('deleted_at') if event.event_type == 'task.deleted' else None,
    }


def _task_sync_payload_from_row(row: dict[str, Any]) -> dict[str, Any]:
    task_uuid = str(row.get('task_uuid') or '')
    return {
        'task_id': task_uuid,
        'task_uuid': task_uuid,
        'server_id': row.get('server_id'),
        'owner_id': str(row.get('owner_id') or ''),
        'agent_id': str(row.get('agent_id') or ''),
        'name': str(row.get('name') or ''),
        'description': row.get('description'),
        'prompt': str(row.get('prompt') or ''),
        'system_prompt': row.get('system_prompt'),
        'skill_bundle_ids': _coerce_list(row.get('skill_bundle_ids')),
        'skill_bundle_refs': _coerce_list(row.get('skill_bundle_refs')),
        'skill_ids': _coerce_list(row.get('skill_ids')),
        'schedule_type': str(row.get('schedule_type') or 'once'),
        'schedule_config': _coerce_dict(row.get('schedule_config')),
        'schedule_display': row.get('schedule_display'),
        'enabled': bool(row.get('enabled', True)),
        'state': str(row.get('state') or 'scheduled'),
        'continuation_enabled': bool(row.get('continuation_enabled', False)),
        'enable_subagents': bool(row.get('enable_subagents', False)),
        'created_by_kind': str(row.get('created_by_kind') or 'owner'),
        'next_run_at': _timestamp_or_original(row.get('next_run_at')),
        'run_count': _optional_int(row.get('run_count')) or 0,
        'repeat_times': _optional_int(row.get('repeat_times')),
        'repeat_completed': _optional_int(row.get('repeat_completed')) or 0,
        'sync_status': 'synced',
        'created_at': _timestamp_or_original(row.get('created_time')),
        'updated_at': _timestamp_or_original(row.get('updated_time')),
    }


def _task_assignment_event_payload(
    task: dict[str, Any],
    assignment: dict[str, Any],
    old_node_id: str,
) -> dict[str, Any]:
    return {
        **_task_sync_payload_from_row(task),
        'executor_policy': assignment['executor_kind'],
        'executor_kind': assignment['executor_kind'],
        'executor_node_id': assignment['executor_node_id'],
        'binding_id': assignment['binding_id'],
        'assignment_state': assignment['assignment_state'],
        'previous_executor_node_id': old_node_id or None,
        'visible_node_ids': [assignment['executor_node_id']] if assignment['executor_node_id'] else [],
    }


def _assignment_from_runtime_report(report: dict[str, Any]) -> dict[str, Any]:
    dispatchable = (
        report.get('runtime_status') == 'online'
        and bool(report.get('adapter_registered', True))
        and bool(report.get('handle_available', True))
        and bool(report.get('node_id'))
    )
    if not dispatchable:
        return {
            'executor_kind': 'unresolved',
            'executor_node_id': '',
            'binding_id': report.get('binding_id'),
            'assignment_state': 'unresolved',
            'resolved_at': report.get('reported_at') or timezone.now(),
        }
    return {
        'executor_kind': 'cloud_runtime_host' if _runtime_report_is_cloud_host(report) else 'local_node',
        'executor_node_id': str(report.get('node_id') or ''),
        'binding_id': report.get('binding_id'),
        'assignment_state': 'assigned',
        'resolved_at': report.get('reported_at') or timezone.now(),
    }


def _runtime_report_is_cloud_host(report: dict[str, Any]) -> bool:
    summary = _coerce_dict(report.get('summary_json'))
    runtime_type = str(report.get('runtime_type') or '').lower()
    runtime_host = str(summary.get('runtime_host') or summary.get('host_kind') or '').lower()
    return (
        runtime_type in {'cloud_runtime_host', 'cloud_hermes', 'cloud_sdk'}
        or runtime_host in {'cloud', 'cloud_runtime_host'}
        or bool(summary.get('cloud_runtime_host'))
        or bool(summary.get('is_cloud_runtime_host'))
    )


def _task_run_summary_for_storage(
    request: TaskRunSummaryRequest,
    *,
    owner_id: str,
    agent_hasn_id: str,
) -> dict[str, Any]:
    task_uuid = str(request.task_uuid or request.task_id or '')
    run_uuid = str(request.run_uuid or request.run_id or request.task_run_id or uuid.uuid4())
    dedupe_key = str(request.dedupe_key or run_uuid)
    return {
        'run_uuid': run_uuid,
        'task_uuid': task_uuid,
        'owner_id': owner_id,
        'agent_id': agent_hasn_id,
        'executor_node_id': request.executor_node_id,
        'session_id': request.session_id,
        'scheduled_fire_at': _coerce_datetime_or_none(request.scheduled_fire_at),
        'dedupe_key': dedupe_key,
        'status': request.status,
        'output_summary': request.output_summary if request.output_summary is not None else request.output,
        'error': request.error,
        'deep_link': request.deep_link,
        'model': request.model,
        'token_usage': request.token_usage,
        'duration_ms': request.duration_ms,
        'started_at': _coerce_datetime_or_none(request.started_at),
        'finished_at': _coerce_datetime_or_none(request.finished_at),
    }


def _task_run_summary_event_payload(summary: dict[str, Any]) -> dict[str, Any]:
    response = _task_run_summary_response_payload(summary)
    return {
        'owner_id': response['owner_id'],
        'agent_id': response['agent_id'],
        'task_id': response['task_uuid'],
        'task_uuid': response['task_uuid'],
        'run_uuid': response['run_uuid'],
        'dedupe_key': response['dedupe_key'],
        'status': response['status'],
        'output_summary': response['output_summary'],
        'error': response['error'],
        'deep_link': response['deep_link'],
    }


def _task_run_summary_response_payload(summary: dict[str, Any]) -> dict[str, Any]:
    token_usage = summary.get('token_usage')
    if isinstance(token_usage, str):
        try:
            token_usage = json.loads(token_usage)
        except json.JSONDecodeError:
            token_usage = None
    return {
        'run_uuid': str(summary.get('run_uuid') or ''),
        'task_uuid': str(summary.get('task_uuid') or summary.get('task_id') or ''),
        'owner_id': str(summary.get('owner_id') or ''),
        'agent_id': str(summary.get('agent_id') or ''),
        'session_id': summary.get('session_id'),
        'dedupe_key': str(summary.get('dedupe_key') or ''),
        'status': str(summary.get('status') or ''),
        'output_summary': summary.get('output_summary') if summary.get('output_summary') is not None else summary.get('output'),
        'error': summary.get('error'),
        'deep_link': summary.get('deep_link'),
        'model': summary.get('model'),
        'token_usage': token_usage if isinstance(token_usage, dict) else None,
        'duration_ms': summary.get('duration_ms'),
    }


def _required_string(payload: dict[str, Any], key: str, error_name: str) -> str:
    value = payload.get(key)
    if not isinstance(value, str) or not value:
        raise errors.RequestError(msg=error_name)
    return value


def _optional_string(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value)
    return text if text else None


def _optional_int(value: Any) -> int | None:
    if value is None or value == '':
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _coerce_list(value: Any) -> list[Any]:
    if isinstance(value, list):
        return value
    return []


def _coerce_list_or_none(value: Any) -> list[Any] | None:
    if value is None:
        return None
    return _coerce_list(value)


def _coerce_datetime_or_none(value: Any) -> datetime | None:
    if value is None:
        return None
    if isinstance(value, datetime):
        return value
    if isinstance(value, int | float):
        return datetime.fromtimestamp(value, tz=datetime_timezone.utc)
    if isinstance(value, str):
        if not value:
            return None
        try:
            return datetime.fromisoformat(value.replace('Z', '+00:00'))
        except ValueError:
            return None
    return None


def _timestamp_or_original(value: Any) -> Any:
    if isinstance(value, datetime):
        return int(value.timestamp())
    return value


def _memory_aggregate_id(event: ClientEvent) -> str:
    record_id = event.payload.get('record_id')
    namespace = event.payload.get('namespace')
    sync_scope_kind = event.payload.get('sync_scope_kind')
    sync_scope_id = event.payload.get('sync_scope_id')
    if record_id:
        return str(record_id)
    if namespace and sync_scope_kind and sync_scope_id:
        return f'{sync_scope_kind}:{sync_scope_id}:{namespace}'
    return event.client_event_id


def _memory_pull_selections(request: MemorySyncPullRequest) -> list[MemorySyncCursor]:
    namespace_map: dict[str, list[str]] = {}
    for selector in request.namespaces:
        namespace_map.setdefault(selector.sync_scope_kind, [])
        for namespace in selector.names:
            if not _memory_namespace_allowed(selector.sync_scope_kind, namespace):
                raise errors.RequestError(
                    msg=_MEMORY_NAMESPACE_UNKNOWN_ERROR.name,
                    data=_MEMORY_NAMESPACE_UNKNOWN_ERROR.model_dump(),
                )
            if namespace not in namespace_map[selector.sync_scope_kind]:
                namespace_map[selector.sync_scope_kind].append(namespace)

    cursor_map = {
        (cursor.sync_scope_kind, cursor.sync_scope_id, cursor.namespace): cursor.last_pulled_revision
        for cursor in request.cursors
    }
    selections: list[MemorySyncCursor] = []
    for namespace in namespace_map.get('owner', []):
        selections.append(
            MemorySyncCursor(
                sync_scope_kind='owner',
                sync_scope_id=request.owner_id,
                namespace=namespace,
                last_pulled_revision=cursor_map.get(('owner', request.owner_id, namespace), 0),
            )
        )
    for agent_id in request.agent_ids:
        for namespace in namespace_map.get('agent', []):
            selections.append(
                MemorySyncCursor(
                    sync_scope_kind='agent',
                    sync_scope_id=agent_id,
                    namespace=namespace,
                    last_pulled_revision=cursor_map.get(('agent', agent_id, namespace), 0),
                )
            )
    return selections


def _advance_memory_cursors(
    selections: list[MemorySyncCursor], events: list[SyncEventRecord]
) -> list[MemorySyncCursor]:
    revisions = {
        (cursor.sync_scope_kind, cursor.sync_scope_id, cursor.namespace): cursor.last_pulled_revision
        for cursor in selections
    }
    for event in events:
        sync_scope_kind = event.payload.get('sync_scope_kind')
        sync_scope_id = event.payload.get('sync_scope_id')
        namespace = event.payload.get('namespace')
        namespace_revision = event.payload.get('namespace_revision')
        key = (sync_scope_kind, sync_scope_id, namespace)
        if key in revisions and isinstance(namespace_revision, int):
            revisions[key] = max(revisions[key], namespace_revision)

    return [
        MemorySyncCursor(
            sync_scope_kind=cursor.sync_scope_kind,
            sync_scope_id=cursor.sync_scope_id,
            namespace=cursor.namespace,
            last_pulled_revision=revisions[(cursor.sync_scope_kind, cursor.sync_scope_id, cursor.namespace)],
        )
        for cursor in selections
    ]


def _memory_namespace_revision_key(event: ClientEvent) -> tuple[str, str, str]:
    sync_scope_kind = _required_memory_payload_string(event, 'sync_scope_kind')
    sync_scope_id = _required_memory_payload_string(event, 'sync_scope_id')
    namespace = _required_memory_payload_string(event, 'namespace')
    if sync_scope_kind not in MEMORY_SYNC_SCOPE_KINDS:
        raise errors.RequestError(msg='ERR_MEMORY_SYNC_SCOPE_INVALID')
    return sync_scope_kind, sync_scope_id, namespace


def _memory_namespace_allowed(sync_scope_kind: str, namespace: str) -> bool:
    if sync_scope_kind == 'owner':
        return namespace in OWNER_MEMORY_NAMESPACES
    if sync_scope_kind == 'agent':
        return namespace in AGENT_MEMORY_NAMESPACES
    return False


def _required_memory_payload_string(event: ClientEvent, key: str) -> str:
    value = event.payload.get(key)
    if not isinstance(value, str) or not value:
        raise errors.RequestError(msg='ERR_MEMORY_SYNC_SCOPE_INVALID')
    return value


def _report_id(owner_id: str, node_id: str, summary: RuntimeSummary) -> str:
    stable = uuid.uuid5(uuid.NAMESPACE_URL, f'hasn:runtime-report:{owner_id}:{node_id}:{summary.agent_id}')
    return f'rr_{stable.hex[:24]}'


def _runtime_status_for_storage(status: str) -> str:
    if status == 'missing':
        return 'unavailable'
    if status == 'failed':
        return 'error'
    return status


def _contains_private_runtime_key(value: Any) -> bool:
    if isinstance(value, dict):
        for key, child in value.items():
            if str(key).lower() in PRIVATE_RUNTIME_KEYS:
                return True
            if _contains_private_runtime_key(child):
                return True
    elif isinstance(value, list):
        return any(_contains_private_runtime_key(item) for item in value)
    return False


def _redact_runtime_summary(summary: dict[str, Any]) -> dict[str, Any]:
    return {
        key: value
        for key, value in dict(summary).items()
        if str(key).lower() not in PRIVATE_RUNTIME_KEYS
    }


def _coerce_datetime(value: Any) -> datetime:
    if isinstance(value, datetime):
        return value
    return timezone.now()


def _coerce_dict(value: Any) -> dict[str, Any]:
    if isinstance(value, dict):
        return value
    if isinstance(value, str):
        try:
            parsed = json.loads(value)
            return parsed if isinstance(parsed, dict) else {}
        except json.JSONDecodeError:
            return {}
    return {}


hasn_sync_service = HasnSyncService()
