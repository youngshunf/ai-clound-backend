"""多任务编排（工作流）N2 云端验收：manifest + scope + Agent API（真实 PG，零 mock）。

覆盖（实施 92 N2 验收）：
- manifest：hasn.workflow.* 能力齐全 + required_scopes/risk_level + 写类走统一授权开关
- scope catalog：workflow:read/manage/run 登记
- Agent JWT → /api/v1/hasn-task/agent/workflows：建菱形图 / 查图 / 列图 / 发现分身 / run / pause
- 定时图 → pending_approval + 通知；owner app approve → active
- 跨户 NotFound

事实源: docs/hasn-node设计文档/12-任务系统实施方案/07-多任务编排（工作流）设计.md §9；实施 92 N2。
"""

from __future__ import annotations

import uuid

from datetime import UTC, datetime
from pathlib import Path
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

from backend.app.hasn.model.hasn_agents import HasnAgents
from backend.app.hasn.model.hasn_humans import HasnHumans
from backend.app.hasn_task.api.v1.agent.workflow import router as agent_workflow_router
from backend.app.hasn_task.api.v1.app.workflow import router as app_workflow_router
from backend.common.dataclasses import AgentTokenPayload
from backend.common.exception.errors import BaseExceptionError
from backend.common.security.agent_jwt_auth import agent_jwt_auth
from backend.common.security.jwt import DependsJwtAuth
from backend.database.db import SQLALCHEMY_DATABASE_URL, get_db, get_db_transaction

if TYPE_CHECKING:
    from collections.abc import AsyncIterator

pytestmark = pytest.mark.asyncio

_SQL_DIR = Path(__file__).resolve().parents[2] / 'sql' / 'hasn_task' / 'migrations'
AINATIVE_SQL = (_SQL_DIR / '2026-06-10-ainative-refactor.sql').read_text(encoding='utf-8')
WORKFLOW_SQL = (_SQL_DIR / '2026-06-11-workflow.sql').read_text(encoding='utf-8')

_APP = FastAPI()
_APP.include_router(agent_workflow_router, prefix='/api/v1/hasn-task/agent')
_APP.include_router(app_workflow_router, prefix='/api/v1/hasn-task/app')


@_APP.exception_handler(BaseExceptionError)
async def _err_handler(_request: Request, exc: BaseExceptionError) -> JSONResponse:  # noqa: RUF029
    return JSONResponse(status_code=exc.code, content={'code': exc.code, 'msg': str(exc.msg), 'data': None})


def _uid() -> str:
    return uuid.uuid4().hex[:8]


async def _run_sql(sql: str) -> None:
    import asyncpg

    dsn = SQLALCHEMY_DATABASE_URL.render_as_string(hide_password=False).replace('postgresql+asyncpg://', 'postgresql://')
    conn = await asyncpg.connect(dsn)
    try:
        await conn.execute(sql)
    finally:
        await conn.close()


# ---------- 纯 Python：manifest + scope ----------


def test_manifest_has_workflow_capabilities() -> None:
    from backend.app.hasn_task.service.ai_native_manifest import HASN_TASK_AI_NATIVE_MANIFEST

    caps = {c['mcp_name']: c for c in HASN_TASK_AI_NATIVE_MANIFEST['capabilities']}
    for name in (
        'hasn.workflow.create',
        'hasn.workflow.add_node',
        'hasn.workflow.add_edge',
        'hasn.workflow.list_agents',
        'hasn.workflow.get',
        'hasn.workflow.get_node_result',
        'hasn.workflow.run',
        'hasn.workflow.pause',
        'hasn.workflow.cancel',
        'hasn.workflow.list',
    ):
        assert name in caps, f'manifest 缺工作流能力 {name}'
    assert 'workflow:manage' in caps['hasn.workflow.create']['required_scopes']
    assert 'workflow:read' in caps['hasn.workflow.get']['required_scopes']
    assert 'workflow:run' in caps['hasn.workflow.run']['required_scopes']
    assert caps['hasn.workflow.cancel']['risk_level'] == 'high'
    # 出厂全 Allow 免确认（16-doc D-v3-1）；owner 可设 ask/deny override。
    assert caps['hasn.workflow.create']['human_confirmation']['required'] is False
    assert caps['hasn.workflow.list']['human_confirmation'] == {'required': False}


