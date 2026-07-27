from __future__ import annotations

from types import SimpleNamespace
from typing import Any

import pytest

from fastapi import FastAPI, Request
from fastapi.testclient import TestClient
from starlette_context.middleware import ContextMiddleware
from starlette_context.plugins import RequestIdPlugin

from backend.app.hasn.api.v1.agent import hasn_task_run as agent_task_run_api
from backend.app.hasn.schema.hasn_task import CreateHasnTaskParam
from backend.app.hasn.service import task_scheduler as task_scheduler_module
from backend.app.hasn_task.schema.skill_bundle import CreateHasnSkillBundleParam
from backend.common.exception.exception_handler import register_exception
from backend.common.security.agent_jwt_auth import DependsAgentJwtAuth
from backend.core.conf import settings
from backend.database.db import get_db, get_db_transaction


def test_center_task_scheduler_is_default_disabled() -> None:
    assert settings.HASN_TASK_CENTER_SCHEDULER_ENABLED is False


class FakeSession:
    def __init__(self, execute_results: list[Any] | None = None) -> None:
        self.added: list[Any] = []
        self.next_id = 456
        self.execute_results = execute_results or []
        self.committed = False

    def add(self, value: Any) -> None:
        self.added.append(value)

    async def flush(self) -> None:
        self.added[-1].id = self.next_id

    async def execute(self, _stmt: object) -> Any:
        return self.execute_results.pop(0)

    async def commit(self) -> None:
        self.committed = True


class FakeResult:
    def __init__(self, value: Any) -> None:
        self.value = value

    def scalars(self) -> FakeResult:
        return self

    def first(self) -> Any:
        return self.value

    def scalar_one_or_none(self) -> Any:
        return self.value


class FakeDbSession:
    def __init__(self, execute_results: list[Any] | None = None) -> None:
        self.execute_results = execute_results or []
        self.committed = False

    async def execute(self, _stmt: object) -> Any:
        return self.execute_results.pop(0)

    async def commit(self) -> None:
        self.committed = True


@pytest.fixture
def agent_api_app(monkeypatch: pytest.MonkeyPatch) -> FastAPI:
    app = FastAPI()
    app.add_middleware(ContextMiddleware, plugins=[RequestIdPlugin(validate=True)])
    register_exception(app)
    app.include_router(agent_task_run_api.router, prefix='/api/v1/hasn/agent/hasn/task/runs')

    async def fake_agent_auth(request: Request) -> None:
        request.state.agent = SimpleNamespace(agent_hasn_id='a_agent')
        return

    async def fake_db() -> FakeDbSession:
        return FakeDbSession()

    app.dependency_overrides[DependsAgentJwtAuth.dependency] = fake_agent_auth
    app.dependency_overrides[get_db] = fake_db
    app.dependency_overrides[get_db_transaction] = fake_db
    return app


@pytest.mark.asyncio
async def test_task_result_update_requires_matching_agent() -> None:
    from backend.app.hasn.service import task_scheduler as module
    from backend.common.exception import errors

    scheduler = module.TaskSchedulerService()
    task_run = SimpleNamespace(id=456, task_id=123, agent_id='a_agent', status='pending')
    session: Any = FakeSession(execute_results=[FakeResult(task_run)])

    with pytest.raises(errors.ForbiddenError):
        await scheduler._handle_task_result_in_session(
            session=session,
            run_id=456,
            reporting_agent_id='a_other',
            status='success',
            output='wrong agent',
        )

    assert task_run.status != 'success'
    assert not session.committed


@pytest.mark.asyncio
async def test_task_result_update_persists_matching_agent_result() -> None:
    from backend.app.hasn.service import task_scheduler as module

    scheduler = module.TaskSchedulerService()
    task_run = SimpleNamespace(id=456, task_id=123, agent_id='a_agent', status='pending')
    task = SimpleNamespace(id=123, last_status=None, last_error=None)
    session: Any = FakeSession(execute_results=[FakeResult(task_run), FakeResult(task)])

    success = await scheduler._handle_task_result_in_session(
        session=session,
        run_id=456,
        reporting_agent_id='a_agent',
        prompt_snapshot='Skill bundles: backend-dev\n\n生成日报',
        status='success',
        output='done',
        model='runtime-model',
        token_usage={'input_tokens': 1, 'output_tokens': 2, 'total_tokens': 3},
        duration_ms=1200,
    )

    assert success is True
    assert session.committed is True
    assert task_run.status == 'success'
    assert task_run.output == 'done'
    assert task_run.prompt_snapshot == 'Skill bundles: backend-dev\n\n生成日报'
    assert task_run.model == 'runtime-model'
    assert task_run.duration_ms == 1200
    assert task.last_status == 'success'
    assert task.last_error is None


