from __future__ import annotations

import asyncio
import json

from types import SimpleNamespace
from typing import TYPE_CHECKING, Any
from uuid import UUID

import pytest

from fastapi import FastAPI, HTTPException, Request
from fastapi.testclient import TestClient
from starlette.status import HTTP_401_UNAUTHORIZED
from starlette_context.middleware import ContextMiddleware
from starlette_context.plugins import RequestIdPlugin

from backend.app.hasn.api.v1.app import hasn_task_sessions as sessions_api
from backend.app.hasn.service import hasn_sessions_service as service_module
from backend.common.exception import errors
from backend.common.exception.exception_handler import register_exception
from backend.common.security.jwt import DependsJwtAuth
from backend.database.db import get_db, get_db_transaction

if TYPE_CHECKING:
    from collections.abc import AsyncGenerator

OWNER_ID = 'h_owner'
AGENT_ID = 'a_agent'
SESSION_ID = 'sess_task_001'


class FakeScalarResult:
    def __init__(self, value: Any) -> None:
        self.value = value

    def scalar_one_or_none(self) -> Any:
        return self.value


class FakeMappingResult:
    def __init__(self, value: dict[str, Any] | None) -> None:
        self.value = value

    def mappings(self) -> FakeMappingResult:
        return self

    def first(self) -> dict[str, Any] | None:
        return self.value

    def one(self) -> dict[str, Any]:
        assert self.value is not None
        return self.value


class FakeDb:
    def __init__(self, results: list[Any] | None = None) -> None:
        self.results = results or []
        self.executed: list[tuple[Any, Any]] = []
        self.added: list[Any] = []
        self.flushed = False

    async def execute(self, stmt: Any, params: Any = None) -> Any:
        self.executed.append((stmt, params))
        if not self.results:
            raise AssertionError(f'unexpected query: {stmt}')
        return self.results.pop(0)

    def add(self, row: Any) -> None:
        self.added.append(row)

    async def flush(self) -> None:
        self.flushed = True


async def _fake_db() -> AsyncGenerator[SimpleNamespace, None]:
    await asyncio.sleep(0)
    yield SimpleNamespace()


def _fake_jwt_auth(request: Request) -> str:
    auth = request.headers.get('Authorization') or request.headers.get('authorization')
    if not auth or not auth.lower().startswith('bearer '):
        raise HTTPException(status_code=HTTP_401_UNAUTHORIZED, detail='Token 无效')
    request.scope['user'] = SimpleNamespace(id=7, hasn_id=OWNER_ID)
    request.scope['auth'] = ['authenticated']
    return auth[7:]


def _fake_jwt_auth_with_external_permissions(permissions: dict[str, Any]):
    def fake_jwt_auth(request: Request) -> str:
        token = _fake_jwt_auth(request)
        request.scope['external_app_permissions'] = {'work_sessions': permissions}
        return token

    return fake_jwt_auth


@pytest.fixture
def task_sessions_app() -> FastAPI:
    app = FastAPI()
    app.add_middleware(ContextMiddleware, plugins=[RequestIdPlugin(validate=True)])
    register_exception(app)
    app.include_router(sessions_api.router, prefix='/api/v1/hasn/app')
    app.include_router(sessions_api.work_sessions_router, prefix='/api/v1/hasn')
    app.dependency_overrides[DependsJwtAuth.dependency] = _fake_jwt_auth
    app.dependency_overrides[get_db] = _fake_db
    app.dependency_overrides[get_db_transaction] = _fake_db
    return app


def _session_payload(**overrides: Any) -> dict[str, Any]:
    payload = {
        'session_id': SESSION_ID,
        'conversation_id': None,
        'owner_id': OWNER_ID,
        'hasn_id': AGENT_ID,
        'session_kind': 'task',
        'session_scope': 'summary_only',
        'session_status': 'active',
        'origin_type': 'task_run',
        'origin_ref': 'task_run:123',
        'title': '生成日报',
        'summary_checkpoint_json': {'summary': 'pending'},
    }
    payload.update(overrides)
    return payload


def _projection_payload(**overrides: Any) -> dict[str, Any]:
    payload = {
        'summary': '已生成客户优先级和跟进建议。',
        'status': 'success',
        'completion_reason': 'auto_on_final',
        'deep_link': f'hasn://tasks/sessions/{SESSION_ID}',
        'task_id': 123,
        'task_run_id': 456,
    }
    payload.update(overrides)
    return payload


