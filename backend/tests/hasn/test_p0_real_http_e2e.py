from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from types import SimpleNamespace
from typing import TYPE_CHECKING, Any

from fastapi import FastAPI, Request
from fastapi.testclient import TestClient
from starlette_context.middleware import ContextMiddleware
from starlette_context.plugins import RequestIdPlugin

from backend.app.hasn.api.v1 import ai_native_app as ai_native_api
from backend.app.hasn.api.v1 import onboarding as onboarding_api
from backend.app.hasn.api.v1 import sync as sync_api
from backend.app.hasn.api.v1.app import hasn_task_sessions as task_sessions_api
from backend.app.hasn.api.v1.app import knowledge as knowledge_api
from backend.app.hasn.api.v1.app import workspace as workspace_api
from backend.app.hasn.model import HasnAiNativeAppAudit, HasnSessions
from backend.app.hasn.schema.hasn_onboarding import SandboxSummary
from backend.app.hasn.service import hasn_onboarding_service as onboarding_service_module
from backend.app.hasn.service.hasn_onboarding_service import (
    DEFAULT_AGENT_DISPLAY_NAME,
    SMS_CODE_PREFIX,
    HasnOnboardingService,
    HasnPhoneAuthService,
)
from backend.app.hasn.service.hasn_sync_service import HasnSyncService
from backend.app.hasn.service.workspace_notification_subscriber import (
    RecordingWorkspaceNotificationActions,
    workspace_notification_subscriber,
)
from backend.app.hasn_community.api.v1.app import community as community_api
from backend.app.hasn_task.api.v1.app import run as task_run_api
from backend.app.hasn_task.api.v1.app import skill_bundle as skill_bundle_api
from backend.app.hasn_task.api.v1.app import sync as task_sync_api
from backend.app.hasn_task.api.v1.app import task as task_api
from backend.app.mcp.routes import mcp_router
from backend.common.dataclasses import AgentTokenPayload
from backend.common.exception import errors
from backend.common.exception.exception_handler import register_exception
from backend.common.security.agent_jwt import jwt_decode_agent, jwt_encode_agent
from backend.database.db import get_db, get_db_transaction

if TYPE_CHECKING:
    import pytest

P0_AGENT_ID = 'a_p0_default'
P0_OWNER_ID = 'h_p0_owner'
P0_OWNER_USER_ID = 7
P0_AGENT_SESSION_UUID = 'session-p0-agent-jwt'
P0_AGENT_SCOPES = ['message.read', 'knowledge.read']
P0_AGENT_EXPIRE_TIME = datetime(2099, 1, 1, tzinfo=timezone.utc)


def p0_agent_token(agent_name: str = DEFAULT_AGENT_DISPLAY_NAME) -> str:
    return jwt_encode_agent(
        {
            'sub': P0_AGENT_ID,
            'token_type': 'agent',
            'agent_hasn_id': P0_AGENT_ID,
            'agent_name': agent_name,
            'owner_hasn_id': P0_OWNER_ID,
            'owner_user_id': P0_OWNER_USER_ID,
            'scopes': P0_AGENT_SCOPES,
            'session_uuid': P0_AGENT_SESSION_UUID,
            'exp': int(P0_AGENT_EXPIRE_TIME.timestamp()),
        }
    )


async def fake_cloud_current_knowledge_credentials() -> dict[str, Any]:
    return {
        'code': 200,
        'msg': 'ok',
        'data': {
            'workspace': {
                'kind': 'enterprise',
                'user_id': None,
                'enterprise_id': 42,
                'workspace_key': 'enterprise:42',
            },
            'status': 'pending',
            'credential': None,
        },
    }


class FakeRedis:
    def __init__(self) -> None:
        self.values: dict[str, Any] = {}
        self.ttls: dict[str, int] = {}

    async def exists(self, key: str) -> bool:
        return key in self.values

    async def ttl(self, key: str) -> int:
        return self.ttls.get(key, 0)

    async def setex(self, key: str, seconds: int, value: str) -> None:
        self.values[key] = value
        self.ttls[key] = seconds

    async def get(self, key: str) -> Any:
        return self.values.get(key)

    async def delete(self, key: str) -> None:
        self.values.pop(key, None)
        self.ttls.pop(key, None)

    async def delete_prefix(self, prefix: str, exclude: str | list[str] | None = None, batch_size: int = 1000) -> None:
        exclude_set = set(exclude) if isinstance(exclude, list) else {exclude} if isinstance(exclude, str) else set()
        for key in [key for key in self.values if key.startswith(prefix) and key not in exclude_set]:
            await self.delete(key)


class FakeSms:
    def __init__(self) -> None:
        self.sent: list[tuple[str, str]] = []

    async def send_code(self, phone: str, code: str) -> bool:
        self.sent.append((phone, code))
        return True


@dataclass
class FakeUser:
    id: int
    username: str
    nickname: str
    phone: str
    avatar: str | None = None
    bio: str | None = None
    is_multi_login: bool = True
    last_login_time: Any = None


class FakeUserGateway:
    async def get_or_create_phone_user(self, _db: Any, phone: str) -> tuple[FakeUser, bool]:
        return FakeUser(id=7, username=phone, nickname='P0 Dev User', phone=phone), True


class FakeDb:
    def __init__(self) -> None:
        # 应用平台 v3 P3（设计 17 决策①②）：hasn_workspace_app / hasn_user_active_workspace
        # 已退役删表，FakeDb 不再持有 workspace_apps / active_workspaces。
        self.enterprise_memberships: dict[tuple[int, int], SimpleNamespace] = {}
        self.humans_by_user_id: dict[int, SimpleNamespace] = {}
        self.agents_by_hasn_id: dict[str, SimpleNamespace] = {}
        self.sessions_by_id: dict[str, Any] = {}
        self.audit_rows: list[HasnAiNativeAppAudit] = []

    async def execute(self, stmt: Any) -> Any:
        sql = str(stmt)
        params = getattr(stmt.compile(), 'params', {})
        if 'hasn_agents' in sql:
            if 'hasn_id_1' in params:
                hasn_id = params.get('hasn_id_1')
                assert isinstance(hasn_id, str)
                row = self.agents_by_hasn_id.get(hasn_id)
                return _ScalarResult([row] if row is not None else [])
            rows = [
                agent
                for agent in self.agents_by_hasn_id.values()
                if agent.owner_id == params.get('owner_id_1')
                and (params.get('status_1') is None or agent.status == params.get('status_1'))
            ]
            return _ScalarResult(rows)
        if 'hasn_enterprise_membership' in sql:
            enterprise_id = params.get('enterprise_id_1')
            user_id = params.get('user_id_1')
            assert isinstance(enterprise_id, int)
            assert isinstance(user_id, int)
            row = self.enterprise_memberships.get((enterprise_id, user_id))
            return _ScalarResult([row] if row is not None else [])
        if 'hasn_ai_native_app_audit' in sql:
            return _ScalarResult(self._filter_audit_rows(params))
        if 'hasn_ai_native_app_manifest' in sql:
            return _ScalarResult([])
        if 'hasn_humans' in sql:
            user_id = params.get('user_id_1')
            assert isinstance(user_id, int)
            row = self.humans_by_user_id.get(user_id)
            return _ScalarResult([row] if row is not None else [])
        if 'hasn_sessions' in sql:
            if 'session_id_1' in params:
                session_id = params.get('session_id_1')
                assert isinstance(session_id, str)
                row = self.sessions_by_id.get(session_id)
                return _ScalarResult([row] if row is not None else [])
            rows = [
                session
                for session in self.sessions_by_id.values()
                if session.owner_id == params.get('owner_id_1')
            ]
            return _ScalarResult(rows)
        return _ScalarResult([])

    def add(self, row: Any) -> None:
        if isinstance(row, HasnSessions):
            self.sessions_by_id[row.session_id] = row
            return
        if isinstance(row, HasnAiNativeAppAudit):
            if getattr(row, 'id', None) is None:
                row.id = len(self.audit_rows) + 1
            if getattr(row, 'created_at', None) is None:
                row.created_at = datetime(2026, 5, 20, 8, 0, len(self.audit_rows), tzinfo=timezone.utc)
            self.audit_rows.append(row)
            return
        return

    async def flush(self) -> None:
        return None

    async def commit(self) -> None:
        # runtime_tool_call 末尾显式 commit（d1c33ab9 Bug7），fake 面等价 no-op
        return None

    async def refresh(self, row: Any) -> None:
        if isinstance(row, HasnAiNativeAppAudit) and getattr(row, 'id', None) is None:
            row.id = len(self.audit_rows)

    def _filter_audit_rows(self, params: dict[str, Any]) -> list[HasnAiNativeAppAudit]:
        rows = list(self.audit_rows)
        if 'workspace_kind_1' in params:
            rows = [row for row in rows if row.workspace_kind == params['workspace_kind_1']]
        if 'app_id_1' in params:
            rows = [row for row in rows if row.app_id == params['app_id_1']]
        if 'agent_hasn_id_1' in params:
            rows = [row for row in rows if row.agent_hasn_id == params['agent_hasn_id_1']]
        if 'trace_id_1' in params:
            rows = [row for row in rows if row.trace_id == params['trace_id_1']]
        created_at_from = params.get('created_at_1')
        created_at_to = params.get('created_at_2')
        if created_at_from is not None:
            rows = [row for row in rows if row.created_at >= created_at_from]
        if created_at_to is not None:
            rows = [row for row in rows if row.created_at <= created_at_to]
        return rows


