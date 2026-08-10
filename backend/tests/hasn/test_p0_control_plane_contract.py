from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import pytest

from backend.app.hasn.service import hasn_onboarding_service as onboarding_mod

REPO_ROOT = Path(__file__).resolve().parents[3]
CONTROL_PLANE_CODEGEN_TABLES = (
    'hasn_agent_runtime_reports',
    'hasn_channel_bindings',
    'hasn_clients',
    'hasn_pending_intents',
    'hasn_suppressed_messages',
    'hasn_sync_events',
    'hasn_sync_inbox_events',
)


def test_p0_control_plane_sql_tables_have_codegen_backend_foundation() -> None:
    """P0 tables must have generated backend foundations; CRUD files are codegen-owned."""
    missing: list[str] = []
    for table in CONTROL_PLANE_CODEGEN_TABLES:
        expected = {
            REPO_ROOT / 'backend' / 'app' / 'hasn' / 'model' / f'{table}.py',
            REPO_ROOT / 'backend' / 'app' / 'hasn' / 'schema' / f'{table}.py',
            REPO_ROOT / 'backend' / 'app' / 'hasn' / 'crud' / f'crud_{table}.py',
            REPO_ROOT / 'backend' / 'app' / 'hasn' / 'service' / f'{table}_service.py',
        }
        missing.extend(path.relative_to(REPO_ROOT).as_posix() for path in expected if not path.exists())
    assert missing == []


def test_onboarding_default_agent_uses_assistant_idempotency_key() -> None:
    assert onboarding_mod.DEFAULT_AGENT_NAME == 'assistant'


@dataclass
class _CapturingSyncGateway:
    sync_events: list[Any] = field(default_factory=list)
    client_events: list[Any] = field(default_factory=list)
    namespace_revisions: dict[tuple[str, str, str], dict[str, Any]] = field(default_factory=dict)
    client_event_revisions: dict[tuple[str, str, str], int] = field(default_factory=dict)

    async def pull_events(self, db: Any, *, owner_id: str, after_revision: int, limit: int) -> list[Any]:
        return [event for event in self.sync_events if event.revision > after_revision][:limit]

    async def pull_memory_events(
        self, db: Any, *, owner_id: str, selections: list[Any], limit: int
    ) -> list[Any]:
        del db, owner_id
        selected = {
            (cursor.sync_scope_kind, cursor.sync_scope_id, cursor.namespace): cursor.last_pulled_revision
            for cursor in selections
        }
        events = []
        for event in self.sync_events:
            key = (
                event.payload.get('sync_scope_kind'),
                event.payload.get('sync_scope_id'),
                event.payload.get('namespace'),
            )
            if key in selected and int(event.payload.get('namespace_revision', 0)) > selected[key]:
                events.append(event)
        return events[:limit]

    async def existing_client_event_revision(
        self, db: Any, *, owner_id: str, node_id: str, client_event_id: str
    ) -> int | None:
        return self.client_event_revisions.get((owner_id, node_id, client_event_id))

    async def save_client_event(self, db: Any, *, owner_id: str, node_id: str, event: Any) -> int | None:
        existing_revision = await self.existing_client_event_revision(
            db,
            owner_id=owner_id,
            node_id=node_id,
            client_event_id=event.client_event_id,
        )
        if existing_revision is not None:
            return existing_revision
        if event.event_type.startswith('memory.'):
            from backend.app.hasn.schema.hasn_sync import SyncEventRecord
            from backend.app.hasn.service.hasn_sync_service import _memory_namespace_revision_key

            sync_scope_kind, sync_scope_id, namespace = _memory_namespace_revision_key(event)
            revision_key = (sync_scope_kind, sync_scope_id, namespace)
            previous = self.namespace_revisions.get(revision_key)
            namespace_revision = int(previous['revision']) + 1 if previous else 1
            revision = len(self.sync_events) + 1
            event_id = f'se_memory_{revision}'
            payload = {
                **event.payload,
                'client_event_id': event.client_event_id,
                'node_id': node_id,
                'namespace_revision': namespace_revision,
            }
            self.sync_events.append(
                SyncEventRecord(
                    event_id=event_id,
                    event_type=event.event_type,
                    revision=revision,
                    created_at=datetime(2026, 5, 1, 9, revision, tzinfo=timezone.utc),
                    payload=payload,
                )
            )
            self.namespace_revisions[revision_key] = {'revision': namespace_revision, 'last_event_id': event_id}
            self.client_events.append((owner_id, node_id, event))
            self.client_event_revisions[owner_id, node_id, event.client_event_id] = revision
            return revision
        self.client_events.append((owner_id, node_id, event))
        return None


