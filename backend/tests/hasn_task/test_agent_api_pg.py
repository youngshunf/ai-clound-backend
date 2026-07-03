"""任务系统 M2 云端验收（实施/91，真实 PG，零 mock）。

覆盖：
  A) 平台注册：workbench catalog 含 hasn_task、AI-Native manifest 注册、scope R5 收口、
     resolve_app_access free 放行。
  B) Agent API（/api/v1/hasn-task/agent，Agent JWT）经事件通道（save_task_event）落
     hasn_task.task + hasn_sync_events feed：
     - D4：once → scheduled（有 next_run_at）；interval → pending_approval + 提醒通知行
     - R4：10 分钟幂等键（created=False）+ 单 agent 10/小时频控
     - R2：改调度升级成周期 → 回 pending_approval；rejected 拒写；resume 仅 paused；
           run_now 仅 scheduled/paused
     - D4 业务态审批：owner app 面 approve → scheduled / reject → rejected
     - D2 深查：query_results / runs 读 hasn_task.run_summary（含 artifacts_json）
     - 跨户隔离：他人任务恒 NotFound

最小 app 同时挂 agent 面 + app 面（approve/reject），override 鉴权与 DB 会话，
经 ASGITransport 走完整 HTTP；事务末尾回滚不污染库。需要 export DATABASE_PORT=15432。
"""

from __future__ import annotations

import uuid

from datetime import UTC, datetime
from types import SimpleNamespace
from typing import TYPE_CHECKING

import httpx
import pytest
import pytest_asyncio

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

from backend.app.hasn.model.hasn_humans import HasnHumans
from backend.app.hasn_task.api.v1.agent.task import router as agent_task_router
from backend.app.hasn_task.api.v1.app.task import router as app_task_router
from backend.common.dataclasses import AgentTokenPayload
from backend.common.exception.errors import BaseExceptionError
from backend.common.security.agent_jwt_auth import agent_jwt_auth
from backend.common.security.jwt import DependsJwtAuth
from backend.database.db import SQLALCHEMY_DATABASE_URL, get_db, get_db_transaction

if TYPE_CHECKING:
    from collections.abc import AsyncIterator

pytestmark = pytest.mark.asyncio

_APP = FastAPI()
_APP.include_router(agent_task_router, prefix='/api/v1/hasn-task/agent')
_APP.include_router(app_task_router, prefix='/api/v1/hasn-task/app')


@_APP.exception_handler(BaseExceptionError)
async def _err_handler(_request: Request, exc: BaseExceptionError) -> JSONResponse:  # noqa: RUF029 FastAPI 要求 async handler
    return JSONResponse(status_code=exc.code, content={'code': exc.code, 'msg': str(exc.msg), 'data': None})


def _uid() -> str:
    return uuid.uuid4().hex[:8]


# ---------- A) 平台注册（纯 Python，不依赖 DB） ----------


def test_workbench_registry_contains_hasn_task() -> None:
    from backend.app.hasn.service.app_catalog_registry import AppCatalogRegistry

    registry = AppCatalogRegistry.default()
    app = next((a for a in registry.list() if a.id == 'hasn_task'), None)
    assert app is not None, 'workbench registry 缺 hasn_task'
    assert app.entry_route == '/apps/tasks'
    assert app.install_policy == 'auto'


def test_ai_native_manifest_registered() -> None:
    from backend.app.hasn.service.ai_native_app_registry import ai_native_app_registry

    manifest = ai_native_app_registry.get_builtin_manifest('hasn_task')
    assert manifest is not None, 'AI-Native registry 缺 hasn_task manifest'
    assert manifest['app_id'] == 'hasn_task'
    assert manifest['tools'] == []  # 方案 A：本地工具，云端 manifest 不承载 tool 定义
    caps = {c['mcp_name']: c for c in manifest['capabilities']}
    assert 'hasn.task.create' in caps
    assert caps['hasn.task.create']['tool_id'] == 'hasn_task.create'
    assert 'task:manage' in caps['hasn.task.create']['required_scopes']
    assert 'hasn.task.query_results' in caps
    assert 'task:read' in caps['hasn.task.query_results']['required_scopes']
    assert 'hasn.task.run_now' in caps
    assert 'task:run' in caps['hasn.task.run_now']['required_scopes']
    # 出厂全 Allow 免确认（16-doc D-v3-1）；owner 可设 ask/deny override（D4 工具授权面）
    assert caps['hasn.task.create']['human_confirmation']['required'] is False
    assert caps['hasn.task.list']['human_confirmation'] == {'required': False}