@dataclass
class TaskRecord:
    id: int
    owner_id: str
    agent_id: str
    name: str
    description: str | None
    prompt: str
    system_prompt: str | None
    input_template: str | None
    skill_bundle_ids: list[str]
    skill_bundle_refs: list[dict[str, Any]]
    skill_ids: list[str]
    skill_refs: list[dict[str, Any]]
    workflow_id: int | None
    workflow: dict[str, Any]
    enabled_toolsets: list[str] | None
    context_from_task_id: int | None
    schedule_type: str
    schedule_config: dict[str, Any]
    schedule_display: str | None
    timezone: str
    misfire_policy: str
    catchup_limit: int | None
    enabled: bool
    state: str
    next_run_at: Any
    last_run_at: Any
    last_status: str | None
    last_error: str | None
    run_count: int
    repeat_times: int | None
    repeat_completed: int
    create_time: Any
    update_time: Any
    created_time: Any
    updated_time: Any
    created_by: str | None
    task_uuid: str | None
    executor_policy: str
    executor_node_id: str | None
    task_revision: int
    deleted_at: Any
    # 内置任务列（M2 builtin 后 service 建行会带上）
    builtin_key: str | None = None
    builtin_synced_revision: int | None = None
    # M1（任务系统 AI-Native 化）schema 新列，CreateHasnTaskParam.model_dump() 会带出
    continuation_enabled: bool = False
    enable_subagents: bool = False
    created_by_kind: str = 'owner'
    project_id: str | None = None
    app_id: str | None = None
    execution_kind: str = 'freeform'
    execution_spec: dict[str, Any] = field(default_factory=dict)


@dataclass
class SkillBundleRecord:
    id: int
    owner_id: str
    name: str
    display_name: str | None
    description: str | None
    skill_ids: list[str]
    instruction: str | None
    create_time: Any
    update_time: Any
    created_time: Any
    updated_time: Any


@dataclass
class TaskRunRecord:
    id: int
    task_id: int
    agent_id: str
    session_id: str | None
    source_conversation_id: str | None
    source_message_id: str | None
    runtime_node_id: str | None
    status: str
    started_at: Any
    finished_at: Any
    duration_ms: int | None
    prompt_snapshot: str | None
    output: str | None
    error: str | None
    model: str | None
    token_usage: dict[str, Any] | None
    create_time: Any
    created_time: Any
    updated_time: Any
    # M1（任务系统 AI-Native 化）run 新列
    continued_from_run_id: str | None = None


def _fixture_time() -> datetime:
    return datetime(2026, 5, 22, 9, 0, tzinfo=timezone.utc)


def _page_payload(items: list[Any]) -> dict[str, Any]:
    total = len(items)
    total_pages = 1 if total > 0 else 0
    return {
        'items': items,
        'total': total,
        'page': 1,
        'size': 20,
        'total_pages': total_pages,
        'links': {
            'first': '?page=1&size=20',
            'last': '?page=1&size=20',
            'self': '?page=1&size=20',
            'next': None,
            'prev': None,
        },
    }


@dataclass
class InMemoryTaskStore:
    records: dict[int, TaskRecord] = field(default_factory=dict)
    next_id: int = 1

    async def get_list_by_owner(self, db: Any, owner_id: str) -> dict[str, Any]:
        items = [record for record in self.records.values() if record.owner_id == owner_id]
        items.sort(key=lambda record: record.id, reverse=True)
        return _page_payload(items)

    async def create(self, db: Any, obj: Any) -> TaskRecord:
        payload = obj.model_dump()
        timestamp = _fixture_time()
        create_time = payload.pop('create_time', None) or timestamp
        update_time = payload.pop('update_time', None)
        created_time = payload.pop('created_time', None) or create_time
        updated_time = payload.pop('updated_time', None) or update_time
        record = TaskRecord(
            id=self.next_id,
            **payload,
            create_time=create_time,
            update_time=update_time,
            created_time=created_time,
            updated_time=updated_time,
        )
        self.records[self.next_id] = record
        self.next_id += 1
        return record

    async def get(self, db: Any, pk: int) -> TaskRecord:
        record = self.records.get(pk)
        if record is None:
            raise errors.NotFoundError(msg='任务定义不存在')
        return record

    async def update(self, db: Any, pk: int, obj: Any) -> int:
        record = self.records.get(pk)
        if record is None:
            return 0
        payload = obj.model_dump()
        payload.pop('created_time', None)
        payload.pop('updated_time', None)
        for key, value in payload.items():
            setattr(record, key, value)
        timestamp = _fixture_time()
        record.update_time = timestamp
        record.updated_time = timestamp
        return 1

    async def delete(self, db: Any, obj: Any) -> int:
        deleted = 0
        for pk in obj.pks:
            if pk in self.records:
                del self.records[pk]
                deleted += 1
        return deleted


@dataclass
class InMemorySkillBundleStore:
    records: dict[int, SkillBundleRecord] = field(default_factory=dict)
    next_id: int = 1

    async def get_list_by_owner(self, db: Any, owner_id: str) -> dict[str, Any]:
        items = [record for record in self.records.values() if record.owner_id == owner_id]
        items.sort(key=lambda record: record.id, reverse=True)
        return _page_payload(items)

    async def create(self, db: Any, obj: Any) -> SkillBundleRecord:
        payload = obj.model_dump()
        timestamp = _fixture_time()
        create_time = payload.pop('create_time', None) or timestamp
        update_time = payload.pop('update_time', None)
        created_time = payload.pop('created_time', None) or create_time
        updated_time = payload.pop('updated_time', None) or update_time
        record = SkillBundleRecord(
            id=self.next_id,
            **payload,
            create_time=create_time,
            update_time=update_time,
            created_time=created_time,
            updated_time=updated_time,
        )
        self.records[self.next_id] = record
        self.next_id += 1
        return record

    async def get(self, db: Any, pk: int) -> SkillBundleRecord:
        record = self.records.get(pk)
        if record is None:
            raise errors.NotFoundError(msg='Skill Bundle 定义表（多个 skill 的组合）不存在')
        return record

    async def update(self, db: Any, pk: int, obj: Any) -> int:
        record = self.records.get(pk)
        if record is None:
            return 0
        payload = obj.model_dump()
        payload.pop('created_time', None)
        payload.pop('updated_time', None)
        for key, value in payload.items():
            setattr(record, key, value)
        timestamp = _fixture_time()
        record.update_time = timestamp
        record.updated_time = timestamp
        return 1

    async def delete(self, db: Any, obj: Any) -> int:
        deleted = 0
        for pk in obj.pks:
            if pk in self.records:
                del self.records[pk]
                deleted += 1
        return deleted


@dataclass
class InMemoryTaskRunStore:
    task_store: InMemoryTaskStore
    records: dict[int, TaskRunRecord] = field(default_factory=dict)
    next_id: int = 1

    async def get_list_by_owner(self, db: Any, owner_id: str) -> dict[str, Any]:
        items = [
            record
            for record in self.records.values()
            if (task := self.task_store.records.get(record.task_id)) is not None and task.owner_id == owner_id
        ]
        items.sort(key=lambda record: record.id, reverse=True)
        return _page_payload(items)

    async def get_list_by_task_id(self, db: Any, *, task_id: int) -> dict[str, Any]:
        items = [record for record in self.records.values() if record.task_id == task_id]
        items.sort(key=lambda record: record.id, reverse=True)
        return _page_payload(items)

    async def create(self, db: Any, obj: Any) -> TaskRunRecord:
        payload = obj.model_dump()
        timestamp = _fixture_time()
        create_time = payload.pop('create_time', None) or timestamp
        created_time = payload.pop('created_time', None) or create_time
        updated_time = payload.pop('updated_time', None)
        record = TaskRunRecord(
            id=self.next_id,
            **payload,
            create_time=create_time,
            created_time=created_time,
            updated_time=updated_time,
        )
        self.records[self.next_id] = record
        self.next_id += 1
        return record

    async def get(self, db: Any, pk: int) -> TaskRunRecord:
        record = self.records.get(pk)
        if record is None:
            raise errors.NotFoundError(msg='任务执行记录不存在')
        return record

    async def update(self, db: Any, pk: int, obj: Any) -> int:
        record = self.records.get(pk)
        if record is None:
            return 0
        payload = obj.model_dump()
        payload.pop('created_time', None)
        payload.pop('updated_time', None)
        for key, value in payload.items():
            setattr(record, key, value)
        timestamp = _fixture_time()
        record.updated_time = timestamp
        return 1

    async def delete(self, db: Any, obj: Any) -> int:
        deleted = 0
        for pk in obj.pks:
            if pk in self.records:
                del self.records[pk]
                deleted += 1
        return deleted


