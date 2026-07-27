"""单测：Hermes runtime internal LLM credential endpoints（§09 §5）。

策略：搭最小 FastAPI app，挂载 internal_llm_credential.router；
通过 dependency_overrides 替换 get_db / get_newapi_db；
通过 monkeypatch settings.RUNTIME_INTERNAL_TOKEN + 注入 X-Internal-Token header；
mock LlmNewapiUserMappingService 方法，不真连 DB。
"""
from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from backend.app.hermes.api.v1.internal import llm_credential as endpoint_module
from backend.database.db import get_db


INTERNAL_TOKEN = 'test-internal-token-abc-987'
HEADERS_OK = {'X-Internal-Token': INTERNAL_TOKEN}


async def _fake_db():
    yield SimpleNamespace()


@pytest.fixture
def configured_internal_token(monkeypatch):
    from backend.common.security import internal_auth as ia_mod
    monkeypatch.setattr(ia_mod.settings, 'RUNTIME_INTERNAL_TOKEN', INTERNAL_TOKEN)


@pytest.fixture
def test_app(configured_internal_token):
    app = FastAPI()
    app.include_router(endpoint_module.router, prefix='/api/v1/hermes/internal/llm')
    app.dependency_overrides[get_db] = _fake_db
    return app


# ------------------------------------------------------------------
# /issue-credential
# ------------------------------------------------------------------


def test_issue_without_internal_token_returns_401(test_app):
    """缺 X-Internal-Token → 401，service 不被调用"""
    with TestClient(test_app) as client:
        resp = client.post(
            '/api/v1/hermes/internal/llm/issue-credential',
            json={'agent_id': 'agt_1', 'user_id': 1},
        )
    assert resp.status_code == 401, resp.text


def test_issue_with_wrong_internal_token_returns_401(test_app):
    """错 token → 401"""
    with TestClient(test_app) as client:
        resp = client.post(
            '/api/v1/hermes/internal/llm/issue-credential',
            json={'agent_id': 'agt_1', 'user_id': 1},
            headers={'X-Internal-Token': 'WRONG-TOKEN'},
        )
    assert resp.status_code == 401, resp.text


def test_issue_happy_returns_raw_token_key_once(test_app, monkeypatch):
    """首次签发 → 返回 raw_token_key + reused=False；service 收到正确 kwargs"""
    fake_issued = {
        'agent_id': 'agt_x',
        'newapi_user_id': 200001,
        'newapi_token_id': 300001,
        'token_key_prefix': 'hxAbCdEf',
        'raw_token_key': 'hxAbCdEf' + 'A' * 40,
        'reused': False,
    }
    fake_ensure = AsyncMock(return_value=fake_issued)
    monkeypatch.setattr(
        endpoint_module.llm_newapi_user_mapping_service,
        'ensure_agent_token',
        fake_ensure,
    )

    with TestClient(test_app) as client:
        resp = client.post(
            '/api/v1/hermes/internal/llm/issue-credential',
            headers=HEADERS_OK,
            json={
                'agent_id': 'agt_x',
                'user_id': 42,
                'model_allowlist': ['anthropic/claude-sonnet-4.5'],
                'rate_limit_rps': 20,
                'per_token_quota': 100_000,
            },
        )

    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body['code'] == 200
    data = body['data']
    assert data['agent_id'] == 'agt_x'
    assert data['newapi_user_id'] == 200001
    assert data['newapi_token_id'] == 300001
    assert data['token_key_prefix'] == 'hxAbCdEf'
    assert data['raw_token_key'] == fake_issued['raw_token_key']
    assert data['reused'] is False
    assert isinstance(data['issued_at'], str) and len(data['issued_at']) > 10

    # service kwargs 正确传递
    fake_ensure.assert_awaited_once()
    kwargs = fake_ensure.await_args.kwargs
    assert kwargs['agent_id'] == 'agt_x'
    assert kwargs['user_id'] == 42
    assert kwargs['model_allowlist'] == ['anthropic/claude-sonnet-4.5']
    assert kwargs['rate_limit_rps'] == 20
    assert kwargs['per_token_quota'] == 100_000


def test_issue_idempotent_returns_existing_no_raw(test_app, monkeypatch):
    """同 agent 已有未撤销 token → reused=True，raw_token_key=None（不再返回明文）"""
    fake_existing = {
        'agent_id': 'agt_x',
        'newapi_user_id': 200001,
        'newapi_token_id': 300001,
        'token_key_prefix': 'hxOldOld',
        'raw_token_key': None,  # 关键：service 已确保 reuse 时不返回 raw
        'reused': True,
    }
    fake_ensure = AsyncMock(return_value=fake_existing)
    monkeypatch.setattr(
        endpoint_module.llm_newapi_user_mapping_service,
        'ensure_agent_token',
        fake_ensure,
    )

    with TestClient(test_app) as client:
        resp = client.post(
            '/api/v1/hermes/internal/llm/issue-credential',
            headers=HEADERS_OK,
            json={'agent_id': 'agt_x', 'user_id': 42},
        )

    assert resp.status_code == 200, resp.text
    data = resp.json()['data']
    assert data['reused'] is True
    assert data['raw_token_key'] is None
    assert data['token_key_prefix'] == 'hxOldOld'