def _external_launch_payload(**overrides: Any) -> dict[str, Any]:
    payload = {
        'external_app_id': 'crm',
        'external_trace_id': 'trace-123',
        'agent_id': AGENT_ID,
        'title': '客户计划',
        'task_description': '整理高优先级客户',
    }
    payload.update(overrides)
    return payload


def _owned_agent_db() -> FakeDb:
    return FakeDb(
        results=[
            FakeScalarResult(SimpleNamespace(hasn_id=AGENT_ID, owner_id=OWNER_ID, status='active', deleted_at=None)),
            FakeScalarResult(None),
        ]
    )


def _override_transaction_db(app: FastAPI, db: FakeDb) -> None:
    async def fake_db_override() -> AsyncGenerator[FakeDb, None]:
        await asyncio.sleep(0)
        yield db

    app.dependency_overrides[get_db_transaction] = fake_db_override


def _override_external_permissions(app: FastAPI, permissions: dict[str, Any]) -> None:
    app.dependency_overrides[DependsJwtAuth.dependency] = _fake_jwt_auth_with_external_permissions(permissions)


def test_session_upsert_rejects_request_owner_mismatch(task_sessions_app: FastAPI) -> None:
    with TestClient(task_sessions_app) as client:
        response = client.post(
            '/api/v1/hasn/app/sessions/upsert',
            headers={'Authorization': 'Bearer fake-token'},
            json=_session_payload(owner_id='h_other'),
        )

    assert response.status_code == 403, response.text
    assert 'owner' in response.json()['msg'].lower() or '无权' in response.json()['msg']


def test_external_app_work_session_launch_creates_summary_only_header(
    task_sessions_app: FastAPI,
) -> None:
    db = _owned_agent_db()
    _override_transaction_db(task_sessions_app, db)
    _override_external_permissions(
        task_sessions_app,
        {
            'skill_ids': ['crm.search'],
            'skill_bundle_ids': ['customer-success'],
            'toolsets': ['crm'],
            'workflow_ids': ['wf_customer'],
            'allow_system_prompt': True,
        },
    )

    with TestClient(task_sessions_app) as client:
        response = client.post(
            '/api/v1/hasn/work-sessions',
            headers={'Authorization': 'Bearer fake-token'},
            json=_external_launch_payload(
                system_prompt='只输出行动项',
                skill_ids=['crm.search'],
                skill_bundle_ids=['customer-success'],
                enabled_toolsets={'crm': True},
                workflow={'workflow_id': 'wf_customer', 'workflow_run_id': 'wfr_ext'},
                projection_policy={'project_summary_to_owner_conversation': True},
            ),
        )

    assert response.status_code == 200, response.text
    data = response.json()['data']
    assert data['accepted'] is True
    assert data['session_id'].startswith('sess_ext_')
    assert data['deep_link'] == f"/tasks/sessions/{data['session_id']}"
    assert data['launch_spec'] == {
        'session_id': data['session_id'],
        'owner_id': OWNER_ID,
        'agent_id': AGENT_ID,
        'origin_type': 'external_app',
        'origin_ref': 'crm:trace-123',
        'title': '客户计划',
        'task_description': '整理高优先级客户',
        'system_prompt': '只输出行动项',
        'skill_ids': ['crm.search'],
        'skill_bundle_ids': ['customer-success'],
        'enabled_toolsets': {'crm': True},
        'workflow': {'workflow_id': 'wf_customer', 'workflow_run_id': 'wfr_ext'},
        'source': {'external_app_id': 'crm', 'external_trace_id': 'trace-123'},
        'projection_policy': {'project_summary_to_owner_conversation': True},
        'completion_policy': {
            'mode': 'external_controlled',
            'project_on_complete': True,
            'require_user_confirmation': True,
        },
    }
    session = db.added[0]
    assert session.owner_id == OWNER_ID
    assert session.hasn_id == AGENT_ID
    assert session.conversation_id is None
    assert session.session_kind == 'task'
    assert session.session_scope == 'summary_only'
    assert session.origin_type == 'external_app'
    assert session.origin_ref == 'crm:trace-123'
    assert session.summary_checkpoint_json == {
        'summary': '整理高优先级客户',
        'external_app_id': 'crm',
        'external_trace_id': 'trace-123',
        'deep_link': data['deep_link'],
    }
    assert 'system_prompt' not in json.dumps(session.summary_checkpoint_json)