class _ScalarResult:
    def __init__(self, rows: list[Any]) -> None:
        self._rows = list(rows)

    def scalars(self) -> _ScalarResult:
        return self

    def first(self) -> Any:
        return self._rows[0] if self._rows else None

    def all(self) -> list[Any]:
        return list(self._rows)

    def scalar_one_or_none(self) -> Any:
        return self.first()

    def scalar(self) -> Any:
        return self.first()


class FakeLlmCredentialIssuer:
    async def issue(self, _db: Any, user: FakeUser) -> tuple[str, str, str]:
        return f'sk-p0-{user.id}', 'https://llm.example/v1', 'test-model'


class FakeAgentTokenIssuer:
    def __init__(self, redis: FakeRedis) -> None:
        self.redis = redis

    async def issue(
        self,
        _db: Any,
        *,
        agent_hasn_id: str,
        agent_name: str,
        owner_hasn_id: str,
        owner_user_id: int,
    ) -> SimpleNamespace:
        assert (agent_hasn_id, owner_hasn_id, owner_user_id) == (P0_AGENT_ID, P0_OWNER_ID, P0_OWNER_USER_ID)
        access_token = p0_agent_token(agent_name)
        await self.redis.setex(f'agent_token:{agent_hasn_id}:{P0_AGENT_SESSION_UUID}', 3600, access_token)
        return SimpleNamespace(
            access_token=access_token,
            access_token_expire_time=P0_AGENT_EXPIRE_TIME,
            scopes=P0_AGENT_SCOPES,
        )


class FakeOnboardingGateway:
    def __init__(self) -> None:
        self.node_id: str | None = None
        self.owner_star_id = '100001'

    async def get_user(self, _db: Any, user_id: int) -> FakeUser | None:
        if user_id != 7:
            return None
        return FakeUser(id=7, username='13800138000', nickname='P0 Dev User', phone='13800138000')

    async def ensure_human(self, _db: Any, user: FakeUser) -> tuple[Any, bool]:
        return SimpleNamespace(hasn_id='h_p0_owner', name=user.nickname), True

    async def ensure_node(self, _db: Any, _user_id: int, owner_id: str, request: Any) -> Any:
        assert owner_id == 'h_p0_owner'
        assert 'workspace_path' not in request.node.model_dump_json()
        assert request.node.node_id.startswith('n_')
        self.node_id = request.node.node_id
        return SimpleNamespace(node_id=request.node.node_id)

    async def ensure_owner_binding(self, _db: Any, node_id: str, owner_id: str) -> Any:
        return SimpleNamespace(node_id=node_id, owner_id=owner_id, status='active', sync_revision=1)

    async def ensure_default_agent(self, _db: Any, owner_id: str, node_id: str | None) -> tuple[Any, bool]:
        assert node_id is not None
        assert node_id.startswith('n_')
        if self.node_id is not None:
            assert node_id == self.node_id
        return (
            SimpleNamespace(
                hasn_id='a_p0_default',
                owner_id=owner_id,
                name=DEFAULT_AGENT_DISPLAY_NAME,
                star_id=f'{self.owner_star_id}#assistant',
            ),
            True,
        )

    async def consume_pending_intent(self, _db: Any, pending_intent_id: str, owner_id: str, agent_hasn_id: str) -> bool:
        assert (pending_intent_id, owner_id, agent_hasn_id) == ('pi_p0_real', 'h_p0_owner', 'a_p0_default')
        return True

    async def get_sandbox_summary(self, _db: Any, owner_id: str) -> SandboxSummary | None:
        assert owner_id == 'h_p0_owner'
        return SandboxSummary(sandbox_id='sb_p0_owner', status='active', base_url=None)

    async def get_sync_feed_head(self, _db: Any, owner_id: str) -> int:
        assert owner_id == 'h_p0_owner'
        return 0