def test_task_result_route_accepts_only_executing_agent(
    agent_api_app: FastAPI, monkeypatch: pytest.MonkeyPatch
) -> None:
    task_run = SimpleNamespace(id=456, task_id=123, agent_id='a_agent', status='pending')
    task = SimpleNamespace(id=123, last_status=None, last_error=None)
    session = FakeDbSession(execute_results=[FakeResult(task_run), FakeResult(task)])
    monkeypatch.setattr(task_scheduler_module, 'async_db_session', lambda: _session_ctx(session))

    app = agent_api_app
    with TestClient(app) as client:
        response = client.post(
            '/api/v1/hasn/agent/hasn/task/runs/task-result',
            json={
                'run_id': 456,
                'status': 'success',
                'prompt_snapshot': 'Skill bundles: backend-dev\n\n生成日报',
                'output': 'done',
            },
        )

    assert response.status_code == 200, response.text
    assert response.json()['data'] == {'run_id': 456, 'status': 'success'}
    assert task_run.status == 'success'
    assert task_run.output == 'done'
    assert task_run.prompt_snapshot == 'Skill bundles: backend-dev\n\n生成日报'
    assert task.last_status == 'success'
    assert task.last_error is None
    assert session.committed is True


def test_task_and_skill_bundle_schemas_use_name_lists() -> None:
    task = CreateHasnTaskParam(
        owner_id='h_owner',
        agent_id='a_agent',
        name='日报',
        prompt='生成日报',
        skill_bundle_ids=['backend-dev'],
        skill_ids=['pytest'],
        enabled_toolsets=['terminal'],
        schedule_type='once',
        schedule_config={'run_at': '2026-05-22T09:00:00Z'},
        enabled=True,
        state='scheduled',
        run_count=0,
        repeat_completed=0,
    )
    bundle = CreateHasnSkillBundleParam(
        owner_id='h_owner',
        name='backend-dev',
        skill_ids=['pytest', 'test-driven-development'],
    )

    assert task.skill_bundle_ids == ['backend-dev']
    assert task.skill_ids == ['pytest']
    assert task.enabled_toolsets == ['terminal']
    assert bundle.skill_ids == ['pytest', 'test-driven-development']


def test_hasn_app_router_mounts_task_management_routes() -> None:
    from backend.app.hasn.api.router import app
    from backend.app.hasn_task.api.router import app as hasn_task_app

    routes = {getattr(route, 'path') for route in app.routes}
    hasn_task_routes = {getattr(route, 'path') for route in hasn_task_app.routes}

    # M8 退役：旧 app/hasn 任务 CRUD 面已删除，任务面收口到 hasn_task 应用
    assert '/api/v1/hasn/app/hasn/tasks' not in routes
    assert '/api/v1/hasn/app/hasn/skill/bundles' in routes
    assert '/api/v1/hasn-task/app/tasks' in hasn_task_routes
    assert '/api/v1/hasn-task/app/sync/push' in hasn_task_routes


