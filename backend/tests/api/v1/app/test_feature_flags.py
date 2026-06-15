"""D10 - GET /api/v1/app/feature-flags/{hasn_id} 端点契约测试.

测试策略 (与 test_push_tokens.py 一致):
- 最小 FastAPI 测试 app, 只挂被测 router
- monkeypatch `resolve_flags_for_hasn_id` 业务函数 → 避免 aiosqlite 依赖
- 覆盖 D10 acceptance: "seed 1 个 flag + 1 个 assignment → GET 返回 enabled=true"

覆盖用例:
1. 路由挂载契约 (GET /feature-flags/{hasn_id})
2. 单 flag + assignment → enabled=true (D10 acceptance)
3. 多 flag 混合 (assignment 覆盖 default_enabled)
4. 空 flags (未 seed) → data.flags == []
5. payload 透传 (含 None)
6. FeatureFlagEntry / FeatureFlagsResponse 契约
7. resolve_flags_for_hasn_id service: assignment 覆盖 default_enabled 双向
"""
from __future__ import annotations

from types import SimpleNamespace
from typing import Any

import pytest

from fastapi import FastAPI
from fastapi.testclient import TestClient

from backend.app.hasn_client.api import feature_flags as feature_flags_module
from backend.database.db import get_db

FAKE_HASN_ID = 'h_100001'


def _fake_db() -> Any:
    """替身 DB 依赖 (sync generator, FastAPI 接受)."""
    yield SimpleNamespace()


@pytest.fixture
def test_app() -> FastAPI:
    """最小测试 app, 仅 /api/v1/app/feature-flags/* 路由."""
    app = FastAPI()
    app.include_router(
        feature_flags_module.router, prefix='/api/v1/app/feature-flags'
    )
    app.dependency_overrides[get_db] = _fake_db
    return app


def _patch_resolver(
    monkeypatch: pytest.MonkeyPatch,
    resolved: list[feature_flags_module._ResolvedFlag],
    recorder: list[str] | None = None,
) -> None:
    async def fake_resolve(db: Any, hasn_id: str):  # noqa: RUF029, ANN202
        if recorder is not None:
            recorder.append(hasn_id)
        return list(resolved)

    monkeypatch.setattr(
        feature_flags_module, 'resolve_flags_for_hasn_id', fake_resolve
    )


# ---------------------------------------------------------------------------
# 路由挂载契约
# ---------------------------------------------------------------------------


def test_router_exposes_get_hasn_id_path(test_app: FastAPI) -> None:
    paths_methods = {
        (route.path, tuple(sorted(route.methods)))
        for route in test_app.routes
        if hasattr(route, 'methods') and getattr(route, 'methods', None)
    }
    assert (
        '/api/v1/app/feature-flags/{hasn_id}', ('GET',)
    ) in paths_methods, (
        f'GET /api/v1/app/feature-flags/{{hasn_id}} 路由缺失; 实际={paths_methods}'
    )


# ---------------------------------------------------------------------------
# D10 acceptance: seed 1 flag + 1 assignment → enabled=true
# ---------------------------------------------------------------------------


def test_get_returns_enabled_true_when_assignment_active(
    test_app: FastAPI, monkeypatch: pytest.MonkeyPatch
) -> None:
    recorder: list[str] = []
    _patch_resolver(
        monkeypatch,
        [
            feature_flags_module._ResolvedFlag(
                key='im_new_ui', enabled=True, payload={'variant': 'v2'},
            )
        ],
        recorder,
    )

    with TestClient(test_app) as client:
        resp = client.get(f'/api/v1/app/feature-flags/{FAKE_HASN_ID}')

    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body['code'] == 200
    flags = body['data']['flags']
    assert len(flags) == 1
    assert flags[0]['key'] == 'im_new_ui'
    assert flags[0]['enabled'] is True
    assert flags[0]['payload'] == {'variant': 'v2'}
    assert recorder == [FAKE_HASN_ID]


# ---------------------------------------------------------------------------
# 多 flag 混合
# ---------------------------------------------------------------------------