CapturingSyncGateway: Any = _CapturingSyncGateway
TEST_DB: Any = None


@pytest.mark.asyncio
async def test_sync_pull_still_delivers_runtime_reported_events_after_write_path_retired() -> None:
    """写入端摘除后，历史 `runtime.reported` 事件的**读端**（sync pull）必须照常可拉。

    2026-08-10 云端 Runtime 形态退役，`report_runtime` / `save_runtime_report` 整条写入链路删除；
    但 `hasn_agent_runtime_reports` 表与既有 sync 事件都保留，daemon 侧的下行读不能被连坐。
    """
    from backend.app.hasn.schema.hasn_sync import SyncEventRecord, SyncPullRequest
    from backend.app.hasn.service.hasn_sync_service import HasnSyncService

    gateway = CapturingSyncGateway(
        sync_events=[
            SyncEventRecord(
                event_id='se_1',
                event_type='runtime.reported',
                revision=4,
                created_at=datetime(2026, 5, 1, 9, tzinfo=timezone.utc),
                payload={'agent_id': 'a_agent'},
            )
        ]
    )
    service = HasnSyncService(gateway=gateway)

    pull_response = await service.pull(TEST_DB, SyncPullRequest(owner_id='h_owner', cursor='owner:h_owner:3'))

    assert pull_response.next_cursor == 'owner:h_owner:4'
    assert [event.event_id for event in pull_response.events] == ['se_1']


def test_runtime_report_write_path_is_retired() -> None:
    """防回归：Runtime 上报的写入链路已整条摘除（2026-08-10 云端 Runtime 形态退役）。

    原两条用例断的是「接受脱敏摘要」与「拒绝私有元数据」，都以写入端存在为前提。
    写入端删除后这里改断三个入口都不在：service 的 `report_runtime`、gateway 实现与
    Protocol 的 `save_runtime_report`、以及触发任务执行器归属刷新的
    `_refresh_task_assignments_for_runtime_report`。任一被加回来即红。

    注：私有元数据拒绝（`ERR_RUNTIME_PRIVATE_METADATA_REJECTED`）并未失守——
    sync push 路径仍在用 `_contains_private_runtime_key` 拦截。
    """
    from backend.app.hasn.service.hasn_sync_service import HasnSyncService, SqlAlchemySyncGateway, SyncGateway

    assert not hasattr(HasnSyncService, 'report_runtime')
    assert not hasattr(SqlAlchemySyncGateway, 'save_runtime_report')
    assert not hasattr(SqlAlchemySyncGateway, '_refresh_task_assignments_for_runtime_report')
    assert not hasattr(SyncGateway, 'save_runtime_report')


@pytest.mark.asyncio
async def test_sync_push_memory_owner_event_becomes_pullable_owner_sync_event() -> None:
    from backend.app.hasn.schema.hasn_sync import ClientEvent, SyncPullRequest, SyncPushRequest
    from backend.app.hasn.service.hasn_sync_service import HasnSyncService

    gateway = CapturingSyncGateway()
    service = HasnSyncService(gateway=gateway)

    push_response = await service.push(
        TEST_DB,
        SyncPushRequest(
            owner_id='h_owner',
            node_id='n_runtime',
            events=[
                ClientEvent(
                    client_event_id='ce_memory_owner_event_1',
                    event_type='memory.owner_event.upserted',
                    hasn_id='h_owner',
                    dedupe_key='memory:owner_event:h_owner:1',
                    payload={
                        'sync_scope_kind': 'owner',
                        'sync_scope_id': 'h_owner',
                        'namespace': 'events',
                        'record_id': 'owner_event:h_owner:1',
                        'revision': 1,
                    },
                )
            ],
        ),
    )
    pull_response = await service.pull(TEST_DB, SyncPullRequest(owner_id='h_owner', cursor='owner:h_owner:0'))

    assert push_response.accepted == 1
    assert push_response.next_cursor == 'owner:h_owner:1'
    assert [event.event_type for event in pull_response.events] == ['memory.owner_event.upserted']
    assert pull_response.events[0].payload['namespace'] == 'events'
    assert pull_response.next_cursor == 'owner:h_owner:1'