def test_external_app_work_session_launch_rejects_unauthorized_skill_id(task_sessions_app: FastAPI) -> None:
    db = _owned_agent_db()
    _override_transaction_db(task_sessions_app, db)
    _override_external_permissions(task_sessions_app, {'skill_ids': ['crm.search']})

    with TestClient(task_sessions_app) as client:
        response = client.post(
            '/api/v1/hasn/work-sessions',
            headers={'Authorization': 'Bearer fake-token'},
            json=_external_launch_payload(skill_ids=['private.export']),
        )

    assert response.status_code == 403, response.text
    assert 'skill' in response.json()['msg'].lower()
    assert db.added == []


def test_external_app_work_session_launch_rejects_unauthorized_skill_bundle(task_sessions_app: FastAPI) -> None:
    db = _owned_agent_db()
    _override_transaction_db(task_sessions_app, db)
    _override_external_permissions(task_sessions_app, {'skill_bundle_ids': ['customer-success']})

    with TestClient(task_sessions_app) as client:
        response = client.post(
            '/api/v1/hasn/work-sessions',
            headers={'Authorization': 'Bearer fake-token'},
            json=_external_launch_payload(skill_bundle_ids=['secret-bundle']),
        )

    assert response.status_code == 403, response.text
    assert 'skill_bundle' in response.json()['msg'].lower()
    assert db.added == []


def test_external_app_work_session_launch_rejects_unauthorized_toolset(task_sessions_app: FastAPI) -> None:
    db = _owned_agent_db()
    _override_transaction_db(task_sessions_app, db)
    _override_external_permissions(task_sessions_app, {'toolsets': ['crm']})

    with TestClient(task_sessions_app) as client:
        response = client.post(
            '/api/v1/hasn/work-sessions',
            headers={'Authorization': 'Bearer fake-token'},
            json=_external_launch_payload(enabled_toolsets={'terminal': True}),
        )

    assert response.status_code == 403, response.text
    assert 'toolset' in response.json()['msg'].lower()
    assert db.added == []


def test_external_app_work_session_launch_rejects_unauthorized_workflow(task_sessions_app: FastAPI) -> None:
    db = _owned_agent_db()
    _override_transaction_db(task_sessions_app, db)
    _override_external_permissions(task_sessions_app, {'workflow_ids': ['wf_customer']})

    with TestClient(task_sessions_app) as client:
        response = client.post(
            '/api/v1/hasn/work-sessions',
            headers={'Authorization': 'Bearer fake-token'},
            json=_external_launch_payload(workflow={'workflow_id': 'wf_secret'}),
        )

    assert response.status_code == 403, response.text
    assert 'workflow' in response.json()['msg'].lower()
    assert db.added == []


def test_external_app_work_session_launch_rejects_system_prompt_without_permission(task_sessions_app: FastAPI) -> None:
    db = _owned_agent_db()
    _override_transaction_db(task_sessions_app, db)
    _override_external_permissions(task_sessions_app, {})

    with TestClient(task_sessions_app) as client:
        response = client.post(
            '/api/v1/hasn/work-sessions',
            headers={'Authorization': 'Bearer fake-token'},
            json=_external_launch_payload(system_prompt='覆盖默认安全策略'),
        )

    assert response.status_code == 403, response.text
    assert 'system_prompt' in response.json()['msg'].lower()
    assert db.added == []


@pytest.mark.asyncio
async def test_upsert_rejects_local_only_cloud_sessions() -> None:
    db = FakeDb()

    with pytest.raises(errors.RequestError):
        await service_module.hasn_sessions_service.upsert(
            db=db,
            owner_id=OWNER_ID,
            session_data=_session_payload(session_scope='local_only'),
        )


