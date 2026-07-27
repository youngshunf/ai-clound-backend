"""单测：/api/v1/llm/app/usage/summary 的 ?agent_id=... 分支（§09 §5）。

- 不传 agent_id → 走原 by-user 路径（service.get_usage_summary 被调用）
- 传 agent_id 且归属当前用户 → 走 by-agent 路径（service.get_usage_summary_by_agent 被调用）
- 传 agent_id 但不归属当前用户（或不存在） → 403

策略：搭最小 FastAPI app，挂载 router；
override JWT 依赖注入固定 user.id；
override get_db / get_newapi_db；
db.execute 返回的 scalar_one_or_none 决定 ownership 校验结果。
"""
from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest
from fastapi import FastAPI, HTTPException, Request
from fastapi.testclient import TestClient
from starlette.status import HTTP_401_UNAUTHORIZED

from fastapi.responses import JSONResponse

from backend.app.newapi.api.v1.app import llm_newapi_user_mapping as endpoint_module
from backend.app.newapi.schema.llm_newapi_user_mapping import NewApiUsageSummary
from backend.common.exception.errors import BaseExceptionError
from backend.common.security.jwt import DependsJwtAuth
from backend.database.db import get_db


FAKE_USER_ID = 42
OTHER_USER_ID = 99


async def _fake_jwt_auth_ok(request: Request):
    auth = request.headers.get('Authorization') or ''
    if not auth.lower().startswith('bearer '):
        raise HTTPException(status_code=HTTP_401_UNAUTHORIZED, detail='Token 无效')
    request.scope['user'] = SimpleNamespace(id=FAKE_USER_ID, username='tester')
    request.scope['auth'] = ['authenticated']
    return auth[7:]


def _make_db_with_owner(owner_user_id: int | None):
    """db.execute(...).scalar_one_or_none() 返回 owner_user_id（None 表示 agent 不存在）"""
    db = AsyncMock()
    result = MagicMock()
    result.scalar_one_or_none = MagicMock(return_value=owner_user_id)
    db.execute = AsyncMock(return_value=result)
    return db


@pytest.fixture
def make_test_app():
    """工厂 fixture：参数为 ownership 校验返回的 user_id（None=不存在）"""
    def _build(owner_user_id: int | None):
        db = _make_db_with_owner(owner_user_id)

        async def _fake_db():
            yield db

        app = FastAPI()

        # 注册最小 BaseExceptionError handler（避开项目主 handler 对 starlette_context middleware 的依赖）
        @app.exception_handler(BaseExceptionError)
        async def _h(_request, exc: BaseExceptionError):
            return JSONResponse(
                status_code=exc.code,
                content={'code': exc.code, 'msg': exc.msg, 'data': exc.data},
            )

        app.include_router(endpoint_module.router, prefix='/api/v1/llm/app')
        app.dependency_overrides[DependsJwtAuth.dependency] = _fake_jwt_auth_ok
        app.dependency_overrides[get_db] = _fake_db
        return app, db
    return _build


def test_summary_without_agent_id_keeps_by_user_path(make_test_app, monkeypatch):
    """不传 agent_id → 走 service.get_usage_summary（by-user），不调 by-agent"""
    app, _ = make_test_app(owner_user_id=None)  # ownership 不会被查

    fake_by_user = AsyncMock(return_value=NewApiUsageSummary(
        items=[], total_prompt_tokens=0, total_completion_tokens=0,
        total_quota=0, total_requests=0, period_start=0, period_end=0,
    ))
    fake_by_agent = AsyncMock()  # 不应被调用
    monkeypatch.setattr(
        endpoint_module.llm_newapi_user_mapping_service,
        'get_usage_summary', fake_by_user,
    )
    monkeypatch.setattr(
        endpoint_module.llm_newapi_user_mapping_service,
        'get_usage_summary_by_agent', fake_by_agent,
    )

    with TestClient(app) as client:
        resp = client.get(
            '/api/v1/llm/app/usage/summary?start_time=1000&end_time=2000',
            headers={'Authorization': 'Bearer fake'},
        )

    assert resp.status_code == 200, resp.text
    fake_by_user.assert_awaited_once()
    fake_by_agent.assert_not_awaited()
    # 第二个 positional 参数应为当前 user.id
    assert fake_by_user.await_args.args[1] == FAKE_USER_ID