def test_app_task_create_overrides_owner_from_authenticated_user(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from backend.app.hasn_task.api.v1.app import task as module
    from backend.common.security.jwt import DependsJwtAuth

    fastapi_app = FastAPI()
    fastapi_app.add_middleware(ContextMiddleware, plugins=[RequestIdPlugin(validate=True)])
    register_exception(fastapi_app)
    fastapi_app.include_router(module.router, prefix='/api/v1/hasn-task/app')

    captured: dict[str, Any] = {}

    async def fake_agent_auth(request: Request) -> None:
        request.scope['user'] = SimpleNamespace(id=7)
        return

    async def fake_db() -> FakeDbSession:
        return FakeDbSession([FakeResult(SimpleNamespace(hasn_id='h_owner'))])

    async def fake_create(*, db: Any, obj: Any) -> Any:
        captured['db'] = db
        captured['obj'] = obj
        return SimpleNamespace(id=999, owner_id=obj.owner_id)

    monkeypatch.setattr(module.hasn_task_service, 'create_with_schedule', fake_create)
    fastapi_app.dependency_overrides[DependsJwtAuth.dependency] = fake_agent_auth
    fastapi_app.dependency_overrides[get_db] = fake_db
    fastapi_app.dependency_overrides[get_db_transaction] = fake_db

    with TestClient(fastapi_app) as client:
        response = client.post(
            '/api/v1/hasn-task/app/tasks',
            json={
                'owner_id': 'h_other',
                'agent_id': 'a_agent',
                'name': '日报',
                'prompt': '生成日报',
                'skill_bundle_ids': ['backend-dev'],
                'skill_ids': ['pytest'],
                'schedule_type': 'once',
                'schedule_config': {'run_at': '2026-05-22T09:00:00Z'},
                'enabled': True,
                'state': 'scheduled',
                'run_count': 0,
                'repeat_completed': 0,
            },
        )

    assert response.status_code == 200, response.text
    assert captured['obj'].owner_id == 'h_owner'
    assert captured['obj'].agent_id == 'a_agent'


def test_app_task_detail_rejects_foreign_owner(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from backend.app.hasn_task.api.v1.app import task as module
    from backend.common.security.jwt import DependsJwtAuth

    fastapi_app = FastAPI()
    fastapi_app.add_middleware(ContextMiddleware, plugins=[RequestIdPlugin(validate=True)])
    register_exception(fastapi_app)
    fastapi_app.include_router(module.router, prefix='/api/v1/hasn-task/app')

    async def fake_agent_auth(request: Request) -> None:
        request.scope['user'] = SimpleNamespace(id=7)
        return

    async def fake_db() -> FakeDbSession:
        return FakeDbSession([FakeResult(SimpleNamespace(hasn_id='h_owner'))])

    async def fake_get(*, db: Any, pk: int) -> Any:
        return SimpleNamespace(id=pk, owner_id='h_other')

    monkeypatch.setattr(module.hasn_task_service, 'get', fake_get)
    fastapi_app.dependency_overrides[DependsJwtAuth.dependency] = fake_agent_auth
    fastapi_app.dependency_overrides[get_db] = fake_db
    fastapi_app.dependency_overrides[get_db_transaction] = fake_db

    with TestClient(fastapi_app) as client:
        response = client.get('/api/v1/hasn-task/app/tasks/123')

    # 新面跨户返回 404（不泄露存在性，owned_task 守卫）
    assert response.status_code == 404


def test_app_task_run_detail_rejects_foreign_task_owner(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from backend.app.hasn_task.api.v1.app import run as module
    from backend.app.hasn_task.api.v1.app import task as task_module
    from backend.common.security.jwt import DependsJwtAuth

    fastapi_app = FastAPI()
    fastapi_app.add_middleware(ContextMiddleware, plugins=[RequestIdPlugin(validate=True)])
    register_exception(fastapi_app)
    fastapi_app.include_router(module.router, prefix='/api/v1/hasn-task/app')

    async def fake_agent_auth(request: Request) -> None:
        request.scope['user'] = SimpleNamespace(id=7)
        return

    async def fake_db() -> FakeDbSession:
        return FakeDbSession([FakeResult(SimpleNamespace(hasn_id='h_owner'))])

    async def fake_get_run(*, db: Any, pk: int) -> Any:
        return SimpleNamespace(id=pk, task_id=123, agent_id='a_agent')

    async def fake_get_task(*, db: Any, pk: int) -> Any:
        return SimpleNamespace(id=pk, owner_id='h_other')

    monkeypatch.setattr(module.hasn_task_run_service, 'get', fake_get_run)
    monkeypatch.setattr(task_module.hasn_task_service, 'get', fake_get_task)
    fastapi_app.dependency_overrides[DependsJwtAuth.dependency] = fake_agent_auth
    fastapi_app.dependency_overrides[get_db] = fake_db
    fastapi_app.dependency_overrides[get_db_transaction] = fake_db

    with TestClient(fastapi_app) as client:
        response = client.get('/api/v1/hasn-task/app/runs/456')

    # 新面跨户返回 404（不泄露存在性，owned_task 守卫）
    assert response.status_code == 404


def _session_ctx(session: Any):
    class _Ctx:
        async def __aenter__(self) -> Any:
            return session

        async def __aexit__(self, exc_type, exc, tb) -> None:  # noqa: ANN001
            return None

    return _Ctx()