@dataclass
class InMemorySyncGateway:
    reports: list[dict[str, Any]] = field(default_factory=list)
    run_summaries: dict[str, dict[str, Any]] = field(default_factory=dict)
    client_events: list[Any] = field(default_factory=list)
    sync_events: list[Any] = field(default_factory=list)
    owner_user_ids: dict[str, int] = field(default_factory=lambda: {'h_p0_owner': 7})
    namespace_revisions: dict[tuple[str, str, str], dict[str, Any]] = field(default_factory=dict)
    task_records: dict[str, dict[str, Any]] = field(default_factory=dict)
    assignments: dict[str, dict[str, Any]] = field(default_factory=dict)
    inbox_event_ids: set[tuple[str, str, str]] = field(default_factory=set)
    sessions: list[dict[str, Any]] = field(default_factory=list)
    session_events: list[dict[str, Any]] = field(default_factory=list)
    session_artifacts: list[dict[str, Any]] = field(default_factory=list)

    async def owns_owner(self, _db: Any, *, owner_id: str, user_id: int) -> bool:
        return self.owner_user_ids.get(owner_id) == user_id

    async def save_session(self, _db: Any, session: dict[str, Any]) -> None:
        self.sessions.append(session)

    async def save_session_event(self, _db: Any, event: dict[str, Any]) -> None:
        self.session_events.append(event)

    async def save_session_artifact(self, _db: Any, artifact: dict[str, Any]) -> None:
        self.session_artifacts.append(artifact)

    async def existing_client_event_revision(
        self, _db: Any, *, owner_id: str, node_id: str, client_event_id: str
    ) -> int | None:
        if (owner_id, node_id, client_event_id) not in self.inbox_event_ids:
            return None
        return self._latest_revision()

    async def emit_memory_event(
        self,
        _db: Any,
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
        revision = len(self.sync_events) + 1
        event_id = f'se_memory_{revision}'
        return revision, event_id

    async def save_runtime_report(self, _db: Any, report: dict[str, Any]) -> None:
        self.reports.append(report)
        assignment = _fake_assignment_from_runtime_report(report)
        for task_id, task in list(self.task_records.items()):
            if task.get('owner_id') != report['owner_id'] or task.get('agent_id') != report['agent_hasn_id']:
                continue
            previous = self.assignments.get(task_id)
            old_node_id = str((previous or {}).get('executor_node_id') or '')
            if previous == assignment:
                continue
            self.assignments[task_id] = dict(assignment)
            self._append_event(
                event_type='task.assignment_updated',
                payload={
                    **task,
                    'task_uuid': task_id,
                    'executor_kind': assignment['executor_kind'],
                    'executor_policy': assignment['executor_kind'],
                    'executor_node_id': assignment['executor_node_id'],
                    'binding_id': assignment['binding_id'],
                    'assignment_state': assignment['assignment_state'],
                    'previous_executor_node_id': old_node_id or None,
                    'visible_node_ids': [assignment['executor_node_id']] if assignment['executor_node_id'] else [],
                },
            )
            if old_node_id and old_node_id != assignment['executor_node_id']:
                self._append_event(
                    event_type='task.updated',
                    payload={
                        **task,
                        'state': 'waiting_for_runtime',
                        'executor_policy': assignment['executor_kind'],
                        'executor_node_id': assignment['executor_node_id'],
                        'assignment_state': assignment['assignment_state'],
                        'visible_node_ids': [old_node_id],
                    },
                )
            if assignment['executor_node_id']:
                self._append_event(
                    event_type='task.updated',
                    payload={
                        **task,
                        'executor_policy': assignment['executor_kind'],
                        'executor_node_id': assignment['executor_node_id'],
                        'assignment_state': assignment['assignment_state'],
                        'visible_node_ids': [assignment['executor_node_id']],
                    },
                )

    async def pull_events(self, _db: Any, *, owner_id: str, after_revision: int, limit: int) -> list[Any]:
        from backend.app.hasn.schema.hasn_sync import SyncEventRecord

        events = [
            event
            for event in self.sync_events
            if event.payload.get('owner_id', owner_id) == owner_id and event.revision > after_revision
            and not event.event_type.startswith('task.')
            and event.event_type != 'task_run.summary_reported'
        ]
        if self.reports and not events:
            events.append(SyncEventRecord(
                event_id='se_runtime_reported',
                event_type='runtime.reported',
                revision=max(after_revision + 1, 1),
                created_at=datetime(2026, 5, 1, tzinfo=timezone.utc),
                payload={'owner_id': owner_id, 'reports': len(self.reports), 'limit': limit},
            ))
        return events[:limit]

    async def pull_memory_events(
        self, _db: Any, *, owner_id: str, selections: list[Any], limit: int
    ) -> list[Any]:
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

    async def save_client_event(self, _db: Any, *, owner_id: str, node_id: str, event: Any) -> int | None:
        from backend.app.hasn.schema.hasn_sync import SyncEventRecord

        if not event.event_type.startswith('memory.'):
            self.client_events.append((owner_id, node_id, event))
            return None
        sync_scope_kind = str(event.payload['sync_scope_kind'])
        sync_scope_id = str(event.payload['sync_scope_id'])
        namespace = str(event.payload['namespace'])
        revision_key = (sync_scope_kind, sync_scope_id, namespace)
        previous = self.namespace_revisions.get(revision_key)
        namespace_revision = int(previous['revision']) + 1 if previous else 1
        revision = len(self.sync_events) + 1
        event_id = f'se_memory_{revision}'
        self.sync_events.append(SyncEventRecord(
            event_id=event_id,
            event_type=event.event_type,
            revision=revision,
            created_at=datetime(2026, 5, 1, 0, revision, tzinfo=timezone.utc),
            payload={
                **event.payload,
                'client_event_id': event.client_event_id,
                'node_id': node_id,
                'namespace_revision': namespace_revision,
            },
        ))
        self.namespace_revisions[revision_key] = {'revision': namespace_revision, 'last_event_id': event_id}
        self.client_events.append((owner_id, node_id, event))
        return revision

    async def save_task_event(self, _db: Any, *, owner_id: str, node_id: str, event: Any) -> int | None:
        from backend.app.hasn.service.hasn_sync_service import TaskSyncConflictError

        if (owner_id, node_id, event.client_event_id) in self.inbox_event_ids:
            return self._latest_revision()

        self.inbox_event_ids.add((owner_id, node_id, event.client_event_id))
        payload = dict(event.payload.get('task') if isinstance(event.payload.get('task'), dict) else event.payload)
        task_id = str(payload.get('task_id') or event.dedupe_key or event.client_event_id)
        existing = self.task_records.get(task_id)
        if existing is not None:
            if event.event_type != 'task.deleted' and existing.get('state') == 'deleted':
                raise TaskSyncConflictError
            base_revision = payload.get('base_revision')
            if base_revision is not None and int(base_revision) < int(existing.get('task_revision', 0)):
                raise TaskSyncConflictError
        payload['task_id'] = task_id
        payload['owner_id'] = owner_id
        if event.event_type == 'task.deleted':
            payload.setdefault('state', 'deleted')
            payload.setdefault('deleted_at', payload.get('updated_at'))
            updated_record = dict(existing or {})
            updated_record.update(payload)
            updated_record['task_revision'] = int(updated_record.get('task_revision', 0)) + 1
            self.task_records[task_id] = updated_record
        else:
            previous_revision = int(existing.get('task_revision', 0)) if existing else 0
            payload['task_revision'] = int(payload.get('base_revision') or previous_revision) + 1
            self.task_records[task_id] = payload
        self.assignments[task_id] = {
            'executor_kind': str(self.task_records[task_id].get('executor_policy') or 'local_node'),
            'executor_node_id': str(self.task_records[task_id].get('executor_node_id') or node_id),
            'binding_id': self.task_records[task_id].get('binding_id'),
            'assignment_state': 'unresolved' if self.task_records[task_id].get('state') == 'deleted' else 'assigned',
        }

        revision = self._append_event(
            event_type=event.event_type,
            payload={
                **self.task_records[task_id],
                'client_event_id': event.client_event_id,
                'node_id': node_id,
            },
        )
        self.client_events.append((owner_id, node_id, event))
        return revision

    async def pull_task_events(
        self, _db: Any, *, owner_id: str, node_id: str | None, after_revision: int, limit: int
    ) -> list[Any]:
        return [
            event
            for event in self.sync_events
            if (event.event_type.startswith('task.') or event.event_type == 'task_run.summary_reported')
            and event.payload.get('owner_id') == owner_id
            and event.revision > after_revision
            and self._task_event_visible_to_node(event, node_id)
        ][:limit]

    def _task_event_visible_to_node(self, event: Any, node_id: str | None) -> bool:
        if not node_id or event.event_type == 'task_run.summary_reported':
            return True
        visible_node_ids = event.payload.get('visible_node_ids')
        if isinstance(visible_node_ids, list):
            return node_id in {str(item) for item in visible_node_ids}
        task_id = str(event.payload.get('task_uuid') or event.payload.get('task_id') or '')
        assignment = self.assignments.get(task_id)
        if assignment is not None:
            return assignment.get('assignment_state') == 'assigned' and assignment.get('executor_node_id') == node_id
        return event.payload.get('node_id') == node_id or not event.payload.get('node_id')

    async def save_task_run_summary(
        self,
        _db: Any,
        *,
        owner_id: str,
        agent_hasn_id: str,
        summary: dict[str, Any],
    ) -> dict[str, Any]:
        task_uuid = str(summary.get('task_uuid') or summary.get('task_id') or '')
        task = self.task_records.get(task_uuid)
        if task is not None and (task.get('owner_id') != owner_id or task.get('agent_id') != agent_hasn_id):
            raise PermissionError('agent cannot report this task run')
        dedupe_key = str(summary.get('dedupe_key') or summary.get('run_uuid') or summary.get('run_id'))
        stored = {
            **summary,
            'run_uuid': str(summary.get('run_uuid') or summary.get('run_id') or summary.get('task_run_id')),
            'task_uuid': task_uuid,
            'owner_id': owner_id,
            'agent_id': agent_hasn_id,
            'dedupe_key': dedupe_key,
        }
        if dedupe_key not in self.run_summaries:
            self.run_summaries[dedupe_key] = stored
            self._append_event(
                event_type='task_run.summary_reported',
                payload={
                    'owner_id': owner_id,
                    'agent_id': agent_hasn_id,
                    'task_id': task_uuid,
                    'task_uuid': task_uuid,
                    'run_uuid': stored['run_uuid'],
                    'dedupe_key': dedupe_key,
                    'status': stored.get('status'),
                    'output_summary': stored.get('output_summary'),
                    'error': stored.get('error'),
                    'deep_link': stored.get('deep_link'),
                },
            )
        return self.run_summaries[dedupe_key]

    def _append_event(self, *, event_type: str, payload: dict[str, Any]) -> int:
        from backend.app.hasn.schema.hasn_sync import SyncEventRecord

        revision = self._latest_revision() + 1
        self.sync_events.append(SyncEventRecord(
            event_id=f'se_task_{revision}',
            event_type=event_type,
            revision=revision,
            created_at=datetime(2026, 5, 1, 0, revision, tzinfo=timezone.utc),
            payload=payload,
        ))
        return revision

    def _latest_revision(self) -> int:
        return max((event.revision for event in self.sync_events), default=0)


def _fake_assignment_from_runtime_report(report: dict[str, Any]) -> dict[str, Any]:
    summary = dict(report.get('summary_json') or {})
    dispatchable = (
        report.get('runtime_status') == 'online'
        and report.get('adapter_registered', True)
        and report.get('handle_available', True)
        and report.get('node_id')
    )
    if not dispatchable:
        return {
            'executor_kind': 'unresolved',
            'executor_node_id': '',
            'binding_id': report.get('binding_id'),
            'assignment_state': 'unresolved',
        }
    is_cloud = bool(summary.get('cloud_runtime_host')) or summary.get('runtime_host') == 'cloud'
    return {
        'executor_kind': 'cloud_runtime_host' if is_cloud else 'local_node',
        'executor_node_id': report['node_id'],
        'binding_id': report.get('binding_id'),
        'assignment_state': 'assigned',
    }


def make_app(monkeypatch: pytest.MonkeyPatch) -> FastAPI:
    app = FastAPI()
    app.add_middleware(ContextMiddleware, plugins=[RequestIdPlugin(validate=True)])
    register_exception(app)
    app.include_router(onboarding_api.router, prefix='/api/v1/hasn')
    app.include_router(workspace_api.router, prefix='/api/v1/hasn')
    app.include_router(knowledge_api.router, prefix='/api/v1/hasn/app')
    app.include_router(skill_bundle_api.router, prefix='/api/v1/hasn/app/hasn/skill/bundles')
    app.include_router(task_api.router, prefix='/api/v1/hasn-task/app')
    app.include_router(task_run_api.router, prefix='/api/v1/hasn-task/app')
    app.include_router(task_sync_api.router, prefix='/api/v1/hasn-task/app')
    app.include_router(task_sessions_api.router, prefix='/api/v1/hasn/app')
    app.include_router(community_api.router, prefix='/api/v1/community/app')
    app.include_router(task_sessions_api.work_sessions_router, prefix='/api/v1/hasn')
    app.include_router(sync_api.router, prefix='/api/v1/hasn')
    app.include_router(ai_native_api.apps_router, prefix='/api/v1/ai-native/apps')
    app.include_router(ai_native_api.runtime_router, prefix='/api/v1/ai-native/runtime')
    app.include_router(ai_native_api.audit_router, prefix='/api/v1/ai-native/audit')
    app.include_router(mcp_router, tags=['MCP'])
    app.add_api_route(
        '/api/v1/hasn/app/users/me/knowledge-credentials',
        fake_cloud_current_knowledge_credentials,
        methods=['GET'],
    )

    fake_db_instance = FakeDb()

    async def fake_db():
        yield fake_db_instance

    class FakeAsyncDbSession:
        async def __aenter__(self) -> FakeDb:
            return fake_db_instance

        async def __aexit__(self, exc_type: Any, exc: Any, tb: Any) -> bool:
            return False

    app.dependency_overrides[get_db] = fake_db
    app.dependency_overrides[get_db_transaction] = fake_db
    jwt_override = _fake_jwt_user(
        7,
        external_app_permissions={
            'work_sessions': {
                'skill_bundle_ids': ['backend-dev'],
                'toolsets': ['crm'],
                'workflow_ids': ['wf_p0_external'],
            }
        },
    )
    app.dependency_overrides[sync_api.DependsJwtAuth.dependency] = jwt_override
    app.dependency_overrides[onboarding_api.DependsJwtAuth.dependency] = jwt_override
    app.dependency_overrides[task_sessions_api.DependsJwtAuth.dependency] = jwt_override
    app.dependency_overrides[community_api.DependsJwtAuth.dependency] = jwt_override

    async def fake_agent_jwt(request: Request) -> AgentTokenPayload:
        auth = request.headers.get('Authorization', '')
        if not auth.startswith('Bearer '):
            from fastapi import HTTPException

            raise HTTPException(status_code=401, detail='Agent JWT required')
        token = auth.removeprefix('Bearer ').strip()
        if token == 'agent.jwt.task':
            payload = AgentTokenPayload(
                agent_hasn_id=P0_AGENT_ID,
                agent_name=DEFAULT_AGENT_DISPLAY_NAME,
                owner_hasn_id=P0_OWNER_ID,
                owner_user_id=P0_OWNER_USER_ID,
                session_uuid=P0_AGENT_SESSION_UUID,
                expire_time=P0_AGENT_EXPIRE_TIME,
            )
        else:
            from fastapi import HTTPException

            try:
                payload = jwt_decode_agent(token)
            except Exception as exc:
                raise HTTPException(status_code=401, detail='Agent JWT required') from exc
        request.state.agent = payload
        return payload

    app.dependency_overrides[task_sync_api.DependsAgentJwtAuth.dependency] = fake_agent_jwt
    monkeypatch.setattr(onboarding_api, 'jwt_decode', lambda _token: SimpleNamespace(id=7))

    async def fake_token_creator(*_args: Any, **_kwargs: Any) -> SimpleNamespace:
        return SimpleNamespace(access_token='jwt-p0-real-http', session_uuid='session-p0-real-http')

    async def fake_refresh_token_creator(*_args: Any, **_kwargs: Any) -> SimpleNamespace:
        return SimpleNamespace(refresh_token='refresh-p0-real-http')

    redis = FakeRedis()
    from backend.app.hasn.service import ai_native_runtime_gateway as ai_native_gateway_module
    from backend.common.security import agent_jwt as agent_jwt_module

    monkeypatch.setattr(agent_jwt_module, 'redis_client', redis)
    monkeypatch.setattr(ai_native_gateway_module, 'redis_client', redis)
    monkeypatch.setattr('backend.app.mcp.auth.async_db_session', lambda: FakeAsyncDbSession())
    monkeypatch.setattr('backend.database.db.async_db_session', lambda: FakeAsyncDbSession())
    monkeypatch.setattr('backend.app.mcp.server.HasnCloudMcpServer._log_tool_call', _fake_mcp_log_tool_call)
    monkeypatch.setattr(onboarding_service_module, 'create_refresh_token', fake_refresh_token_creator)
    monkeypatch.setattr(agent_jwt_module, 'get_agent_scopes_cached', _fake_agent_scopes_cached)
    # auth.py 用 `from ...agent_jwt import get_agent_scopes_cached` 把名字绑进自身命名空间，
    # 仅 patch 模块属性拦不住它（同 async_db_session 上面那两行）。D3 现查走 auth.py 的引用，
    # 不补这行会落到真 get_agent_scopes_from_db → 参数化 db.execute(stmt, params) → FakeDb 只收一参炸。
    monkeypatch.setattr('backend.app.mcp.auth.get_agent_scopes_cached', _fake_agent_scopes_cached)
    # RF-CLOUD：knowledge.search 不再是可调用 cloud 工具（已下沉本地 RF-MCP）。AI-Native
    # runtime 的工具调用 + 审计真实 HTTP 覆盖改用 community.get_post（仍是云端 gateway_internal 工具）。
    monkeypatch.setattr(
        ai_native_gateway_module.community_service,
        'get_agent_post_resource',
        _fake_agent_post_resource,
    )

    task_store = InMemoryTaskStore()
    skill_bundle_store = InMemorySkillBundleStore()
    task_run_store = InMemoryTaskRunStore(task_store=task_store)

    monkeypatch.setattr(task_api.hasn_task_service, 'get_list_by_owner', task_store.get_list_by_owner)
    monkeypatch.setattr(task_api.hasn_task_service, 'create_with_schedule', task_store.create)
    monkeypatch.setattr(task_api.hasn_task_service, 'get', task_store.get)
    monkeypatch.setattr(task_api.hasn_task_service, 'update', task_store.update)
    monkeypatch.setattr(task_api.hasn_task_service, 'delete', task_store.delete)
    monkeypatch.setattr(skill_bundle_api.hasn_skill_bundle_service, 'get_list_by_owner', skill_bundle_store.get_list_by_owner)
    monkeypatch.setattr(skill_bundle_api.hasn_skill_bundle_service, 'create', skill_bundle_store.create)
    monkeypatch.setattr(skill_bundle_api.hasn_skill_bundle_service, 'get', skill_bundle_store.get)
    monkeypatch.setattr(skill_bundle_api.hasn_skill_bundle_service, 'update', skill_bundle_store.update)
    monkeypatch.setattr(skill_bundle_api.hasn_skill_bundle_service, 'delete', skill_bundle_store.delete)
    monkeypatch.setattr(task_run_api.hasn_task_run_service, 'get_list_by_task_id', task_run_store.get_list_by_task_id)
    monkeypatch.setattr(task_run_api.hasn_task_run_service, 'get', task_run_store.get)
    # 新面无 owner POST/DELETE run 路由（run 由 daemon 同步/Agent 上报产生），流测试直接播种 store
    app.state.task_store = task_store
    app.state.task_run_store = task_run_store

    community_post = {
        'content_type': 'post',
        'post_id': 'post_e2e_share',
        'origin_workspace': {'kind': 'personal', 'id': 'h_p0_owner'},
        'author': {
            'hasn_id': 'h_p0_owner',
            'type': 'human',
            'display_name': 'P0 Dev User',
            'avatar': None,
        },
        'content': '这是一条用于验证社区分享卡片闭环的帖子。',
        'tags': ['卡片消息', 'E2E'],
        'like_count': 12,
        'comment_count': 0,
        'published_time': '2026-05-28T12:00:00Z',
        'is_liked': False,
        'is_collected': False,
    }
    community_article = {
        'article_id': 'article_e2e_share',
        'title': 'E2E 卡片消息文章',
        'summary': '用于验证社区文章分享卡片闭环。',
        'cover_url': None,
        'content': '# E2E 卡片消息文章\n\n这是一篇用于验证文章卡片跳转的正文。',
        'author': {
            'hasn_id': 'h_p0_owner',
            'type': 'human',
            'display_name': 'P0 Dev User',
            'avatar': None,
        },
        'tags': ['卡片消息', '文章'],
        'visibility': 'public',
        'comment_policy': 'all',
        'like_count': 7,
        'comment_count': 0,
        'view_count': 21,
        'published_time': '2026-05-28T12:05:00Z',
        'updated_time': '2026-05-28T12:05:00Z',
        'is_liked': False,
        'is_collected': False,
    }

    async def fake_community_feed(
        _db: Any,
        *,
        user_id: int | None = None,
        feed_type: str = 'recommend',
        tag: str | None = None,
        q: str | None = None,
        cursor: str | None = None,
        limit: int = 20,
    ) -> dict[str, Any]:
        assert user_id == 7
        assert limit >= 1
        return {'items': [community_post], 'next_cursor': None}

    async def fake_community_get_post(
        _db: Any,
        *,
        post_id: str,
        user_id: int,
    ) -> dict[str, Any]:
        assert user_id == 7
        if post_id != community_post['post_id']:
            raise errors.NotFoundError(msg='帖子不存在')
        return community_post

    async def fake_community_get_article(
        _db: Any,
        *,
        user_id: int,
        hasn_id: str,
        article_id: str,
    ) -> dict[str, Any]:
        assert (user_id, hasn_id) == (7, 'h_p0_owner')
        if article_id != community_article['article_id']:
            raise errors.NotFoundError(msg='文章不存在')
        return community_article

    async def fake_community_comments(
        _db: Any,
        *,
        target_type: str,
        target_id: str,
        sort: str,
        user_id: int,
        cursor: str | None,
        limit: int,
    ) -> dict[str, Any]:
        assert user_id == 7
        return {'items': [], 'next_cursor': None}

    monkeypatch.setattr(community_api.community_service, 'get_feed', fake_community_feed)
    monkeypatch.setattr(community_api.community_service, 'get_post', fake_community_get_post)
    monkeypatch.setattr(community_api.community_service, 'get_article', fake_community_get_article)
    monkeypatch.setattr(community_api.community_service, 'get_comments', fake_community_comments)

    phone_auth = HasnPhoneAuthService(
        redis=redis,
        sms=FakeSms(),
        users=FakeUserGateway(),
        code_generator=lambda: '123456',
        token_creator=fake_token_creator,
        llm_credentials=FakeLlmCredentialIssuer(),
        agent_tokens=FakeAgentTokenIssuer(redis),
    )
    monkeypatch.setattr(onboarding_api, 'hasn_phone_auth_service', phone_auth)
    monkeypatch.setattr(onboarding_api, 'redis_client', redis)
    monkeypatch.setattr(
        onboarding_api,
        'hasn_onboarding_service',
        HasnOnboardingService(gateway=FakeOnboardingGateway(), agent_tokens=FakeAgentTokenIssuer(redis)),
    )
    monkeypatch.setattr(workspace_notification_subscriber, 'actions', RecordingWorkspaceNotificationActions())

    sync_gateway = InMemorySyncGateway()
    fake_sync_service = HasnSyncService(gateway=sync_gateway)
    monkeypatch.setattr(sync_api, 'hasn_sync_service', fake_sync_service)
    # 新面 task sync 模块持有自己的 hasn_sync_service 引用，须同步替换
    monkeypatch.setattr(task_sync_api, 'hasn_sync_service', fake_sync_service)

    redis.values[f'{SMS_CODE_PREFIX}:13800138000'] = '123456'
    fake_db_instance.enterprise_memberships[42, 7] = SimpleNamespace(
        enterprise_id=42,
        user_id=7,
        role='admin',
        status='approved',
    )
    fake_db_instance.humans_by_user_id[7] = SimpleNamespace(hasn_id='h_p0_owner', user_id=7)
    fake_db_instance.agents_by_hasn_id[P0_AGENT_ID] = SimpleNamespace(
        hasn_id=P0_AGENT_ID,
        owner_id=P0_OWNER_ID,
        name=DEFAULT_AGENT_DISPLAY_NAME,
        display_name=DEFAULT_AGENT_DISPLAY_NAME,
        agent_name=DEFAULT_AGENT_DISPLAY_NAME,
        status='active',
        star_id='100001#assistant',
    )
    return app


def make_sync_auth_app(
    monkeypatch: pytest.MonkeyPatch,
    user_id: int = 7,
) -> tuple[FastAPI, InMemorySyncGateway]:
    """构造只验证旧 runtime-report 鉴权边界的轻量 HTTP 应用。"""
    app = FastAPI()
    app.add_middleware(
        ContextMiddleware,
        plugins=[RequestIdPlugin(validate=True)],
    )
    register_exception(app)
    app.include_router(sync_api.router, prefix='/api/v1/hasn')

    async def fake_db():
        yield FakeDb()

    app.dependency_overrides[get_db] = fake_db
    app.dependency_overrides[get_db_transaction] = fake_db
    app.dependency_overrides[sync_api.DependsJwtAuth.dependency] = (
        _fake_jwt_user(user_id)
    )

    sync_gateway = InMemorySyncGateway()
    monkeypatch.setattr(
        sync_api,
        'hasn_sync_service',
        HasnSyncService(gateway=sync_gateway),
    )
    return app, sync_gateway


def _fake_jwt_user(user_id: int, *, external_app_permissions: dict[str, Any] | None = None):
    async def fake_jwt(request: Request) -> None:
        request.scope['user'] = SimpleNamespace(
            id=user_id,
            hasn_id=P0_OWNER_ID if user_id == P0_OWNER_USER_ID else None,
        )
        if external_app_permissions is not None:
            request.scope['external_app_permissions'] = external_app_permissions

    return fake_jwt


async def _fake_agent_scopes_cached(_agent_hasn_id: str, _db: Any) -> dict[str, Any]:
    return {'scopes': ['message.read', 'knowledge.read'], 'post_needs_review': True}


async def _fake_agent_post_resource(_db: Any, *, agent: Any, post_id: str) -> dict[str, Any]:
    # AI-Native runtime community.get_post handler 的真实 HTTP E2E 替身（RF-CLOUD 后替代 knowledge.search）。
    assert post_id == 'post_e2e_share'
    return {
        'resource': {
            'type': 'community.post',
            'id': post_id,
            'app_id': 'community',
            'uri': f'hasn://app/community/posts/{post_id}',
        },
        'summary': '卡片摘要',
        'content': '完整正文',
    }


async def _fake_mcp_log_tool_call(*_args: Any, **_kwargs: Any) -> None:
    return None


def test_p0_real_http_flow_covers_auth_onboarding_and_runtime_report(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client = TestClient(make_app(monkeypatch))
    auth = {'Authorization': 'Bearer jwt-p0-real-http'}

    assert client.post('/api/v1/hasn/auth/phone/send_code', json={'phone': '13800138000'}).status_code == 200
    verify = client.post(
        '/api/v1/hasn/auth/phone/verify',
        json={'phone': '13800138000', 'code': '123456', 'pending_intent_id': 'pi_p0_real'},
    )
    assert verify.status_code == 200
    assert verify.json()['access_token'] == 'jwt-p0-real-http'
    assert 'agent_tokens' in verify.json()

    onboarding = client.post(
        '/api/v1/hasn/onboarding/ensure',
        headers=auth,
        json={
            'node': {
                'node_id': 'n_p0_desktop',
                'device_name': 'P0 Desktop',
                'platform': 'macos',
                'client_version': 'p0-real-http',
            },
            'client': {'protocol': 'hasn/0.2', 'supported_extensions': ['sync.pull']},
            'pending_intent_id': 'pi_p0_real',
        },
    )
    assert onboarding.status_code == 200
    assert onboarding.json()['human']['owner_id'] == 'h_p0_owner'
    assert onboarding.json()['default_agent']['hasn_id'] == 'a_p0_default'
    agent_auth = {'Authorization': f"Bearer {onboarding.json()['default_agent']['access_token']}"}
    agent_mcp_auth = {**agent_auth, 'X-HASN-Agent-ID': 'a_p0_default'}

    mcp_tools = client.post('/mcp/tools/list', headers=agent_mcp_auth, json={})
    assert mcp_tools.status_code == 200, mcp_tools.text
    # 渐进披露：云端 tools/list 只回 bootstrap（search + call），长尾经 hasn.cloud.tool.call 直调。
    assert sorted(tool['name'] for tool in mcp_tools.json()['tools']) == [
        'hasn.cloud.tool.call',
        'hasn.cloud.tool.search',
    ]

    mcp_search = client.post(
        '/mcp/tools/call',
        headers=agent_mcp_auth,
        json={'tool_name': 'hasn.cloud.tool.search', 'arguments': {'query': 'sources'}},
    )
    assert mcp_search.status_code == 200, mcp_search.text
    mcp_source_namespaces = {source['namespace'] for source in mcp_search.json()['result']['sources']}
    # 渐进披露下 hasn.cloud.tool.search 回的是颗粒化云端命名空间（community/message/...），
    # 不再是旧的聚合 'hasn.tool'。断言核心云端 App 命名空间确被检索到。
    assert {'hasn.community', 'hasn.message'} <= mcp_source_namespaces

    runtime_report = client.post(
        '/api/v1/hasn/runtime/report',
        headers=auth,
        json={
            'owner_id': 'h_p0_owner',
            'node_id': 'n_p0_desktop',
            'runtime_summaries': [
                {
                    'agent_id': 'a_p0_default',
                    'binding_id': 'bind_p0_default',
                    'runtime_type': 'hermes',
                    'status': 'online',
                    'adapter_registered': True,
                    'handle_available': True,
                    'summary_json': {'capability': 'dispatch'},
                }
            ],
        },
    )
    assert runtime_report.status_code == 200
    assert runtime_report.json()['accepted'] == 1

    capabilities = client.post(
        '/api/v1/ai-native/runtime/capabilities',
        headers=agent_auth,
        json={'workspace': None, 'include_disabled': False, 'trace_id': 'trace-cap-personal'},
    )
    assert capabilities.status_code == 200, capabilities.text
    personal_capabilities = capabilities.json()['data']
    assert personal_capabilities['workspace'] == {
        'kind': 'personal',
        'user_id': 7,
        'enterprise_id': None,
        'workspace_key': 'personal:7',
    }
    assert personal_capabilities['tools'][0]['tool_id'] == 'knowledge.search'

    workspace_switch = client.post(
        '/api/v1/hasn/users/me/workspaces/active',
        headers=agent_auth,
        json={'kind': 'enterprise', 'enterprise_id': 42},
    )
    assert workspace_switch.status_code == 200, workspace_switch.text
    assert workspace_switch.json()['data']['active'] == {'kind': 'enterprise', 'enterprise_id': 42}

    enterprise_capabilities = client.post(
        '/api/v1/ai-native/runtime/capabilities',
        headers=agent_auth,
        json={
            'workspace': {'kind': 'enterprise', 'enterprise_id': 42},
            'include_disabled': False,
            'trace_id': 'trace-cap-enterprise',
        },
    )
    assert enterprise_capabilities.status_code == 200, enterprise_capabilities.text
    assert enterprise_capabilities.json()['data']['workspace'] == {
        'kind': 'enterprise',
        'user_id': None,
        'enterprise_id': 42,
        'workspace_key': 'enterprise:42',
    }

    # RF-CLOUD：runtime 工具调用 + 审计真实 HTTP 覆盖改用 community.get_post（云端 gateway_internal）。
    tool_call = client.post(
        '/api/v1/ai-native/runtime/tools/community/community.get_post/call',
        headers=agent_auth,
        json={
            'workspace': {'kind': 'enterprise', 'enterprise_id': 42},
            'input': {'post_id': 'post_e2e_share'},
            'trace_id': 'trace-tool-enterprise',
        },
    )
    assert tool_call.status_code == 200, tool_call.text
    assert tool_call.json()['data']['decision'] == 'allow'
    assert tool_call.json()['data']['workspace']['workspace_key'] == 'enterprise:42'

    audit = client.get(
        '/api/v1/ai-native/audit',
        params={'app_id': 'community', 'agent_hasn_id': 'a_p0_default', 'trace_id': 'trace-tool-enterprise'},
    )
    assert audit.status_code == 200, audit.text
    assert audit.json()['data']['total'] == 1
    assert audit.json()['data']['items'][0]['tool_id'] == 'community.get_post'

    invalid_input = client.post(
        '/api/v1/ai-native/runtime/tools/community/community.get_post/call',
        headers=agent_auth,
        json={
            'workspace': {'kind': 'enterprise', 'enterprise_id': 42},
            'input': {'post_id': ''},
            'trace_id': 'trace-tool-invalid-input',
        },
    )
    assert invalid_input.status_code == 200, invalid_input.text
    assert invalid_input.json()['data']['decision'] == 'deny'
    # 入参绑定接缝（候选①）后缺必填的 deny 理由细化为 input_invalid:{field}:{reason}
    assert invalid_input.json()['data']['error'] == {'code': '15020', 'message': 'input_invalid:post_id:required'}

    invalid_input_audit = client.get(
        '/api/v1/ai-native/audit',
        params={'app_id': 'community', 'agent_hasn_id': 'a_p0_default', 'trace_id': 'trace-tool-invalid-input'},
    )
    assert invalid_input_audit.status_code == 200, invalid_input_audit.text
    assert invalid_input_audit.json()['data']['total'] == 1
    assert invalid_input_audit.json()['data']['items'][0]['error_code'] == '15020'


def test_sync_pull_rejects_owner_not_bound_to_authenticated_user(monkeypatch: pytest.MonkeyPatch) -> None:
    app, _sync_gateway = make_sync_auth_app(monkeypatch, user_id=7)
    client = TestClient(app)

    response = client.post(
        '/api/v1/hasn/sync/pull',
        headers={'Authorization': 'Bearer jwt-p0-owner-mismatch'},
        json={'owner_id': 'h_other_owner', 'cursor': 'owner:h_other_owner:0'},
    )

    assert response.status_code == 403


def test_sync_push_rejects_owner_not_bound_to_authenticated_user(monkeypatch: pytest.MonkeyPatch) -> None:
    app, sync_gateway = make_sync_auth_app(monkeypatch, user_id=7)
    client = TestClient(app)

    response = client.post(
        '/api/v1/hasn/sync/push',
        headers={'Authorization': 'Bearer jwt-p0-owner-mismatch'},
        json={
            'owner_id': 'h_other_owner',
            'node_id': 'n_p0_desktop',
            'events': [
                {
                    'client_event_id': 'ce_unauthorized',
                    'event_type': 'node.session',
                    'payload': {'status': 'ready'},
                }
            ],
        },
    )

    assert response.status_code == 403
    assert sync_gateway.client_events == []


def test_runtime_report_rejects_owner_not_bound_to_authenticated_user(monkeypatch: pytest.MonkeyPatch) -> None:
    app, sync_gateway = make_sync_auth_app(monkeypatch, user_id=7)
    client = TestClient(app)

    response = client.post(
        '/api/v1/hasn/runtime/report',
        headers={'Authorization': 'Bearer jwt-p0-owner-mismatch'},
        json={
            'owner_id': 'h_other_owner',
            'node_id': 'n_p0_desktop',
            'runtime_summaries': [
                {
                    'agent_id': 'a_other_agent',
                    'binding_id': 'bind_other',
                    'runtime_type': 'hermes',
                    'status': 'online',
                    'summary_json': {'capability': 'dispatch'},
                }
            ],
        },
    )

    assert response.status_code == 403
    assert sync_gateway.reports == []


def test_task_run_summary_requires_agent_jwt_and_is_idempotent(monkeypatch: pytest.MonkeyPatch) -> None:
    client = TestClient(make_app(monkeypatch))
    owner_auth = {'Authorization': 'Bearer jwt-p0-real-http'}
    agent_auth = {'Authorization': 'Bearer agent.jwt.task'}

    owner_response = client.post(
        '/api/v1/hasn-task/app/runs/summary',
        headers=owner_auth,
        json={
            'run_id': 456,
            'task_id': 'task_local_1',
            'agent_id': 'a_p0_default',
            'session_id': 'sess_task_456',
            'status': 'success',
            'output': 'done',
            'dedupe_key': 'work_session_result:sess_task_456:final',
        },
    )
    first = client.post(
        '/api/v1/hasn-task/app/runs/summary',
        headers=agent_auth,
        json={
            'run_id': 456,
            'task_id': 'task_local_1',
            'agent_id': 'a_p0_default',
            'session_id': 'sess_task_456',
            'scheduled_fire_at': 1_779_721_000,
            'status': 'success',
            'output': 'done',
            'deep_link': '/tasks/sessions/sess_task_456',
            'dedupe_key': 'work_session_result:sess_task_456:final',
            'model': 'unknown',
            'token_usage': {'input_tokens': 1, 'output_tokens': 2, 'total_tokens': 3},
            'duration_ms': 1200,
        },
    )
    duplicate = client.post(
        '/api/v1/hasn-task/app/runs/summary',
        headers=agent_auth,
        json={
            'run_id': 456,
            'task_id': 'task_local_1',
            'agent_id': 'a_p0_default',
            'session_id': 'sess_task_456',
            'status': 'success',
            'output': 'done again',
            'dedupe_key': 'work_session_result:sess_task_456:final',
        },
    )
    assert owner_response.status_code == 401
    assert first.status_code == 200, first.text
    assert first.json()['data']['run_uuid'] == '456'
    assert first.json()['data']['status'] == 'success'
    assert duplicate.status_code == 200, duplicate.text
    assert duplicate.json()['data']['output_summary'] == 'done'


def test_task_run_summary_keeps_legacy_run_id_separate_from_task_uuid(monkeypatch: pytest.MonkeyPatch) -> None:
    client = TestClient(make_app(monkeypatch))
    agent_auth = {'Authorization': 'Bearer agent.jwt.task'}

    response = client.post(
        '/api/v1/hasn-task/app/runs/summary',
        headers=agent_auth,
        json={
            'run_id': 456,
            'task_run_id': 456,
            'session_id': 'sess_task_456',
            'status': 'success',
            'output': 'done',
            'dedupe_key': 'work_session_result:sess_task_456:final',
        },
    )
    assert response.status_code == 200, response.text
    assert response.json()['data']['run_uuid'] == '456'
    assert response.json()['data']['task_uuid'] == ''


def test_p0_real_http_flow_covers_task_system_routes(monkeypatch: pytest.MonkeyPatch) -> None:
    app = make_app(monkeypatch)
    client = TestClient(app)
    auth = {'Authorization': 'Bearer jwt-p0-real-http'}

    bundle_create = client.post(
        '/api/v1/hasn/app/hasn/skill/bundles',
        headers=auth,
        json={
            'owner_id': 'h_other_owner',
            'name': 'backend-dev',
            'display_name': '后端开发',
            'description': 'Backend feature work',
            'skill_ids': ['pytest', 'test-driven-development'],
            'instruction': '先跑测试再汇报。',
            'create_time': None,
            'update_time': None,
        },
    )
    assert bundle_create.status_code == 200, bundle_create.text
    bundle_id = bundle_create.json()['data']['id']
    assert bundle_create.json()['data']['owner_id'] == 'h_p0_owner'

    task_create = client.post(
        '/api/v1/hasn-task/app/tasks',
        headers=auth,
        json={
            'owner_id': 'h_other_owner',
            'agent_id': 'a_p0_default',
            'name': '日报任务',
            'description': '生成日报',
            'prompt': '生成日报',
            'skill_bundle_ids': ['backend-dev'],
            'skill_ids': ['pytest'],
            'workflow_id': None,
            'enabled_toolsets': ['terminal'],
            'context_from_task_id': None,
            'schedule_type': 'once',
            'schedule_config': {'run_at': '2026-05-22T09:00:00Z'},
            'schedule_display': '一次性执行',
            'enabled': True,
            'state': 'scheduled',
            'next_run_at': None,
            'last_run_at': None,
            'last_status': None,
            'last_error': None,
            'run_count': 0,
            'repeat_times': None,
            'repeat_completed': 0,
            'create_time': None,
            'update_time': None,
            'created_by': 'tester',
        },
    )
    assert task_create.status_code == 200, task_create.text
    task_id = task_create.json()['data']['task_id']

    tasks = client.get('/api/v1/hasn-task/app/tasks', headers=auth)
    assert tasks.status_code == 200, tasks.text
    assert tasks.json()['data']['total'] == 1
    assert tasks.json()['data']['items'][0]['id'] == task_id

    task_detail = client.get(f'/api/v1/hasn-task/app/tasks/{task_id}', headers=auth)
    assert task_detail.status_code == 200, task_detail.text
    assert task_detail.json()['data']['name'] == '日报任务'
    assert task_detail.json()['data']['owner_id'] == 'h_p0_owner'
    assert task_detail.json()['data']['skill_bundle_ids'] == ['backend-dev']

    task_update = client.put(
        f'/api/v1/hasn-task/app/tasks/{task_id}',
        headers=auth,
        json={
            'owner_id': 'h_other_owner',
            'agent_id': 'a_p0_default',
            'name': '日报任务 v2',
            'description': '生成日报并整理',
            'prompt': '生成日报并整理',
            'skill_bundle_ids': ['backend-dev'],
            'skill_ids': ['pytest'],
            'workflow_id': None,
            'enabled_toolsets': ['terminal'],
            'context_from_task_id': None,
            'schedule_type': 'once',
            'schedule_config': {'run_at': '2026-05-22T09:00:00Z'},
            'schedule_display': '一次性执行',
            'enabled': False,
            'state': 'paused',
            'next_run_at': None,
            'last_run_at': None,
            'last_status': None,
            'last_error': None,
            'run_count': 0,
            'repeat_times': None,
            'repeat_completed': 0,
            'create_time': None,
            'update_time': None,
            'created_by': 'tester',
        },
    )
    assert task_update.status_code == 200, task_update.text
    assert task_update.json()['data'] == {'updated': 1}

    # 新面不提供 owner POST run（run 由 daemon 同步/Agent 上报产生），直接播种 store 验读路径
    run_store = app.state.task_run_store
    task_run_id = run_store.next_id
    run_store.records[task_run_id] = TaskRunRecord(
        id=task_run_id,
        task_id=task_id,
        agent_id='a_p0_default',
        session_id='sess_task_1',
        source_conversation_id=None,
        source_message_id=None,
        runtime_node_id='n_p0_desktop',
        status='pending',
        started_at=None,
        finished_at=None,
        duration_ms=None,
        prompt_snapshot='Skill bundles: backend-dev\n\n生成日报',
        output=None,
        error=None,
        model=None,
        token_usage=None,
        create_time=_fixture_time(),
        created_time=_fixture_time(),
        updated_time=None,
    )
    run_store.next_id += 1

    task_runs = client.get(f'/api/v1/hasn-task/app/tasks/{task_id}/runs', headers=auth)
    assert task_runs.status_code == 200, task_runs.text
    assert task_runs.json()['data']['total'] == 1
    assert task_runs.json()['data']['items'][0]['task_id'] == task_id

    task_run_detail = client.get(f'/api/v1/hasn-task/app/runs/{task_run_id}', headers=auth)
    assert task_run_detail.status_code == 200, task_run_detail.text
    assert task_run_detail.json()['data']['session_id'] == 'sess_task_1'

    external_launch = client.post(
        '/api/v1/hasn/work-sessions',
        headers=auth,
        json={
            'external_app_id': 'crm',
            'external_trace_id': 'trace-p0',
            'agent_id': 'a_p0_default',
            'title': '外部客户整理',
            'task_description': '整理 P0 客户清单',
            'skill_bundle_ids': ['backend-dev'],
            'enabled_toolsets': {'crm': True},
            'workflow': {'workflow_id': 'wf_p0_external', 'workflow_run_id': 'wfr_p0_external'},
            'projection_policy': {'project_summary_to_owner_conversation': True},
        },
    )
    assert external_launch.status_code == 200, external_launch.text
    external_session = external_launch.json()['data']
    assert external_session['launch_spec']['origin_type'] == 'external_app'
    assert external_session['launch_spec']['source'] == {
        'external_app_id': 'crm',
        'external_trace_id': 'trace-p0',
    }
    assert external_session['launch_spec']['completion_policy']['mode'] == 'external_controlled'

    external_detail = client.get(
        f"/api/v1/hasn/work-sessions/{external_session['session_id']}",
        headers=auth,
    )
    assert external_detail.status_code == 200, external_detail.text
    assert external_detail.json()['data']['agent_id'] == 'a_p0_default'
    assert external_detail.json()['data']['summary']['external_trace_id'] == 'trace-p0'

    external_complete = client.post(
        f"/api/v1/hasn/work-sessions/{external_session['session_id']}/complete",
        headers=auth,
        json={'summary': '外部客户清单完成', 'reason': 'external_app_done'},
    )
    assert external_complete.status_code == 200, external_complete.text
    assert external_complete.json()['data'] == {
        'accepted': True,
        'session_id': external_session['session_id'],
        'control': 'complete',
    }

    bundle_detail = client.get(f'/api/v1/hasn/app/hasn/skill/bundles/{bundle_id}', headers=auth)
    assert bundle_detail.status_code == 200, bundle_detail.text
    assert bundle_detail.json()['data']['skill_ids'] == ['pytest', 'test-driven-development']

    bundle_update = client.put(
        f'/api/v1/hasn/app/hasn/skill/bundles/{bundle_id}',
        headers=auth,
        json={
            'owner_id': 'h_other_owner',
            'name': 'backend-dev',
            'display_name': '后端开发',
            'description': 'Backend feature work updated',
            'skill_ids': ['pytest'],
            'instruction': '先跑测试再汇报。',
            'create_time': None,
            'update_time': None,
        },
    )
    assert bundle_update.status_code == 200, bundle_update.text
    assert bundle_update.json()['data'] is None

    bundle_list = client.get('/api/v1/hasn/app/hasn/skill/bundles', headers=auth)
    assert bundle_list.status_code == 200, bundle_list.text
    assert bundle_list.json()['data']['items'][0]['description'] == 'Backend feature work updated'

    assert client.delete(f'/api/v1/hasn-task/app/tasks/{task_id}', headers=auth).status_code == 200
    assert client.delete(f'/api/v1/hasn/app/hasn/skill/bundles/{bundle_id}', headers=auth).status_code == 200

    task_list_after_delete = client.get('/api/v1/hasn-task/app/tasks', headers=auth)
    assert task_list_after_delete.status_code == 200
    assert task_list_after_delete.json()['data']['total'] == 0