@pytest.mark.asyncio
async def test_sync_push_rejects_unknown_memory_namespace_before_revision_advance() -> None:
    from backend.app.hasn.schema.hasn_sync import ClientEvent, SyncPushRequest
    from backend.app.hasn.service.hasn_sync_service import HasnSyncService

    gateway = CapturingSyncGateway()
    service = HasnSyncService(gateway=gateway)

    push_response = await service.push(
        TEST_DB,
        SyncPushRequest(
            owner_id='h_owner',
            node_id='n_runtime',
            events=[
                ClientEvent(
                    client_event_id='ce_memory_unknown_namespace_1',
                    event_type='memory.owner_event.upserted',
                    hasn_id='h_owner',
                    payload={
                        'sync_scope_kind': 'owner',
                        'sync_scope_id': 'h_owner',
                        'namespace': 'unknown_namespace',
                        'record_id': 'owner_event:h_owner:unknown',
                        'revision': 1,
                    },
                )
            ],
        ),
    )

    assert push_response.accepted == 0
    assert [error.name for error in push_response.rejected] == ['ERR_MEMORY_NAMESPACE_UNKNOWN']
    assert push_response.next_cursor == 'owner:h_owner:0'
    assert gateway.sync_events == []
    assert gateway.namespace_revisions == {}
    assert gateway.client_events == []


@pytest.mark.asyncio
async def test_sync_push_memory_retry_is_idempotent_for_namespace_revision() -> None:
    from backend.app.hasn.schema.hasn_sync import ClientEvent, SyncPullRequest, SyncPushRequest
    from backend.app.hasn.service.hasn_sync_service import HasnSyncService

    gateway = CapturingSyncGateway()
    service = HasnSyncService(gateway=gateway)
    request = SyncPushRequest(
        owner_id='h_owner',
        node_id='n_runtime',
        events=[
            ClientEvent(
                client_event_id='ce_memory_retry_1',
                event_type='memory.owner_event.upserted',
                hasn_id='h_owner',
                payload={
                    'sync_scope_kind': 'owner',
                    'sync_scope_id': 'h_owner',
                    'namespace': 'events',
                    'record_id': 'owner_event:h_owner:retry',
                    'revision': 1,
                },
            )
        ],
    )

    first_response = await service.push(TEST_DB, request)
    retry_response = await service.push(TEST_DB, request)
    pull_response = await service.pull(TEST_DB, SyncPullRequest(owner_id='h_owner', cursor='owner:h_owner:0'))

    assert first_response.accepted == 1
    assert retry_response.accepted == 1
    assert first_response.next_cursor == 'owner:h_owner:1'
    assert retry_response.next_cursor == 'owner:h_owner:1'
    assert [event.event_id for event in pull_response.events] == ['se_memory_1']
    assert [event.payload['namespace_revision'] for event in pull_response.events] == [1]
    assert gateway.namespace_revisions == {
        ('owner', 'h_owner', 'events'): {'revision': 1, 'last_event_id': 'se_memory_1'},
    }
    assert [event.client_event_id for _, _, event in gateway.client_events] == ['ce_memory_retry_1']


