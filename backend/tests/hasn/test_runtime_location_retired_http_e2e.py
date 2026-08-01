"""H8「云端 Runtime 与云端分身下线」反向守卫 · 进程内 HTTP E2E（真实 PG，零 mock）。

本文件的前身是双形态 Runtime 的云端派发面 E2E（`test_agent_runtime_dispatch_http_e2e.py`）。
`/api/v1/hasn/agent/runtime/*` 代理面与 `hasn_agent_runtime_{dispatch,provision}_service`
已随「分身跑在云端沙箱」形态整体删除，那些端点闸门/SSE 中继断言随形态消失一并移除。

保留并加固的是**不随形态消失的两条语义**：
  1. 反向守卫（防回潮）：创建分身恒落 `runtime_location='local'`——创建入参不再有该字段，
     即便客户端残留着 `runtime_location='cloud'` 也不得被写回库；
  2. 读模型保留：`GET /api/v1/hasn/agent/profile` 仍下发 `runtime_location`
     （daemon read-through 依赖），且能如实读出**存量** cloud 行——列保留就是为了这个，
     拆除不得把存量数据读成 500 或静默改写。

需要 export DATABASE_PORT=15432。
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
from backend.app.hasn.model.hasn_agents import HasnAgents
from backend.app.hasn.model.hasn_humans import HasnHumans
from backend.app.hasn.schema.hasn_agents import CloudCreateAgentRequest
from backend.app.hasn.service.hasn_agents_service import _merge_agent_create_payload, agent_profile_service
from backend.common.dataclasses import AgentTokenPayload
from backend.common.exception.errors import BaseExceptionError
from backend.common.security.agent_jwt_auth import agent_jwt_auth
from backend.database.db import SQLALCHEMY_DATABASE_URL, get_db, get_db_transaction

pytestmark = pytest.mark.asyncio

_APP = FastAPI()
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
    except Exception as exc:
        await engine.dispose()
        pytest.skip(f'本地 PostgreSQL 不可达，跳过: {exc!r}')

    session = async_sessionmaker(engine, expire_on_commit=False)()

    tag = _uid()
    owner1 = f'h_owner1_{tag}'
    owner1_uid = 1_200_000_000 + int(uuid.uuid4().int % 800_000_000)
    agent_legacy_cloud = f'a_legacy_{tag}'

    session.add(
        HasnHumans(hasn_id=owner1, star_id=f's_{owner1_uid}', user_id=owner1_uid, nickname=f'O1_{tag}', status='active')
    )
    # 存量行：H8 之前落库的 cloud 分身。迁移会把它就地改 local，但读路径必须对任何取值都成立，
    # 所以这里刻意直接写 'cloud'，锁「列保留 = 存量可读」这一不变量。
    session.add(
        HasnAgents(
            hasn_id=agent_legacy_cloud, star_id=f'{owner1_uid}#legacy', owner_id=owner1,
            display_name='Legacy Cloud Agent', agent_name=f'agent_{agent_legacy_cloud}',
            type='cloud', runtime_location='cloud', role='specialist',
            api_key_hash='hash', status='active', created_via='client', profile_revision=3,
        )
    )
    await session.flush()

    async def _yield_session():
        yield session

    async def _agent_auth() -> AgentTokenPayload:
        return AgentTokenPayload(
            agent_hasn_id=agent_legacy_cloud,
            agent_name='agent',
            owner_hasn_id=owner1,
            owner_user_id=owner1_uid,
            session_uuid=f'sess_{tag}',
            expire_time=datetime(2099, 1, 1),
        )

    _APP.dependency_overrides[get_db] = _yield_session
    _APP.dependency_overrides[get_db_transaction] = _yield_session
    _APP.dependency_overrides[agent_jwt_auth] = _agent_auth

    client = httpx.AsyncClient(transport=httpx.ASGITransport(app=_APP), base_url='http://e2e')
    try:
        yield SimpleNamespace(client=client, session=session, owner1=owner1, agent_legacy_cloud=agent_legacy_cloud)
    finally:
        await client.aclose()
        _APP.dependency_overrides.clear()
        await session.rollback()
        await session.close()
        await engine.dispose()


async def test_create_request_has_no_runtime_location_field() -> None:
    """反向守卫①：创建入参 schema 上不再有 runtime_location——形态已下线，字段不得回潮。"""
    assert 'runtime_location' not in CloudCreateAgentRequest.model_fields


async def test_create_always_persists_local_even_if_client_sends_cloud(e2e) -> None:
    """反向守卫②：客户端残留字段传 cloud 也写不进库——payload 与 ORM 行恒 local。

    走真实创建链路的 flush-only 段（gateway.create_agent，回滚不污染库）。
    """
    # 客户端残留 runtime_location='cloud'：Pydantic 已无该字段，额外键被丢弃，不进 payload。
    req_stale = CloudCreateAgentRequest.model_validate(
        {'owner_id': e2e.owner1, 'display_name': f'位置回潮{_uid()}', 'agent_name': None, 'runtime_location': 'cloud'}
    )
    assert not hasattr(req_stale, 'runtime_location')

    payload_stale = _merge_agent_create_payload(req_stale, None)
    assert payload_stale['runtime_location'] == 'local'
    agent_stale, _, _ = await agent_profile_service.gateway.create_agent(e2e.session, payload_stale)
    assert agent_stale.runtime_location == 'local'

    # 常规创建（不传任何位置）同样落 local。
    req_default = CloudCreateAgentRequest(owner_id=e2e.owner1, display_name=f'位置默认{_uid()}', agent_name=None)
    payload_default = _merge_agent_create_payload(req_default, None)
    assert payload_default['runtime_location'] == 'local'
    agent_default, _, _ = await agent_profile_service.gateway.create_agent(e2e.session, payload_default)
    assert agent_default.runtime_location == 'local'


async def test_profile_downlink_still_reads_legacy_cloud_row(e2e) -> None:
    """读模型保留：profile 下行仍带 runtime_location，且存量 cloud 行如实读出、不报错不改写。"""
    r = await e2e.client.get('/api/v1/hasn/agent/profile')
    assert r.status_code == 200, r.text
    assert r.json()['data']['runtime_location'] == 'cloud'