def test_scope_catalog_r5() -> None:
    from backend.app.mcp.scopes import SCOPE_CATALOG

    assert 'task:read' in SCOPE_CATALOG
    assert 'task:manage' in SCOPE_CATALOG
    assert 'task:run' in SCOPE_CATALOG
    assert 'task:create' not in SCOPE_CATALOG, 'R5 收口：task:create 应废弃'


# ---------- B) Agent API E2E（真实 PG） ----------


@pytest_asyncio.fixture
async def e2e() -> AsyncIterator[SimpleNamespace]:
    engine = create_async_engine(SQLALCHEMY_DATABASE_URL, poolclass=NullPool)
    try:
        async with engine.connect() as conn:
            await conn.execute(select(1))
    except Exception as exc:
        await engine.dispose()
        pytest.skip(f'本地 PostgreSQL 不可达，跳过: {exc!r}')

    session = async_sessionmaker(engine, expire_on_commit=False)()

    tag = _uid()
    owner = f'h_tsk_{tag}'
    other_owner = f'h_tsk2_{tag}'
    owner_uid = 960000 + int(uuid.uuid4().int % 9000)
    agent_hasn = f'a_tsk_{tag}'
    session.add(
        HasnHumans(hasn_id=owner, star_id=f's_{owner_uid}', user_id=owner_uid, nickname='Owner', status='active')
    )
    session.add(
        HasnHumans(
            hasn_id=other_owner,
            star_id=f's_{owner_uid + 1}',
            user_id=owner_uid + 1,
            nickname='Other',
            status='active',
        )
    )
    await session.flush()

    async def _yield_session() -> AsyncIterator:  # noqa: RUF029 FastAPI 依赖 override 须为 async generator
        yield session

    def _payload(owner_id: str, uid: int) -> AgentTokenPayload:
        return AgentTokenPayload(
            agent_hasn_id=agent_hasn,
            agent_name=f'agent_{tag}',
            owner_hasn_id=owner_id,
            owner_user_id=uid,
            session_uuid=f'sess_{tag}',
            expire_time=datetime(2099, 1, 1, tzinfo=UTC),
        )

    state = SimpleNamespace(agent_owner=owner)

    async def _agent_auth(request: Request) -> AgentTokenPayload:  # noqa: RUF029 override 目标依赖是 async
        payload = _payload(state.agent_owner, owner_uid)
        request.state.agent = payload
        return payload

    async def _owner_auth(request: Request) -> None:  # noqa: RUF029 override 目标依赖是 async
        request.scope['user'] = SimpleNamespace(id=owner_uid, hasn_id=owner)

    _APP.dependency_overrides[get_db] = _yield_session
    _APP.dependency_overrides[get_db_transaction] = _yield_session
    _APP.dependency_overrides[agent_jwt_auth] = _agent_auth
    _APP.dependency_overrides[DependsJwtAuth.dependency] = _owner_auth

    client = httpx.AsyncClient(transport=httpx.ASGITransport(app=_APP), base_url='http://e2e')
    try:
        yield SimpleNamespace(
            client=client, session=session, owner=owner, other_owner=other_owner, agent_hasn=agent_hasn, state=state
        )
    finally:
        await client.aclose()
        _APP.dependency_overrides.clear()
        await session.rollback()
        await session.close()
        await engine.dispose()


