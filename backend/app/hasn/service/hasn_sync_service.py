"""P0 HASN sync/runtime report service.

The service owns the hand-written hasn-node API boundary. Generated CRUD remains
available for admin inspection, but hasn-node uses these redacted, owner-scoped
methods instead of generic table CRUD.
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
    RuntimeReportRequest,
    RuntimeReportResponse,
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
    _assignment_from_runtime_report,
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
    _redact_runtime_summary,
    _report_id,
    _required_string,
    _runtime_status_for_storage,
    _task_assignment_event_payload,
    _task_cursor,
    _task_payload_for_storage,
    _task_run_summary_event_payload,
    _task_run_summary_for_storage,
    _task_run_summary_response_payload,
    _task_storage_row,
    _task_sync_payload,
    _task_sync_payload_from_row,
)
from backend.common.exception import errors
from backend.common.log import log
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

# 会话消息事件：客户端推上来后必须落入权威 feed（hasn_sync_events），供换设备 sync/pull
# 还原完整会话历史。owner↔自己分身的对话本地短路执行、不经 route_message，唯一上云路径
# 就是这里（见 docs/hasn-node设计文档/02-数据与同步/06-owner与分身会话跨设备同步修复设计.md）。
# h↔h / 跨 owner 消息由 message_router.route_message 直接写 feed，daemon 侧不重复镜像（去重边界）。
FEED_MESSAGE_EVENT_TYPES = {'message.sent', 'message.received', 'message.agent_reply'}


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
                """
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
                """
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
                """
                SELECT event_id, event_type, revision, occurred_at, payload
                FROM public.hasn_sync_events
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
                """
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
                """
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
        elif event.event_type in FEED_MESSAGE_EVENT_TYPES:
            # owner↔自己分身的会话消息（主人提问 message.sent / 分身回复 message.agent_reply）
            # 本地短路执行、不经 route_message，必须在此落入权威 feed，否则换设备 sync/pull
            # 永远拉不回（历史 bug：这里只写 inbox 死信表）。message.received 一并支持。
            server_revision = await self._append_message_feed_event_idempotent(
                db, owner_id=owner_id, event=event
            )
            # doc16 Phase A「消息上云」：同一条 loopback 消息**额外**落入权威 hasn_messages
            # （单一云端记忆提取的数据源）。feed 已写（上一行），此处只补会话/消息表、不重复
            # 写 feed。best-effort + SAVEPOINT 隔离：落库失败绝不连累 feed/跨设备同步。
            await self._persist_loopback_message_best_effort(db, owner_id=owner_id, event=event)
        await db.execute(
            sa.text(
                """
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

    async def _append_message_feed_event_idempotent(
        self, db: AsyncSession, *, owner_id: str, event: ClientEvent
    ) -> int | None:
        """将会话消息事件幂等地追加到权威 feed（hasn_sync_events）。

        幂等键 = (owner_id, aggregate_id, event_type)，其中 aggregate_id 取 payload.message_id
        （回退 dedupe_key）。同一条镜像消息重复 push 时返回既有 revision、不重复追加。
        h↔h / 跨 owner 消息由 route_message 用云端数字 message_id 写 feed，与本地 ULID
        天然不撞键；daemon 侧也不镜像它们，双重保证不双写。
        """
        message_id = event.payload.get('message_id') or event.dedupe_key
        if not message_id:
            raise errors.RequestError(msg='ERR_MESSAGE_ID_REQUIRED')
        existing = await db.execute(
            sa.text(
                """
                SELECT revision
                FROM public.hasn_sync_events
                WHERE owner_id = :owner_id
                  AND aggregate_type = 'message'
                  AND aggregate_id = :aggregate_id
                  AND event_type = :event_type
                LIMIT 1
                """
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

    async def _persist_loopback_message_best_effort(
        self, db: AsyncSession, *, owner_id: str, event: ClientEvent
    ) -> None:
        """把一条 owner↔自有分身会话同步事件**额外**落入权威 ``hasn_messages``（doc16 Phase A）。

        SAVEPOINT 隔离 + best-effort：本步是「记忆提取数据源」的补写，与跨设备 feed 解耦。
        失败（如分身已删、并发撞键、瞬时 DB 错）只记日志并回滚本 SAVEPOINT，**绝不**连累
        外层 feed 写入与跨设备同步——这正是 daemon 侧「镜像入队 best-effort」的云端对偶。
        """
        from backend.app.hasn.service.owner_message_sync_service import (
            persist_loopback_message_from_sync_event,
        )

        try:
            async with db.begin_nested():
                await persist_loopback_message_from_sync_event(
                    db, owner_id=owner_id, event_type=event.event_type, payload=event.payload
                )
        except Exception as exc:
            log.warning(
                'doc16 hasn_messages persist skipped (owner=%s, event=%s): %r',
                owner_id,
                event.event_type,
                exc,
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
                """
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
                """
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
                """
                SELECT executor_kind, executor_node_id, binding_id, assignment_state
                FROM hasn_task.assignment
                WHERE owner_id = :owner_id
                  AND task_uuid = :task_uuid
                ORDER BY updated_time DESC NULLS LAST, id DESC
                LIMIT 1
                """
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
        event_payload = {**event_payload, 'server_id': task_server_id}
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
        await db.execute(
            sa.text(
                """
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
                SELECT owner_id, agent_id
                FROM hasn_task.task
                WHERE task_uuid = :task_uuid
                LIMIT 1
                """
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
                """
                SELECT event_id
                FROM public.hasn_sync_events
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
                """
                SELECT server_revision
                FROM public.hasn_sync_inbox_events
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
        # sync 事件唯一写入口（R2-07 · doc16 §3.2）。原「advisory lock + SELECT MAX+1 + INSERT」
        # 逻辑已下沉为 PG 函数 hasn_sync.append_event：函数内 per-owner advisory xact lock 串行化
        # gapless revision 分配（uq_hasn_sync_events_owner_revision 要求每 owner 连续无冲突，并发
        # 写不会都读到同一 MAX），并叠加 (owner_id, producer, source_event_id) 幂等去重
        # （带 producer 时跨重启重放返回原 revision·deduped=true，不新增行）。函数在 db 当前事务内
        # 执行，与业务写同事务提交/回滚。所有写入方都经此方法 → 这里就是唯一 append 实现，
        # 严禁「函数 + ORM 直写」双路径（§8.1）。
        result = await db.execute(
            sa.text(
                """
                SELECT revision, event_id, deduped
                FROM hasn_sync.append_event(
                    :owner_id,
                    :hasn_id,
                    :event_type,
                    :aggregate_type,
                    :aggregate_id,
                    CAST(:payload AS jsonb),
                    :producer,
                    :source_event_id,
                    :occurred_at
                )
                """
            ),
            {
                'owner_id': owner_id,
                'hasn_id': hasn_id,
                'event_type': event_type,
                'aggregate_type': aggregate_type,
                'aggregate_id': aggregate_id,
                'payload': json.dumps(payload, ensure_ascii=False, sort_keys=True, default=str),
                'producer': producer,
                'source_event_id': source_event_id,
                'occurred_at': occurred_at,
            },
        )
        row = result.mappings().one()
        return int(row['revision']), row['event_id'], bool(row['deduped'])

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


hasn_sync_service = HasnSyncService()
