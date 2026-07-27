from __future__ import annotations

import asyncio
import json

from types import SimpleNamespace
from typing import TYPE_CHECKING, Any

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
    db: Any = FakeDb()

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

    db: Any = SimpleNamespace()
    page_data = await service_module.hasn_sessions_service.get_list_by_owner(
        db=db,
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


def test_app_origin_ref_parser_keeps_local_ref_as_metadata_only() -> None:
    """origin_ref 只解析应用与本地溯源 ref，不承担跨端 URI 身份。"""
    assert service_module._parse_app_origin_ref('resource:deck:deck_01ABC') == (
        'deck',
        'deck_01ABC',
    )
    assert service_module._parse_app_origin_ref('resource:deck:   ') is None
    assert service_module._parse_app_origin_ref('task_run:123') is None
    assert service_module._parse_app_origin_ref(None) is None


def _deck_projection_card(
    *, origin_ref: str, deck_server_id: str | None
) -> dict[str, Any]:
    """直接验证确定性组卡，不用数据库替身冒充集成链路。"""
    overrides: dict[str, Any] = {
        'agent_id': AGENT_ID,
        'origin_type': 'app',
        'origin_ref': origin_ref,
        'title': '唤星融资路演 · 生成',
        'task_id': None,
        'task_run_id': None,
    }
    if deck_server_id is not None:
        overrides['deck_server_id'] = deck_server_id

    content_json = service_module._projection_content_json(
        session_id=SESSION_ID,
        agent_id=AGENT_ID,
        origin_type='app',
        origin_ref=origin_ref,
        projection_data=_projection_payload(**overrides),
    )
    return service_module._projection_card_body(
        session_id=SESSION_ID,
        title='唤星融资路演 · 生成',
        content_json=content_json,
    )


def test_projection_deck_card_prefers_cloud_server_id() -> None:
    """演示文稿完成卡的 `hasn://deck/{id}` 必须用**云端权威 deck id**（非设备本地 ULID）。

    分身不再自己发卡：完成投影据 origin_ref(`resource:deck:{本地ULID}`) 认出 deck 会话，
    判别后卡里的深链/资源/动作 id 一律优先取随投影上传的 `deck_server_id`（云端权威）——
    这样「分享给别人」的链接对端才能据云端 id 读穿云端 ACL 打开（福仔铁律：本地 id 不上 URI）。
    """
    local_deck_id = 'deck_01ABCDEF'
    cloud_deck_id = '556677'  # 云端权威 id：数字串，与本地 ULID 形态不撞
    content = _deck_projection_card(
        origin_ref=f'resource:deck:{local_deck_id}',
        deck_server_id=cloud_deck_id,
    )

    assert content['schema_version'] == 'hasn.card/0.1'
    assert content['title'] == '演示文稿做好了'
    assert content['source'] == {
        'kind': 'app',
        'id': 'deck',
        'display_name': '演示文稿',
        'verified': True,
    }
    assert content['resource']['type'] == 'app.resource'
    assert content['resource']['app_id'] == 'deck'
    # ⭐ 关键：深链/资源/动作的 deck id 全部用云端权威 id，绝不出现本地 ULID
    assert content['resource']['id'] == cloud_deck_id
    assert content['resource']['uri'] == f'hasn://deck/{cloud_deck_id}'
    assert content['primary_action']['action_id'] == 'open_deck'
    assert content['primary_action']['kind'] == 'open_uri'
    assert content['primary_action']['uri'] == f'hasn://deck/{cloud_deck_id}'
    assert content['primary_action']['event']['event_type'] == 'deck.opened'
    assert content['primary_action']['event']['payload']['deck_id'] == cloud_deck_id
    # ⭐ 所有「打开路径」字段（webui 真正据以打开 deck 的入口）绝不含本地 ULID。
    # 注：resource.metadata.origin_ref / legacy_content_json 仍按原样保留本地 origin_ref —— 那是
    # 溯源/去重记录（标识这条投影来自哪个本地 deck 会话），webui 从不用它打开，故不在此约束内。
    open_path_fields = [
        content['resource']['id'],
        content['resource']['uri'],
        content['primary_action']['uri'],
        content['primary_action']['event']['payload']['deck_id'],
    ]
    for value in open_path_fields:
        assert local_deck_id not in str(value), f'打开路径字段不得含本地 ULID：{value!r}'
    # 不是任务卡：用户可见的标题/资源/动作都指向 deck，且不带任务卡的 状态/完成原因 fields 块
    assert content['description'] == '已生成客户优先级和跟进建议。'
    assert not content.get('fields'), 'deck 卡不应带任务卡的 状态/完成原因 字段块'


def test_projection_without_server_id_never_places_local_id_in_uri() -> None:
    """deck 尚未取得云端 ID 时回落任务摘要卡，绝不把设备本地 ID 写入 URI。"""
    local_deck_id = 'deck_01ABCDEF'
    content = _deck_projection_card(
        origin_ref=f'resource:deck:{local_deck_id}',
        deck_server_id=None,
    )

    assert content['title'] == '工作会话「唤星融资路演 · 生成」已完成'
    assert content['resource']['type'] == 'task_session'
    assert local_deck_id not in content['resource']['uri']
    assert local_deck_id not in content['primary_action']['uri']


def test_send_message_does_not_return_placeholder(task_sessions_app: FastAPI) -> None:
    with TestClient(task_sessions_app) as client:
        response = client.post(
            f'/api/v1/hasn/app/sessions/{SESSION_ID}/messages',
            headers={'Authorization': 'Bearer fake-token'},
            json={'content_text': '继续处理', 'client_message_id': 'client-1'},
        )

    assert response.status_code == 400, response.text
    assert 'msg_placeholder' not in response.text