async def _create(
    client: httpx.AsyncClient, name: str, schedule_type: str, schedule_config: dict, **extra: object
) -> dict:
    r = await client.post(
        '/api/v1/hasn-task/agent/tasks',
        json={
            'name': name,
            'prompt': f'执行 {name}',
            'schedule_type': schedule_type,
            'schedule_config': schedule_config,
            **extra,
        },
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body['code'] == 200, body
    return body['data']


async def test_once_creates_scheduled_and_feed_event(e2e: SimpleNamespace) -> None:
    data = await _create(e2e.client, '一次性任务', 'once', {'run_at': '2099-01-01T08:00:00+08:00'})
    task = data['task']
    assert data['created'] is True
    assert task['state'] == 'scheduled'
    assert task['next_run_at'] is not None
    assert task['created_by_kind'] == 'agent'
    assert task['task_id'].startswith('tsk_')

    # 权威行进 hasn_task.task + feed 进 hasn_sync_events（节点经 sync/pull 可拉）
    row = await e2e.session.execute(
        text(
            "SELECT payload FROM hasn_sync_events WHERE owner_id = :o AND event_type = 'task.created'"
        ),
        {'o': e2e.owner},
    )
    events = row.mappings().all()
    assert len(events) == 1
    assert events[0]['payload']['task_id'] == task['task_id']


async def test_periodic_goes_pending_approval_with_notification(e2e: SimpleNamespace) -> None:
    data = await _create(e2e.client, '每日简报', 'interval', {'minutes': 1440})
    task = data['task']
    assert task['state'] == 'pending_approval'
    assert task['next_run_at'] is None

    # D4：提醒卡片 = 通知权威行（非审批票据，绝不走 ask_gate）
    row = await e2e.session.execute(
        text(
            'SELECT type, dedupe_key FROM hasn_notifications '
            "WHERE target_id = :o AND type = 'task.pending_approval'"
        ),
        {'o': e2e.owner},
    )
    notes = row.mappings().all()
    assert len(notes) == 1
    assert notes[0]['dedupe_key'] == f'task.pending_approval:{task["task_id"]}'


async def test_create_idempotency_window(e2e: SimpleNamespace) -> None:
    first = await _create(e2e.client, '幂等任务', 'once', {'run_at': '2099-02-01T08:00:00+08:00'})
    second = await _create(e2e.client, '幂等任务', 'once', {'run_at': '2099-02-01T08:00:00+08:00'})
    assert first['created'] is True
    assert second['created'] is False
    assert second['task']['task_id'] == first['task']['task_id']


async def test_create_rate_limit_10_per_hour(e2e: SimpleNamespace) -> None:
    for i in range(10):
        await _create(e2e.client, f'频控任务 {i}', 'once', {'run_at': f'2099-03-{i + 1:02d}T08:00:00+08:00'})
    r = await e2e.client.post(
        '/api/v1/hasn-task/agent/tasks',
        json={
            'name': '第 11 个',
            'prompt': '超限',
            'schedule_type': 'once',
            'schedule_config': {'run_at': '2099-04-01T08:00:00+08:00'},
        },
    )
    assert r.status_code != 200, r.text
    assert '频繁' in r.text


async def test_update_schedule_upgrade_returns_pending_approval(e2e: SimpleNamespace) -> None:
    data = await _create(e2e.client, '升级任务', 'once', {'run_at': '2099-05-01T08:00:00+08:00'})
    task_id = data['task']['task_id']
    r = await e2e.client.put(
        f'/api/v1/hasn-task/agent/tasks/{task_id}',
        json={'schedule_type': 'cron', 'schedule_config': {'cron': '0 8 * * *'}},
    )
    assert r.status_code == 200, r.text
    updated = r.json()['data']['task']
    assert updated['state'] == 'pending_approval'  # R2：once→周期升级须重审
    assert updated['next_run_at'] is None


async def test_owner_approve_and_reject_flow(e2e: SimpleNamespace) -> None:
    c = e2e.client
    # 待审批任务 → owner approve → scheduled + next_run_at
    data = await _create(c, '待批准任务', 'interval', {'minutes': 60})
    task_id = data['task']['task_id']
    pk = (
        await e2e.session.execute(
            text('SELECT id FROM hasn_task.task WHERE owner_id = :o AND task_uuid = :u'),
            {'o': e2e.owner, 'u': task_id},
        )
    ).scalar_one()
    r = await c.post(f'/api/v1/hasn-task/app/tasks/{pk}/approve')
    assert r.status_code == 200, r.text
    approved = r.json()['data']['task']
    assert approved['state'] == 'scheduled'
    assert approved['next_run_at'] is not None

    # 第二个待审批任务 → owner reject → rejected，agent 再改被拒
    data2 = await _create(c, '待拒绝任务', 'interval', {'minutes': 30})
    task_id2 = data2['task']['task_id']
    pk2 = (
        await e2e.session.execute(
            text('SELECT id FROM hasn_task.task WHERE owner_id = :o AND task_uuid = :u'),
            {'o': e2e.owner, 'u': task_id2},
        )
    ).scalar_one()
    r = await c.post(f'/api/v1/hasn-task/app/tasks/{pk2}/reject')
    assert r.status_code == 200, r.text
    assert r.json()['data']['task']['state'] == 'rejected'

    r = await c.put(f'/api/v1/hasn-task/agent/tasks/{task_id2}', json={'name': '改名'})
    assert r.status_code != 200
    assert '拒绝' in r.text

    # 已 scheduled 的任务无待审批事项 → approve 报错
    r = await c.post(f'/api/v1/hasn-task/app/tasks/{pk}/approve')
    assert r.status_code != 200


async def test_transition_guards(e2e: SimpleNamespace) -> None:
    c = e2e.client
    data = await _create(c, '状态守卫任务', 'once', {'run_at': '2099-06-01T08:00:00+08:00'})
    task_id = data['task']['task_id']

    # resume 仅 paused：scheduled 直接 resume → 拒
    r = await c.post(f'/api/v1/hasn-task/agent/tasks/{task_id}/resume')
    assert r.status_code != 200

    # pause → paused → resume → scheduled
    r = await c.post(f'/api/v1/hasn-task/agent/tasks/{task_id}/pause')
    assert r.status_code == 200, r.text
    assert r.json()['data']['task']['state'] == 'paused'
    r = await c.post(f'/api/v1/hasn-task/agent/tasks/{task_id}/resume')
    assert r.status_code == 200, r.text
    assert r.json()['data']['task']['state'] == 'scheduled'

    # run_now 仅 scheduled/paused：pending_approval 不可触发（R2 禁绕过审批）
    pending = await _create(c, '不可触发任务', 'cron', {'cron': '0 9 * * *'})
    r = await c.post(f'/api/v1/hasn-task/agent/tasks/{pending["task"]["task_id"]}/run-now')
    assert r.status_code != 200

    r = await c.post(f'/api/v1/hasn-task/agent/tasks/{task_id}/run-now')
    assert r.status_code == 200, r.text
    assert r.json()['data']['task']['next_run_at'] is not None

    # 软删后读不到
    r = await c.delete(f'/api/v1/hasn-task/agent/tasks/{task_id}')
    assert r.status_code == 200, r.text
    r = await c.get(f'/api/v1/hasn-task/agent/tasks/{task_id}')
    assert r.status_code == 404, r.text


async def test_query_results_reads_run_summary_with_artifacts(e2e: SimpleNamespace) -> None:
    c = e2e.client
    data = await _create(c, '有历史的任务', 'once', {'run_at': '2099-07-01T08:00:00+08:00'})
    task_id = data['task']['task_id']
    run_uuid = f'run_{_uid()}'
    await e2e.session.execute(
        text(
            """
            INSERT INTO hasn_task.run_summary (run_uuid, task_uuid, owner_id, agent_id, executor_node_id,
                dedupe_key, status, started_at, finished_at, output_summary, duration_ms, artifacts_json,
                created_time, updated_time)
            VALUES (:r, :t, :o, :a, 'node-x', :dk, 'success', now(), now(), '已完成：产出 1 份周报', 1200,
                CAST(:art AS jsonb), now(), now())
            """
        ),
        {
            'r': run_uuid,
            't': task_id,
            'o': e2e.owner,
            'a': e2e.agent_hasn,
            'dk': f'dk_{run_uuid}',
            'art': '[{"kind": "file", "path": "weekly.md", "asset_id": "ast_1"}]',
        },
    )

    r = await c.get(f'/api/v1/hasn-task/agent/tasks/{task_id}/results')
    assert r.status_code == 200, r.text
    results = r.json()['data']['results']
    assert len(results) == 1
    assert results[0]['run_id'] == run_uuid
    assert results[0]['output_summary'] == '已完成：产出 1 份周报'
    assert results[0]['artifacts'][0]['asset_id'] == 'ast_1'

    r = await c.get(f'/api/v1/hasn-task/agent/runs/{run_uuid}')
    assert r.status_code == 200, r.text
    assert r.json()['data']['run']['task_id'] == task_id

    r = await c.get(f'/api/v1/hasn-task/agent/tasks/{task_id}/runs')
    assert r.status_code == 200, r.text
    assert len(r.json()['data']['runs']) == 1


async def test_catalog_seed_and_free_access(e2e: SimpleNamespace) -> None:
    import sqlalchemy as sa

    from backend.app.hasn.model.hasn_app_catalog import HasnAppCatalog
    from backend.app.hasn.service.app_catalog_service import ensure_catalog_seeded, resolve_app_access

    await ensure_catalog_seeded(e2e.session)
    catalog = (
        await e2e.session.execute(sa.select(HasnAppCatalog).where(HasnAppCatalog.app_id == 'hasn_task'))
    ).scalar_one_or_none()
    assert catalog is not None, 'catalog 播种后缺 hasn_task 行'
    assert catalog.access_type == 'free'

    access = await resolve_app_access(e2e.session, catalog=catalog, owner_hasn_id=e2e.owner)
    assert access['allowed'] is True
    assert access['reason'] == 'free'


# ---------- KNOWU §4.5 风险分级闸：risk_level → 初始 status ----------


async def _risk_in_db(session: object, owner: str, task_id: str) -> str:
    """回读权威行的 risk_level 列，证实端到端事件投影真落库（非仅 projection）。"""
    row = await session.execute(  # type: ignore[attr-defined]
        text('SELECT risk_level FROM hasn_task.task WHERE owner_id = :o AND task_uuid = :t'),
        {'o': owner, 't': task_id},
    )
    return row.scalar_one()


async def test_risk_low_once_scheduled(e2e: SimpleNamespace) -> None:
    """risk=low + once → scheduled（默认场景，risk 与调度同向）。"""
    data = await _create(
        e2e.client, '低风险一次性', 'once', {'run_at': '2099-02-01T08:00:00+08:00'}, risk_level='low'
    )
    task = data['task']
    assert task['state'] == 'scheduled'
    assert task['risk_level'] == 'low'
    assert task['next_run_at'] is not None
    assert await _risk_in_db(e2e.session, e2e.owner, task['task_id']) == 'low'


async def test_risk_high_once_pending_approval(e2e: SimpleNamespace) -> None:
    """risk=high → pending_approval（即便 once 也要主人批，§7.4 外部后果闸）。"""
    data = await _create(
        e2e.client, '高风险外发', 'once', {'run_at': '2099-02-01T09:00:00+08:00'}, risk_level='high'
    )
    task = data['task']
    assert task['state'] == 'pending_approval'
    assert task['risk_level'] == 'high'
    assert task['next_run_at'] is None
    assert await _risk_in_db(e2e.session, e2e.owner, task['task_id']) == 'high'


async def test_risk_low_periodic_overrides_to_scheduled(e2e: SimpleNamespace) -> None:
    """risk=low + interval → scheduled（§7.2：低风险周期追踪任务自动跑，越过周期默认待批）。"""
    data = await _create(e2e.client, '每周复盘汇报', 'interval', {'minutes': 10080}, risk_level='low')
    task = data['task']
    assert task['state'] == 'scheduled'
    assert task['risk_level'] == 'low'
    assert task['next_run_at'] is not None
    assert await _risk_in_db(e2e.session, e2e.owner, task['task_id']) == 'low'


async def test_unspecified_risk_periodic_keeps_legacy_pending(e2e: SimpleNamespace) -> None:
    """未标 risk + interval → pending_approval（沿用既有 D4，未声明不擅自自动跑），列默认 low。"""
    data = await _create(e2e.client, '未标风险周期', 'interval', {'minutes': 1440})
    task = data['task']
    assert task['state'] == 'pending_approval'
    assert task['risk_level'] == 'low'  # 列默认（风险性质），与 status 正交
    assert await _risk_in_db(e2e.session, e2e.owner, task['task_id']) == 'low'


async def test_invalid_risk_rejected(e2e: SimpleNamespace) -> None:
    """非法 risk_level → 400（不静默吞）。"""
    r = await e2e.client.post(
        '/api/v1/hasn-task/agent/tasks',
        json={
            'name': '非法风险',
            'prompt': 'x',
            'schedule_type': 'once',
            'schedule_config': {'run_at': '2099-02-01T08:00:00+08:00'},
            'risk_level': 'medium',
        },
    )
    assert r.status_code == 400, r.text


async def test_cross_owner_isolation(e2e: SimpleNamespace) -> None:
    c = e2e.client
    data = await _create(c, '私有任务', 'once', {'run_at': '2099-08-01T08:00:00+08:00'})
    task_id = data['task']['task_id']

    # 切到另一个 owner 的 agent 身份 → 读/写恒 NotFound（不泄露存在性）
    e2e.state.agent_owner = e2e.other_owner
    try:
        r = await c.get(f'/api/v1/hasn-task/agent/tasks/{task_id}')
        assert r.status_code == 404, r.text
        r = await c.post(f'/api/v1/hasn-task/agent/tasks/{task_id}/pause')
        assert r.status_code == 404, r.text
        r = await c.get('/api/v1/hasn-task/agent/tasks')
        assert r.status_code == 200
        assert r.json()['data']['tasks'] == []
    finally:
        e2e.state.agent_owner = e2e.owner