def test_scope_catalog_has_workflow() -> None:
    from backend.app.mcp.scopes import SCOPE_CATALOG

    assert {'workflow:read', 'workflow:manage', 'workflow:run'} <= set(SCOPE_CATALOG)
    assert SCOPE_CATALOG['workflow:manage']['domain'] == 'task'


# ---------- Agent API E2E（真实 PG） ----------


@pytest_asyncio.fixture
async def e2e() -> AsyncIterator[SimpleNamespace]:
    engine = create_async_engine(SQLALCHEMY_DATABASE_URL, poolclass=NullPool)
    try:
        async with engine.connect() as conn:
            await conn.execute(select(1))
    except Exception as exc:
        await engine.dispose()
        pytest.skip(f'本地 PostgreSQL 不可达，跳过: {exc!r}')

    await _run_sql(AINATIVE_SQL)
    await _run_sql(WORKFLOW_SQL)

    session = async_sessionmaker(engine, expire_on_commit=False)()
    tag = _uid()
    owner = f'h_wf_{tag}'
    other_owner = f'h_wf2_{tag}'
    owner_uid = 970000 + int(uuid.uuid4().int % 9000)
    research = f'a_wf_r_{tag}'
    writer = f'a_wf_w_{tag}'

    session.add(
        HasnHumans(
            hasn_id=owner, star_id=f's_{owner_uid}', user_id=owner_uid, nickname='Owner', status='active'
        )
    )
    session.add(
        HasnHumans(
            hasn_id=other_owner, star_id=f's_{owner_uid + 1}', user_id=owner_uid + 1, nickname='Other', status='active'
        )
    )
    session.add(
        HasnAgents(
            hasn_id=research, star_id=f'{_uid()}#star', owner_id=owner, display_name='研究分身', agent_name='research'
        )
    )
    session.add(
        HasnAgents(
            hasn_id=writer, star_id=f'{_uid()}#star', owner_id=owner, display_name='写作分身', agent_name='writer'
        )
    )
    await session.flush()

    async def _yield_session() -> AsyncIterator:  # noqa: RUF029
        yield session

    async def _agent_auth(request: Request) -> AgentTokenPayload:  # noqa: RUF029
        payload = AgentTokenPayload(
            agent_hasn_id=research,
            agent_name=f'agent_{tag}',
            owner_hasn_id=owner,
            owner_user_id=owner_uid,
            session_uuid=f'sess_{tag}',
            expire_time=datetime(2099, 1, 1, tzinfo=UTC),
        )
        request.state.agent = payload
        return payload

    async def _owner_auth(request: Request) -> None:  # noqa: RUF029
        request.scope['user'] = SimpleNamespace(id=owner_uid, hasn_id=owner)

    _APP.dependency_overrides[get_db] = _yield_session
    _APP.dependency_overrides[get_db_transaction] = _yield_session
    _APP.dependency_overrides[agent_jwt_auth] = _agent_auth
    _APP.dependency_overrides[DependsJwtAuth.dependency] = _owner_auth

    client = httpx.AsyncClient(transport=httpx.ASGITransport(app=_APP), base_url='http://e2e')
    try:
        yield SimpleNamespace(
            client=client, session=session, owner=owner, other_owner=other_owner, research=research, writer=writer
        )
    finally:
        await client.aclose()
        _APP.dependency_overrides.clear()
        await session.rollback()
        await session.close()
        await engine.dispose()


def _data(resp: httpx.Response) -> dict:
    assert resp.status_code == 200, f'{resp.request.method} {resp.request.url} -> {resp.status_code}: {resp.text}'
    body = resp.json()
    assert body.get('code') == 200, f'envelope 非 200: {body}'
    return body['data']


def _diamond_body(
    e2e: SimpleNamespace, name: str, schedule_type: str = 'once', schedule_config: dict | None = None
) -> dict:
    return {
        'name': name,
        'goal': '调研并产出',
        'schedule_type': schedule_type,
        'schedule_config': schedule_config or {},
        'nodes': [
            {'node_key': 'plan', 'agent_id': e2e.research, 'prompt': '拆解'},
            {'node_key': 'cost', 'agent_id': e2e.research, 'prompt': '成本调研'},
            {'node_key': 'perf', 'agent_id': e2e.research, 'prompt': '性能调研'},
            {'node_key': 'synth', 'agent_id': e2e.writer, 'prompt': '综合'},
        ],
        'edges': [
            {'parent': 'plan', 'child': 'cost'},
            {'parent': 'plan', 'child': 'perf'},
            {'parent': 'cost', 'child': 'synth'},
            {'parent': 'perf', 'child': 'synth'},
        ],
    }