def test_summary_with_agent_id_calls_by_agent_path(make_test_app, monkeypatch):
    """传 agent_id 且 hermes_agent.user_id == request.user.id → 走 by-agent"""
    # ownership query 返回当前 user → 通过校验
    app, db = make_test_app(owner_user_id=FAKE_USER_ID)

    fake_by_user = AsyncMock()  # 不应被调用
    fake_by_agent = AsyncMock(return_value={
        'agent_id': 'agt_x',
        'period': [1000, 2000],
        'by_model': [{'model_name': 'anthropic/claude-sonnet-4.5',
                      'prompt_tokens': 100, 'completion_tokens': 50,
                      'quota': 150, 'request_count': 1}],
    })
    monkeypatch.setattr(
        endpoint_module.llm_newapi_user_mapping_service,
        'get_usage_summary', fake_by_user,
    )
    monkeypatch.setattr(
        endpoint_module.llm_newapi_user_mapping_service,
        'get_usage_summary_by_agent', fake_by_agent,
    )

    with TestClient(app) as client:
        resp = client.get(
            '/api/v1/llm/app/usage/summary',
            params={'start_time': 1000, 'end_time': 2000, 'agent_id': 'agt_x'},
            headers={'Authorization': 'Bearer fake'},
        )

    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body['code'] == 200
    data = body['data']
    assert data['agent_id'] == 'agt_x'
    assert data['period'] == [1000, 2000]
    assert data['by_model'][0]['model_name'] == 'anthropic/claude-sonnet-4.5'

    fake_by_user.assert_not_awaited()
    fake_by_agent.assert_awaited_once()
    args = fake_by_agent.await_args.args
    # service 签名: (db, agent_id, start_time, end_time)
    assert args[1] == 'agt_x'
    assert args[2] == 1000
    assert args[3] == 2000

    # ownership 查询确实跑了
    db.execute.assert_awaited_once()


def test_summary_with_other_users_agent_returns_403(make_test_app, monkeypatch):
    """ownership 校验：hermes_agent.user_id != request.user.id → 403，不调任何 service"""
    app, db = make_test_app(owner_user_id=OTHER_USER_ID)

    fake_by_user = AsyncMock()
    fake_by_agent = AsyncMock()
    monkeypatch.setattr(
        endpoint_module.llm_newapi_user_mapping_service,
        'get_usage_summary', fake_by_user,
    )
    monkeypatch.setattr(
        endpoint_module.llm_newapi_user_mapping_service,
        'get_usage_summary_by_agent', fake_by_agent,
    )

    with TestClient(app) as client:
        resp = client.get(
            '/api/v1/llm/app/usage/summary',
            params={'start_time': 1000, 'end_time': 2000, 'agent_id': 'agt_other'},
            headers={'Authorization': 'Bearer fake'},
        )

    assert resp.status_code == 403, resp.text
    fake_by_user.assert_not_awaited()
    fake_by_agent.assert_not_awaited()
    db.execute.assert_awaited_once()


def test_summary_with_nonexistent_agent_id_returns_403(make_test_app, monkeypatch):
    """agent_id 不存在 → 同样 403（不区分「不存在」与「他人持有」，避免泄漏）"""
    app, _ = make_test_app(owner_user_id=None)

    fake_by_agent = AsyncMock()
    monkeypatch.setattr(
        endpoint_module.llm_newapi_user_mapping_service,
        'get_usage_summary_by_agent', fake_by_agent,
    )

    with TestClient(app) as client:
        resp = client.get(
            '/api/v1/llm/app/usage/summary',
            params={'agent_id': 'agt_does_not_exist'},
            headers={'Authorization': 'Bearer fake'},
        )

    assert resp.status_code == 403, resp.text
    fake_by_agent.assert_not_awaited()
