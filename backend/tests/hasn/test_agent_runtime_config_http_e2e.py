"""分身 hermes runtime 原生配置 进程内 HTTP E2E（真实 PG，零 mock）。

覆盖 app-scope GET/PUT /by-hasn-id/{hasn_id}/runtime-config（Owner JWT）+ provision 下行：
  1) GET 默认 → config 全 None（存量行 runtime_config_json=NULL）。
  2) PUT 覆盖式写入 4 槽模型 + 工作目录 + max_turns/网关超时/记忆开关/时区 →
     200 返回 config、落库、bump profile_revision、agent profile 出参带 runtime_config。
  3) GET 回读一致（持久化）。
  4) owner 隔离：authed 为 owner1 时访问 owner2 的 agent → 404（不泄露他人配置）。

最小 app 挂 app-scope agents 路由 + agent-scope profile 路由 + 真实 PG + override 两套鉴权；
经 ASGITransport 走完整 HTTP，事务末尾回滚不污染库。需要 export DATABASE_PORT=15432。
"""

from __future__ import annotations

import uuid

from datetime import datetime
from types import SimpleNamespace

import httpx
import pytest
import pytest_asyncio

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

from backend.app.hasn.api.v1.agent.hasn_agent_profile import router as profile_router
from backend.app.hasn.api.v1.app.hasn_agents import router as app_agents_router
from backend.app.hasn.model.hasn_agents import HasnAgents
from backend.app.hasn.model.hasn_humans import HasnHumans
from backend.common.dataclasses import AgentTokenPayload
from backend.common.exception.errors import BaseExceptionError
from backend.common.security.agent_jwt_auth import agent_jwt_auth
from backend.common.security.jwt import DependsJwtAuth
from backend.database.db import SQLALCHEMY_DATABASE_URL, get_db, get_db_transaction

pytestmark = pytest.mark.asyncio

_APP = FastAPI()
_APP.include_router(app_agents_router, prefix='/api/v1/hasn/app/agents')
_APP.include_router(profile_router, prefix='/api/v1/hasn/agent')


@_APP.exception_handler(BaseExceptionError)
async def _err_handler(_request: Request, exc: BaseExceptionError) -> JSONResponse:
    return JSONResponse(status_code=exc.code, content={'code': exc.code, 'msg': str(exc.msg), 'data': None})


def _uid() -> str:
    return uuid.uuid4().hex[:8]


@pytest_asyncio.fixture
async def e2e():
    engine = create_async_engine(SQLALCHEMY_DATABASE_URL, poolclass=NullPool)
    try:
        async with engine.connect() as conn:
            await conn.execute(select(1))
    except Exception as exc:  # noqa: BLE001
        await engine.dispose()
        pytest.skip(f'本地 PostgreSQL 不可达，跳过: {exc!r}')

    session = async_sessionmaker(engine, expire_on_commit=False)()

    tag = _uid()
    owner1 = f'h_owner1_{tag}'
    owner1_uid = 960000 + int(uuid.uuid4().int % 9000)
    owner2 = f'h_owner2_{tag}'
    owner2_uid = owner1_uid + 1
    agent1 = f'a_one_{tag}'
    agent2 = f'a_two_{tag}'

    session.add(HasnHumans(hasn_id=owner1, star_id=f's_{owner1_uid}', user_id=owner1_uid, nickname='O1', status='active'))
    session.add(HasnHumans(hasn_id=owner2, star_id=f's_{owner2_uid}', user_id=owner2_uid, nickname='O2', status='active'))
    for hasn_id, owner_id in ((agent1, owner1), (agent2, owner2)):
        session.add(
            HasnAgents(
                hasn_id=hasn_id,
                star_id=f'{owner1_uid}#{hasn_id}',
                owner_id=owner_id,
                display_name='RC Agent',
                agent_name=f'agent_{hasn_id}',
                type='desktop',
                role='specialist',
                api_key_hash='hash',
                status='active',
                created_via='client',
                profile_revision=5,
            )
        )
    await session.flush()

    async def _yield_session():
        yield session

    async def _owner1_auth(request: Request) -> None:
        request.scope['user'] = SimpleNamespace(id=owner1_uid)

    async def _agent1_auth() -> AgentTokenPayload:
        return AgentTokenPayload(
            agent_hasn_id=agent1,
            agent_name=f'agent_{agent1}',
            owner_hasn_id=owner1,
            owner_user_id=owner1_uid,
            session_uuid=f'sess_{tag}',
            expire_time=datetime(2099, 1, 1),
        )

    _APP.dependency_overrides[get_db] = _yield_session
    _APP.dependency_overrides[get_db_transaction] = _yield_session
    _APP.dependency_overrides[DependsJwtAuth.dependency] = _owner1_auth
    _APP.dependency_overrides[agent_jwt_auth] = _agent1_auth

    client = httpx.AsyncClient(transport=httpx.ASGITransport(app=_APP), base_url='http://e2e')
    try:
        yield SimpleNamespace(client=client, session=session, agent1=agent1, agent2=agent2)
    finally:
        await client.aclose()
        _APP.dependency_overrides.clear()
        await session.rollback()
        await session.close()
        await engine.dispose()