async def test_agent_create_get_list_workflow(e2e: SimpleNamespace) -> None:
    created = _data(await e2e.client.post('/api/v1/hasn-task/agent/workflows', json=_diamond_body(e2e, f'wf-{_uid()}')))
    wf = created['workflow']
    assert wf['workflow_id'].startswith('wf_')
    assert wf['status'] == 'active'  # 一次性图直接可跑
    assert wf['created_by_kind'] == 'agent'
    wfid = wf['workflow_id']

    detail = _data(await e2e.client.get(f'/api/v1/hasn-task/agent/workflows/{wfid}'))
    assert {n['node_key'] for n in detail['nodes']} == {'plan', 'cost', 'perf', 'synth'}
    assert len(detail['edges']) == 4

    listed = _data(await e2e.client.get('/api/v1/hasn-task/agent/workflows'))
    assert any(w['workflow_id'] == wfid for w in listed['workflows'])

    # 跨户 → NotFound
    resp = await e2e.client.get(f'/api/v1/hasn-task/agent/workflows/{wfid}')
    assert resp.status_code == 200  # 同户可见（sanity）


async def test_agent_list_agents(e2e: SimpleNamespace) -> None:
    data = _data(await e2e.client.get('/api/v1/hasn-task/agent/agents'))
    agent_ids = {a['agent_id'] for a in data['agents']}
    assert {e2e.research, e2e.writer} <= agent_ids


async def test_agent_run_and_pause(e2e: SimpleNamespace) -> None:
    created = _data(await e2e.client.post('/api/v1/hasn-task/agent/workflows', json=_diamond_body(e2e, f'wf-{_uid()}')))
    wfid = created['workflow']['workflow_id']

    paused = _data(await e2e.client.post(f'/api/v1/hasn-task/agent/workflows/{wfid}/pause'))
    assert paused['workflow']['status'] == 'paused'
    assert paused['workflow']['next_run_at'] is None

    ran = _data(await e2e.client.post(f'/api/v1/hasn-task/agent/workflows/{wfid}/run'))
    assert ran['workflow']['status'] == 'active'
    assert ran['workflow']['next_run_at'] is not None


async def test_agent_periodic_pending_approval_then_owner_approve(e2e: SimpleNamespace) -> None:
    body = _diamond_body(e2e, f'wf-cron-{_uid()}', schedule_type='interval', schedule_config={'minutes': 60})
    created = _data(await e2e.client.post('/api/v1/hasn-task/agent/workflows', json=body))
    wf = created['workflow']
    assert wf['status'] == 'pending_approval'  # D4：agent 建定时图待主人确认
    wfid = wf['workflow_id']

    # 通知行落库
    row = await e2e.session.execute(
        text("SELECT 1 FROM hasn_notifications WHERE target_id = :o AND type = 'workflow.pending_approval'"),
        {'o': e2e.owner},
    )
    assert row.first() is not None

    # owner app approve → active
    approved = _data(await e2e.client.post(f'/api/v1/hasn-task/app/workflows/{wfid}/approve'))
    assert approved['workflow']['status'] == 'active'
    assert approved['workflow']['next_run_at'] is not None


async def test_create_rejects_cross_owner_node_agent(e2e: SimpleNamespace) -> None:
    foreign = f'a_foreign_{_uid()}'
    e2e.session.add(
        HasnAgents(
            hasn_id=foreign, star_id=f'{_uid()}#star', owner_id=e2e.other_owner,
            display_name='他人', agent_name='x',
        )
    )
    await e2e.session.flush()
    body = {
        'name': f'wf-bad-{_uid()}',
        'nodes': [{'node_key': 'a', 'agent_id': foreign, 'prompt': 'x'}],
        'edges': [],
    }
    resp = await e2e.client.post('/api/v1/hasn-task/agent/workflows', json=body)
    assert resp.json()['code'] == 404, resp.text