@pytest.mark.asyncio
async def test_get_list_by_owner_builds_owner_scoped_filtered_query(monkeypatch: pytest.MonkeyPatch) -> None:
    captured: dict[str, Any] = {}

    async def fake_paging_data(db: Any, select_stmt: Any) -> dict[str, Any]:
        await asyncio.sleep(0)
        captured['sql'] = str(select_stmt.compile(compile_kwargs={'literal_binds': True}))
        return {'items': [], 'total': 0, 'page': 1, 'size': 20, 'total_pages': 0, 'links': {}}

    monkeypatch.setattr(service_module, 'paging_data', fake_paging_data)

    page_data = await service_module.hasn_sessions_service.get_list_by_owner(
        db=SimpleNamespace(),
        owner_id=OWNER_ID,
        session_kind='task,interactive',
        session_scope='summary_only',
        session_status='active',
        hasn_id=AGENT_ID,
        origin_type='task_run',
        origin_ref='task_run:123',
    )

    assert page_data['total'] == 0
    sql = captured['sql']
    assert "hasn_sessions.owner_id = 'h_owner'" in sql
    assert "hasn_sessions.session_kind IN ('task', 'interactive')" in sql
    assert "hasn_sessions.session_scope = 'summary_only'" in sql
    assert "hasn_sessions.session_status = 'active'" in sql
    assert "hasn_sessions.hasn_id = 'a_agent'" in sql
    assert "hasn_sessions.origin_type = 'task_run'" in sql
    assert "hasn_sessions.origin_ref = 'task_run:123'" in sql