async def _get_config(client, hasn_id: str) -> httpx.Response:
    return await client.get(f'/api/v1/hasn/app/agents/by-hasn-id/{hasn_id}/runtime-config')


async def _put_config(client, hasn_id: str, payload: dict) -> httpx.Response:
    return await client.put(f'/api/v1/hasn/app/agents/by-hasn-id/{hasn_id}/runtime-config', json=payload)


_FULL = {
    'models': {'main': 'gpt-5.5', 'fast': 'gpt-5-mini', 'vision': 'gpt-4o', 'delegation': 'gpt-5-mini'},
    'working_directory': '/Users/me/projects/foo',
    'max_turns': 80,
    'gateway_timeout': 900,
    'memory_enabled': False,
    'user_profile_enabled': True,
    'timezone': 'America/New_York',
    'a2a_max_turns': 12,
}


async def test_get_default_is_all_none(e2e):
    r = await _get_config(e2e.client, e2e.agent1)
    assert r.status_code == 200, r.text
    cfg = r.json()['data']['config']
    assert cfg['models'] == {'main': None, 'fast': None, 'vision': None, 'delegation': None}
    assert cfg['working_directory'] is None
    assert cfg['max_turns'] is None
    assert cfg['gateway_timeout'] is None
    assert cfg['memory_enabled'] is None
    assert cfg['user_profile_enabled'] is None
    assert cfg['timezone'] is None
    assert cfg['a2a_max_turns'] is None


async def test_put_persists_bumps_revision_and_provisions(e2e):
    # 1) PUT 全量写入
    r = await _put_config(e2e.client, e2e.agent1, _FULL)
    assert r.status_code == 200, r.text
    cfg = r.json()['data']['config']
    assert cfg['models'] == _FULL['models']
    assert cfg['working_directory'] == _FULL['working_directory']
    assert cfg['max_turns'] == 80
    assert cfg['gateway_timeout'] == 900
    assert cfg['memory_enabled'] is False
    assert cfg['user_profile_enabled'] is True
    assert cfg['timezone'] == 'America/New_York'
    assert cfg['a2a_max_turns'] == 12

    # 2) GET 回读一致（持久化）
    r2 = await _get_config(e2e.client, e2e.agent1)
    assert r2.json()['data']['config'] == cfg

    # 3) bump profile_revision（5 → 6）+ provision 下行带 runtime_config
    row = (
        await e2e.session.execute(select(HasnAgents).where(HasnAgents.hasn_id == e2e.agent1))
    ).scalar_one()
    assert row.profile_revision == 6
    assert row.runtime_config_json['max_turns'] == 80
    assert row.runtime_config_json['a2a_max_turns'] == 12

    prof = await e2e.client.get('/api/v1/hasn/agent/profile')
    assert prof.status_code == 200, prof.text
    pdata = prof.json()['data']
    assert pdata['profile_revision'] == 6
    assert pdata['runtime_config']['models']['fast'] == 'gpt-5-mini'
    assert pdata['runtime_config']['working_directory'] == _FULL['working_directory']


async def test_partial_models_follow_main_when_omitted(e2e):
    # 只设主模型，其余槽留空 → 跟随主模型（None）
    r = await _put_config(e2e.client, e2e.agent1, {'models': {'main': 'gpt-5.5'}})
    assert r.status_code == 200, r.text
    cfg = r.json()['data']['config']
    assert cfg['models'] == {'main': 'gpt-5.5', 'fast': None, 'vision': None, 'delegation': None}


async def test_owner_isolation_other_owner_agent_404(e2e):
    # authed 为 owner1，访问 owner2 的 agent → 404（不泄露/不可改他人配置）
    r_get = await _get_config(e2e.client, e2e.agent2)
    assert r_get.status_code == 404, r_get.text
    r_put = await _put_config(e2e.client, e2e.agent2, _FULL)
    assert r_put.status_code == 404, r_put.text