# ------------------------------------------------------------------
# /revoke-credential
# ------------------------------------------------------------------


def test_revoke_happy_returns_revoked_true(test_app, monkeypatch):
    """有未撤销 token → revoked=True，revoked_at 是 ISO 8601 字符串"""
    fake_revoke = AsyncMock(return_value=True)
    monkeypatch.setattr(
        endpoint_module.llm_newapi_user_mapping_service,
        'revoke_agent_token',
        fake_revoke,
    )

    with TestClient(test_app) as client:
        resp = client.post(
            '/api/v1/hermes/internal/llm/revoke-credential',
            headers=HEADERS_OK,
            json={'agent_id': 'agt_x'},
        )

    assert resp.status_code == 200, resp.text
    data = resp.json()['data']
    assert data['agent_id'] == 'agt_x'
    assert data['revoked'] is True
    assert isinstance(data['revoked_at'], str) and len(data['revoked_at']) > 10

    # service 收到正确参数
    fake_revoke.assert_awaited_once()
    args = fake_revoke.await_args.args
    # revoke_agent_token(db, agent_id) — 第二个 positional 是 agent_id
    assert args[1] == 'agt_x'


def test_revoke_when_not_exists_returns_revoked_false(test_app, monkeypatch):
    """无未撤销/已撤销 → revoked=False，revoked_at=None"""
    fake_revoke = AsyncMock(return_value=False)
    monkeypatch.setattr(
        endpoint_module.llm_newapi_user_mapping_service,
        'revoke_agent_token',
        fake_revoke,
    )

    with TestClient(test_app) as client:
        resp = client.post(
            '/api/v1/hermes/internal/llm/revoke-credential',
            headers=HEADERS_OK,
            json={'agent_id': 'agt_unknown'},
        )

    assert resp.status_code == 200, resp.text
    data = resp.json()['data']
    assert data['agent_id'] == 'agt_unknown'
    assert data['revoked'] is False
    assert data['revoked_at'] is None


# ------------------------------------------------------------------
# /rotate-credential
# ------------------------------------------------------------------


def test_rotate_revokes_old_returns_new_raw(test_app, monkeypatch):
    """rotate 调 service.rotate_agent_token，返回新 raw_token_key + 新 newapi_token_id。

    底层 service 已保证先 revoke 后 ensure（service 层单测覆盖）；
    端点层只断言 endpoint 调用 rotate_agent_token 并把结果转 JSON。
    """
    new_raw = 'hxNewNewN' + 'B' * 39
    fake_rotated = {
        'agent_id': 'agt_x',
        'newapi_user_id': 200001,
        'newapi_token_id': 300002,  # 新 token id（不同于旧的 300001）
        'token_key_prefix': new_raw[:8],
        'raw_token_key': new_raw,
        'reused': False,
    }
    fake_rotate = AsyncMock(return_value=fake_rotated)
    monkeypatch.setattr(
        endpoint_module.llm_newapi_user_mapping_service,
        'rotate_agent_token',
        fake_rotate,
    )

    with TestClient(test_app) as client:
        resp = client.post(
            '/api/v1/hermes/internal/llm/rotate-credential',
            headers=HEADERS_OK,
            json={'agent_id': 'agt_x', 'user_id': 42},
        )

    assert resp.status_code == 200, resp.text
    data = resp.json()['data']
    assert data['agent_id'] == 'agt_x'
    assert data['newapi_user_id'] == 200001
    assert data['newapi_token_id'] == 300002
    assert data['token_key_prefix'] == new_raw[:8]
    assert data['raw_token_key'] == new_raw
    assert isinstance(data['issued_at'], str) and len(data['issued_at']) > 10

    fake_rotate.assert_awaited_once()
    kwargs = fake_rotate.await_args.kwargs
    assert kwargs['agent_id'] == 'agt_x'
    assert kwargs['user_id'] == 42


def test_rotate_without_internal_token_returns_401(test_app):
    """rotate 也必须 X-Internal-Token 校验"""
    with TestClient(test_app) as client:
        resp = client.post(
            '/api/v1/hermes/internal/llm/rotate-credential',
            json={'agent_id': 'agt_x', 'user_id': 42},
        )
    assert resp.status_code == 401, resp.text