@pytest.mark.asyncio
async def test_projection_is_idempotent_and_writes_summary_message(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    conversation_id = UUID('00000000-0000-0000-0000-000000000123')
    session = SimpleNamespace(
        session_id=SESSION_ID,
        owner_id=OWNER_ID,
        hasn_id=AGENT_ID,
        title='生成日报',
        origin_type='task_run',
        origin_ref='task_run:123',
        summary_checkpoint_json={},
        last_message_id=None,
        last_message_at=None,
        updated_time=None,
    )
    db = FakeDb(
        results=[
            FakeScalarResult(session),
            FakeMappingResult(None),
            FakeMappingResult({'id': 987}),
            FakeScalarResult(session),
            FakeMappingResult({'id': 987, 'conversation_id': str(conversation_id)}),
        ]
    )

    async def fake_ensure_conversation(**kwargs: Any) -> SimpleNamespace:
        await asyncio.sleep(0)
        assert kwargs['caller_hasn_id'] == OWNER_ID
        assert kwargs['peer_hasn_id'] == AGENT_ID
        return SimpleNamespace(id=conversation_id)

    monkeypatch.setattr(
        service_module.hasn_conversations_service,
        'ensure_conversation',
        fake_ensure_conversation,
    )

    first = await service_module.hasn_sessions_service.project_work_session_result(
        db=db,
        owner_id=OWNER_ID,
        session_id=SESSION_ID,
        projection_data=_projection_payload(),
    )
    second = await service_module.hasn_sessions_service.project_work_session_result(
        db=db,
        owner_id=OWNER_ID,
        session_id=SESSION_ID,
        projection_data=_projection_payload(),
    )

    assert first == {
        'result_message_id': '987',
        'conversation_id': str(conversation_id),
        'dedupe_key': f'work_session_result:{SESSION_ID}:final',
        'created': True,
    }
    assert second == {
        'result_message_id': '987',
        'conversation_id': str(conversation_id),
        'dedupe_key': f'work_session_result:{SESSION_ID}:final',
        'created': False,
    }

    insert_params = [
        params
        for _stmt, params in db.executed
        if isinstance(params, dict) and params.get('client_message_id') == f'work_session_result:{SESSION_ID}:final'
    ]
    assert len(insert_params) == 1
    content = json.loads(insert_params[0]['content'])
    assert content['schema_version'] == 'hasn.card/0.1'
    assert content['title'] == '工作会话「生成日报」已完成'
    assert content['description'] == '已生成客户优先级和跟进建议。'
    assert content['source'] == {
        'kind': 'task',
        'id': '123',
        'display_name': '任务系统',
        'verified': True,
    }
    assert content['resource']['type'] == 'task_session'
    assert content['resource']['id'] == SESSION_ID
    assert content['resource']['app_id'] == 'tasks'
    assert content['resource']['uri'] == f'hasn://tasks/sessions/{SESSION_ID}'
    assert content['primary_action']['action_id'] == 'open_task_session'
    assert content['primary_action']['kind'] == 'open_uri'
    assert content['primary_action']['uri'] == f'hasn://tasks/sessions/{SESSION_ID}'
    assert content['primary_action']['event'] == {
        'event_type': 'task.summary.opened',
        'payload': {
            'session_id': SESSION_ID,
            'task_id': 123,
            'task_run_id': 456,
        },
    }
    assert {'label': '状态', 'value': 'success'} in content['fields']
    assert {'label': '完成原因', 'value': 'auto_on_final'} in content['fields']
    projection = session.summary_checkpoint_json
    assert projection['deep_link'] == f'hasn://tasks/sessions/{SESSION_ID}'
    assert projection['dedupe_key'] == f'work_session_result:{SESSION_ID}:final'
    assert 'system prompt' not in json.dumps(content).lower()
    assert session.summary_checkpoint_json['result_message_id'] == '987'
    assert db.flushed is True


@pytest.mark.asyncio
async def test_projection_writes_summary_when_cloud_session_absent(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """work-session 只活在 hasn-node 本地 sessions 表、云端无对应行时，投影仍写回主会话。

    回归：此前云端 ``get_by_session_id`` 对缺失 session 抛 404，导致任务结果永远
    落不回主会话（线上 ``projection_failed status=404``）。现在云端不再强依赖云端
    session，靠 projection_data 自包含字段写卡片消息。
    """
    conversation_id = UUID('00000000-0000-0000-0000-000000000456')
    db = FakeDb(
        results=[
            FakeScalarResult(None),  # try_get_by_session_id -> 云端无该 session
            FakeMappingResult(None),  # _find_projection_message -> 无重复
            FakeMappingResult({'id': 988}),  # INSERT ... RETURNING id
        ]
    )

    async def fake_ensure_conversation(**kwargs: Any) -> SimpleNamespace:
        await asyncio.sleep(0)
        assert kwargs['caller_hasn_id'] == OWNER_ID
        assert kwargs['peer_hasn_id'] == AGENT_ID
        return SimpleNamespace(id=conversation_id)

    monkeypatch.setattr(
        service_module.hasn_conversations_service,
        'ensure_conversation',
        fake_ensure_conversation,
    )

    result = await service_module.hasn_sessions_service.project_work_session_result(
        db=db,
        owner_id=OWNER_ID,
        session_id=SESSION_ID,
        # 无云端 session：agent_id / origin / title 必须由 daemon 经 projection_data 自带
        projection_data=_projection_payload(
            agent_id=AGENT_ID,
            origin_type='task_run',
            origin_ref='task_run:123',
            title='生成日报',
        ),
    )

    assert result == {
        'result_message_id': '988',
        'conversation_id': str(conversation_id),
        'dedupe_key': f'work_session_result:{SESSION_ID}:final',
        'created': True,
    }
    insert_params = [
        params
        for _stmt, params in db.executed
        if isinstance(params, dict) and params.get('client_message_id') == f'work_session_result:{SESSION_ID}:final'
    ]
    assert len(insert_params) == 1, 'projection 必须写出一条会话消息'
    content = json.loads(insert_params[0]['content'])
    assert content['title'] == '工作会话「生成日报」已完成'
    assert content['description'] == '已生成客户优先级和跟进建议。'
    assert content['resource']['id'] == SESSION_ID
    assert content['resource']['metadata']['agent_id'] == AGENT_ID
    assert content['resource']['metadata']['origin_type'] == 'task_run'
    assert content['primary_action']['uri'] == f'hasn://tasks/sessions/{SESSION_ID}'
    assert db.flushed is True


@pytest.mark.asyncio
async def test_projection_rejects_when_agent_id_missing_and_session_absent() -> None:
    """云端无 session 且 projection_data 不带 agent_id 时，明确拒绝（不静默写错会话）。"""
    db = FakeDb(results=[FakeScalarResult(None)])

    with pytest.raises(errors.RequestError):
        await service_module.hasn_sessions_service.project_work_session_result(
            db=db,
            owner_id=OWNER_ID,
            session_id=SESSION_ID,
            projection_data=_projection_payload(),  # 无 agent_id
        )


def test_send_message_does_not_return_placeholder(task_sessions_app: FastAPI) -> None:
    with TestClient(task_sessions_app) as client:
        response = client.post(
            f'/api/v1/hasn/app/sessions/{SESSION_ID}/messages',
            headers={'Authorization': 'Bearer fake-token'},
            json={'content_text': '继续处理', 'client_message_id': 'client-1'},
        )

    assert response.status_code == 400, response.text
    assert 'msg_placeholder' not in response.text