def test_get_returns_multiple_flags_with_mixed_status(
    test_app: FastAPI, monkeypatch: pytest.MonkeyPatch
) -> None:
    _patch_resolver(
        monkeypatch,
        [
            feature_flags_module._ResolvedFlag(
                key='flag_a', enabled=True, payload=None,
            ),
            feature_flags_module._ResolvedFlag(
                key='flag_b', enabled=False, payload={'rollout_pct': 10},
            ),
            feature_flags_module._ResolvedFlag(
                key='flag_c', enabled=True, payload=None,
            ),
        ],
    )

    with TestClient(test_app) as client:
        resp = client.get(f'/api/v1/app/feature-flags/{FAKE_HASN_ID}')

    assert resp.status_code == 200
    flags = resp.json()['data']['flags']
    by_key = {f['key']: f for f in flags}
    assert by_key['flag_a']['enabled'] is True
    assert by_key['flag_a']['payload'] is None
    assert by_key['flag_b']['enabled'] is False
    assert by_key['flag_b']['payload'] == {'rollout_pct': 10}
    assert by_key['flag_c']['enabled'] is True


# ---------------------------------------------------------------------------
# 空 (未 seed)
# ---------------------------------------------------------------------------


def test_get_returns_empty_list_when_no_flags_seeded(
    test_app: FastAPI, monkeypatch: pytest.MonkeyPatch
) -> None:
    _patch_resolver(monkeypatch, [])

    with TestClient(test_app) as client:
        resp = client.get(f'/api/v1/app/feature-flags/{FAKE_HASN_ID}')

    assert resp.status_code == 200
    assert resp.json()['data']['flags'] == []


# ---------------------------------------------------------------------------
# Schema 契约
# ---------------------------------------------------------------------------


def test_feature_flag_entry_payload_defaults_to_none() -> None:
    entry = feature_flags_module.FeatureFlagEntry(key='x', enabled=True)
    assert entry.payload is None


def test_feature_flags_response_flags_defaults_to_empty_list() -> None:
    resp = feature_flags_module.FeatureFlagsResponse()
    assert resp.flags == []


# ---------------------------------------------------------------------------
# resolve_flags_for_hasn_id service (覆盖真实业务分支, 不跑 DB)
# ---------------------------------------------------------------------------


class _StaticScalars:
    def __init__(self, rows: list[Any]) -> None:
        self._rows = rows

    def scalars(self) -> '_StaticScalars':
        return self

    def all(self) -> list[Any]:
        return list(self._rows)


class _SequencedSession:
    """按 execute 调用顺序返回结果: 第 1 次 → flags, 第 2 次 → assignments.

    resolve_flags_for_hasn_id 里的 execute 顺序固定 (先查 FeatureFlag
    全表, 再查 FeatureFlagAssignment by hasn_id), 这里按顺序返回即可。
    """

    def __init__(self, flags_rows: list[Any], assigns_rows: list[Any]) -> None:
        self._queue: list[list[Any]] = [flags_rows, assigns_rows]

    async def execute(self, _stmt: Any) -> _StaticScalars:  # noqa: RUF029
        if not self._queue:
            raise AssertionError('execute called more times than expected')
        return _StaticScalars(self._queue.pop(0))


@pytest.mark.anyio
async def test_resolve_service_assignment_overrides_default_both_directions() -> None:
    flag_on_by_assignment = SimpleNamespace(
        id=1, key='flag_on', default_enabled=False, payload={'a': 1},
    )
    flag_using_default = SimpleNamespace(
        id=2, key='flag_default_on', default_enabled=True, payload=None,
    )
    flag_off_by_assignment = SimpleNamespace(
        id=3, key='flag_force_off', default_enabled=True, payload=None,
    )
    assignment_on = SimpleNamespace(flag_id=1, enabled=True)
    assignment_off = SimpleNamespace(flag_id=3, enabled=False)

    db = _SequencedSession(
        flags_rows=[
            flag_on_by_assignment, flag_using_default, flag_off_by_assignment,
        ],
        assigns_rows=[assignment_on, assignment_off],
    )

    resolved = await feature_flags_module.resolve_flags_for_hasn_id(
        db, FAKE_HASN_ID
    )
    by_key = {r.key: r for r in resolved}
    assert by_key['flag_on'].enabled is True, (
        'assignment.enabled=True 必须覆盖 default_enabled=False'
    )
    assert by_key['flag_on'].payload == {'a': 1}
    assert by_key['flag_default_on'].enabled is True, (
        '无 assignment → 落回 default_enabled=True'
    )
    assert by_key['flag_force_off'].enabled is False, (
        'assignment.enabled=False 必须覆盖 default_enabled=True'
    )


@pytest.fixture(scope='module')
def anyio_backend() -> str:
    return 'asyncio'