@pytest.mark.asyncio
async def test_sync_push_memory_owner_and_agent_events_advance_namespace_revision_independently() -> None:
    from backend.app.hasn.schema.hasn_sync import ClientEvent, SyncPullRequest, SyncPushRequest
    from backend.app.hasn.service.hasn_sync_service import HasnSyncService

    gateway = CapturingSyncGateway()
    service = HasnSyncService(gateway=gateway)

    push_response = await service.push(
        TEST_DB,
        SyncPushRequest(
            owner_id='h_owner',
            node_id='n_runtime',
            events=[
                ClientEvent(
                    client_event_id='ce_owner_event_1',
                    event_type='memory.owner_event.upserted',
                    hasn_id='h_owner',
                    payload={
                        'sync_scope_kind': 'owner',
                        'sync_scope_id': 'h_owner',
                        'namespace': 'events',
                        'record_id': 'owner_event:h_owner:1',
                        'revision': 1,
                    },
                ),
                ClientEvent(
                    client_event_id='ce_owner_event_2',
                    event_type='memory.owner_event.upserted',
                    hasn_id='h_owner',
                    payload={
                        'sync_scope_kind': 'owner',
                        'sync_scope_id': 'h_owner',
                        'namespace': 'events',
                        'record_id': 'owner_event:h_owner:2',
                        'revision': 2,
                    },
                ),
                ClientEvent(
                    client_event_id='ce_agent_event_1',
                    event_type='memory.agent_self_event.upserted',
                    hasn_id='a_agent_event',
                    payload={
                        'sync_scope_kind': 'agent',
                        'sync_scope_id': 'a_agent_event',
                        'namespace': 'agent_events',
                        'record_id': 'agent_event:a_agent_event:1',
                        'revision': 1,
                    },
                ),
            ],
        ),
    )
    pull_response = await service.pull(TEST_DB, SyncPullRequest(owner_id='h_owner', cursor='owner:h_owner:0'))

    assert push_response.accepted == 3
    assert push_response.next_cursor == 'owner:h_owner:3'
    assert [event.revision for event in pull_response.events] == [1, 2, 3]
    assert [event.payload['namespace_revision'] for event in pull_response.events] == [1, 2, 1]
    assert gateway.namespace_revisions == {
        ('owner', 'h_owner', 'events'): {'revision': 2, 'last_event_id': 'se_memory_2'},
        ('agent', 'a_agent_event', 'agent_events'): {'revision': 1, 'last_event_id': 'se_memory_3'},
    }
    assert pull_response.events[1].payload['sync_scope_kind'] == 'owner'
    assert pull_response.events[1].payload['sync_scope_id'] == 'h_owner'
    assert pull_response.events[1].payload['namespace'] == 'events'
    assert pull_response.events[1].payload['record_id'] == 'owner_event:h_owner:2'


@pytest.mark.asyncio
async def test_memory_sync_pull_filters_by_namespace_revision_and_returns_namespace_cursors() -> None:
    from backend.app.hasn.schema.hasn_sync import (
        ClientEvent,
        MemorySyncCursor,
        MemorySyncNamespaceSelector,
        MemorySyncPullRequest,
        SyncPushRequest,
    )
    from backend.app.hasn.service.hasn_sync_service import HasnSyncService

    gateway = CapturingSyncGateway()
    service = HasnSyncService(gateway=gateway)
    await service.push(
        TEST_DB,
        SyncPushRequest(
            owner_id='h_owner',
            node_id='n_runtime',
            events=[
                ClientEvent(
                    client_event_id='ce_owner_event_1',
                    event_type='memory.owner_event.upserted',
                    hasn_id='h_owner',
                    payload={
                        'sync_scope_kind': 'owner',
                        'sync_scope_id': 'h_owner',
                        'namespace': 'events',
                        'record_id': 'owner_event:h_owner:1',
                        'revision': 1,
                    },
                ),
                ClientEvent(
                    client_event_id='ce_owner_event_2',
                    event_type='memory.owner_event.upserted',
                    hasn_id='h_owner',
                    payload={
                        'sync_scope_kind': 'owner',
                        'sync_scope_id': 'h_owner',
                        'namespace': 'events',
                        'record_id': 'owner_event:h_owner:2',
                        'revision': 2,
                    },
                ),
                ClientEvent(
                    client_event_id='ce_owner_fact_1',
                    event_type='memory.owner_fact.upserted',
                    hasn_id='h_owner',
                    payload={
                        'sync_scope_kind': 'owner',
                        'sync_scope_id': 'h_owner',
                        'namespace': 'facts',
                        'record_id': 'fact:h_owner:1',
                        'revision': 1,
                    },
                ),
                ClientEvent(
                    client_event_id='ce_agent_event_1',
                    event_type='memory.agent_self_event.upserted',
                    hasn_id='a_agent_event',
                    payload={
                        'sync_scope_kind': 'agent',
                        'sync_scope_id': 'a_agent_event',
                        'namespace': 'agent_events',
                        'record_id': 'agent_event:a_agent_event:1',
                        'revision': 1,
                    },
                ),
            ],
        ),
    )

    response = await service.pull_memory(
        TEST_DB,
        MemorySyncPullRequest(
            owner_id='h_owner',
            agent_ids=['a_agent_event'],
            namespaces=[
                MemorySyncNamespaceSelector(sync_scope_kind='owner', names=['events']),
                MemorySyncNamespaceSelector(sync_scope_kind='agent', names=['agent_events']),
            ],
            cursors=[
                MemorySyncCursor(
                    sync_scope_kind='owner',
                    sync_scope_id='h_owner',
                    namespace='events',
                    last_pulled_revision=1,
                ),
                MemorySyncCursor(
                    sync_scope_kind='agent',
                    sync_scope_id='a_agent_event',
                    namespace='agent_events',
                    last_pulled_revision=0,
                ),
            ],
            max_events=10,
        ),
    )

    assert [event.event_id for event in response.events] == ['se_memory_2', 'se_memory_4']
    assert [event.payload['namespace_revision'] for event in response.events] == [2, 1]
    assert [cursor.model_dump() for cursor in response.next_cursors] == [
        {
            'sync_scope_kind': 'owner',
            'sync_scope_id': 'h_owner',
            'namespace': 'events',
            'last_pulled_revision': 2,
        },
        {
            'sync_scope_kind': 'agent',
            'sync_scope_id': 'a_agent_event',
            'namespace': 'agent_events',
            'last_pulled_revision': 1,
        },
    ]
    assert response.has_more is False


@pytest.mark.asyncio
async def test_sync_push_rejects_malformed_memory_event_without_scope_fields() -> None:
    from backend.app.hasn.schema.hasn_sync import ClientEvent, SyncPushRequest
    from backend.app.hasn.service.hasn_sync_service import HasnSyncService

    gateway = CapturingSyncGateway()
    service = HasnSyncService(gateway=gateway)

    push_response = await service.push(
        TEST_DB,
        SyncPushRequest(
            owner_id='h_owner',
            node_id='n_runtime',
            events=[
                ClientEvent(
                    client_event_id='ce_bad_memory',
                    event_type='memory.owner_fact.upserted',
                    hasn_id='h_owner',
                    payload={'record_id': 'fact:h_owner:1'},
                ),
                ClientEvent(
                    client_event_id='ce_good_memory',
                    event_type='memory.owner_fact.upserted',
                    hasn_id='h_owner',
                    payload={
                        'sync_scope_kind': 'owner',
                        'sync_scope_id': 'h_owner',
                        'namespace': 'facts',
                        'record_id': 'fact:h_owner:2',
                        'revision': 1,
                    },
                ),
            ],
        ),
    )

    assert push_response.accepted == 1
    assert [error.name for error in push_response.rejected] == ['ERR_MEMORY_SYNC_SCOPE_INVALID']
    assert push_response.next_cursor == 'owner:h_owner:1'
    assert [event.client_event_id for _, _, event in gateway.client_events] == ['ce_good_memory']
    assert gateway.namespace_revisions == {
        ('owner', 'h_owner', 'facts'): {'revision': 1, 'last_event_id': 'se_memory_1'},
    }
