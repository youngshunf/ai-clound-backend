"""双形态 Runtime 云端侧 进程内 HTTP E2E（真实 PG，零 mock）。

覆盖（设计 08/02 §5 + 实施 01 阶段 B）：
  1) runtime_location 落库：create_cloud_first(runtime_location='cloud') → 行落 'cloud'；
     默认（不传）→ 'local'；快照出参带 runtime_location。
  2) profile 下行：GET /api/v1/hasn/agent/profile 出参带 runtime_location（供 daemon read-through）。
  3) 派发代理闸门（POST /api/v1/hasn/agent/runtime/runs，Agent JWT）：
     - local 分身走此面 → 403（应经本地 sidecar 派发）；
     - cloud 分身 runtime_profile_id 空 → 400；
     - owner 不匹配（JWT owner ≠ 行 owner）→ 403；
     - cloud 分身 + 合法 profile_id + 上游不可达 → 200 SSE，但 body 为 `event: error`
       + runtime_unavailable（零 fake：不伪造成功，如实下行错误事件）。

数据面真实联调（POST /v1/runs + SSE 回帧）依赖已部署的云端 runtime + Docker 沙箱，属
infra-gated，留阶段 F 活体验证。需要 export DATABASE_PORT=15432。
"""

from __future__ import annotations

import uuid

from datetime import datetime
from types import SimpleNamespace
from typing import Any

import httpx
import pytest
import pytest_asyncio

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

from backend.app.hasn.api.v1.agent.hasn_agent_profile import router as profile_router
from backend.app.hasn.api.v1.agent.hasn_agent_runtime import router as runtime_router
from backend.app.hasn.model.hasn_agents import HasnAgents
from backend.app.hasn.model.hasn_humans import HasnHumans
from backend.app.hasn.schema.hasn_agents import CloudCreateAgentRequest
from backend.app.hasn.service.hasn_agent_runtime_dispatch_service import (
    hasn_agent_runtime_dispatch_service,
)
from backend.app.hasn.service.hasn_agents_service import _merge_agent_create_payload, agent_profile_service
from backend.app.hermes.service.hermes_runtime_client import HermesRuntimeClient
from backend.common.dataclasses import AgentTokenPayload
from backend.common.exception.errors import BaseExceptionError
from backend.common.security.agent_jwt_auth import agent_jwt_auth
from backend.database.db import SQLALCHEMY_DATABASE_URL, get_db, get_db_transaction

pytestmark = pytest.mark.asyncio

_APP = FastAPI()
_APP.include_router(runtime_router, prefix='/api/v1/hasn/agent/runtime')
_APP.include_router(profile_router, prefix='/api/v1/hasn/agent')


@_APP.exception_handler(BaseExceptionError)
async def _err_handler(_request: Request, exc: BaseExceptionError) -> JSONResponse:
    return JSONResponse(status_code=exc.code, content={'code': exc.code, 'msg': str(exc.msg), 'data': None})


def _uid() -> str:
    return uuid.uuid4().hex[:8]


def _offline_client() -> HermesRuntimeClient:
    """真正断开上游的 client：构造器对 base_url='' 会回落服务目录（dev 机上可能解析到
    一个真实在应答的 hermes 端点），这里构造后强制置空 base_url，确保 `_request` 命中
    「base_url 未配置」守卫、稳定抛 runtime_unavailable，不依赖本机是否跑着 hermes。"""
    client = HermesRuntimeClient()
    client.base_url = ''
    return client


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
    owner2 = f'h_owner2_{tag}'
    owner2_uid = owner1_uid + 1
    agent_cloud = f'a_cloud_{tag}'
    agent_cloud_peer = f'a_cloud_peer_{tag}'
    agent_local = f'a_local_{tag}'
    cloud_profile = f's_{owner1_uid}-agent_{agent_cloud}'
    cloud_peer_profile = f's_{owner1_uid}-agent_{agent_cloud_peer}'
    other_owner_profile = f's_{owner2_uid}-agent_other_{tag}'

    session.add(HasnHumans(hasn_id=owner1, star_id=f's_{owner1_uid}', user_id=owner1_uid, nickname=f'O1_{tag}', status='active'))
    session.add(HasnHumans(hasn_id=owner2, star_id=f's_{owner2_uid}', user_id=owner2_uid, nickname=f'O2_{tag}', status='active'))
    session.add(
        HasnAgents(
            hasn_id=agent_cloud, star_id=f'{owner1_uid}#cloud', owner_id=owner1, display_name='Cloud Agent',
            agent_name=f'agent_{agent_cloud}', type='cloud', runtime_location='cloud', role='specialist',
            api_key_hash='hash', status='active', created_via='client', profile_revision=3,
        )
    )
    session.add(
        HasnAgents(
            hasn_id=agent_local, star_id=f'{owner1_uid}#local', owner_id=owner1, display_name='Local Agent',
            agent_name=f'agent_{agent_local}', type='desktop', runtime_location='local', role='specialist',
            api_key_hash='hash', status='active', created_via='client', profile_revision=3,
        )
    )
    session.add(
        HasnAgents(
            hasn_id=agent_cloud_peer, star_id=f'{owner1_uid}#cloud-peer', owner_id=owner1,
            display_name='Cloud Peer', agent_name=f'agent_{agent_cloud_peer}', type='cloud',
            runtime_location='cloud', role='specialist', api_key_hash='hash', status='active',
            created_via='client', profile_revision=3,
        )
    )
    await session.flush()

    # 可变身份持有器：每个测试用例切换 JWT 身份（agent_hasn_id/owner_hasn_id）。
    ident = SimpleNamespace(agent_hasn_id=agent_cloud, owner_hasn_id=owner1)

    async def _yield_session():
        yield session

    async def _agent_auth() -> AgentTokenPayload:
        return AgentTokenPayload(
            agent_hasn_id=ident.agent_hasn_id,
            agent_name='agent',
            owner_hasn_id=ident.owner_hasn_id,
            owner_user_id=owner1_uid,
            session_uuid=f'sess_{tag}',
            expire_time=datetime(2099, 1, 1),
        )

    _APP.dependency_overrides[get_db] = _yield_session
    _APP.dependency_overrides[get_db_transaction] = _yield_session
    _APP.dependency_overrides[agent_jwt_auth] = _agent_auth

    client = httpx.AsyncClient(transport=httpx.ASGITransport(app=_APP), base_url='http://e2e')
    try:
        yield SimpleNamespace(
            client=client, session=session, ident=ident,
            owner1=owner1, owner2=owner2, agent_cloud=agent_cloud,
            agent_cloud_peer=agent_cloud_peer, agent_local=agent_local,
            cloud_profile=cloud_profile, cloud_peer_profile=cloud_peer_profile,
            other_owner_profile=other_owner_profile,
        )
    finally:
        await client.aclose()
        _APP.dependency_overrides.clear()
        await session.rollback()
        await session.close()
        await engine.dispose()


async def test_create_persists_runtime_location_cloud_and_default(e2e) -> None:
    # 走真实创建链路的 flush-only 段（gateway.create_agent，回滚不污染库），断言
    # request → payload 透传 + ORM 落库。create_cloud_first 会 commit（外层端点收口），
    # 这里只验位置落库这一关键不变量，避免提交泄漏。
    # 1) 显式 cloud → payload 带 cloud + ORM 落 cloud
    req_cloud = CloudCreateAgentRequest(
        owner_id=e2e.owner1, display_name=f'位置云{_uid()}', agent_name=None, runtime_location='cloud',
    )
    payload_cloud = _merge_agent_create_payload(req_cloud, None)
    assert payload_cloud['runtime_location'] == 'cloud'
    agent_cloud, _, _ = await agent_profile_service.gateway.create_agent(e2e.session, payload_cloud)
    assert agent_cloud.runtime_location == 'cloud'

    # 2) 不传 → payload 默认 local + ORM 落 local
    req_default = CloudCreateAgentRequest(owner_id=e2e.owner1, display_name=f'位置默认{_uid()}', agent_name=None)
    payload_default = _merge_agent_create_payload(req_default, None)
    assert payload_default['runtime_location'] == 'local'
    agent_default, _, _ = await agent_profile_service.gateway.create_agent(e2e.session, payload_default)
    assert agent_default.runtime_location == 'local'


async def test_profile_downlink_includes_runtime_location(e2e) -> None:
    e2e.ident.agent_hasn_id = e2e.agent_cloud
    e2e.ident.owner_hasn_id = e2e.owner1
    r = await e2e.client.get('/api/v1/hasn/agent/profile')
    assert r.status_code == 200, r.text
    assert r.json()['data']['runtime_location'] == 'cloud'


async def test_relay_local_agent_forbidden(e2e) -> None:
    e2e.ident.agent_hasn_id = e2e.agent_local
    e2e.ident.owner_hasn_id = e2e.owner1
    r = await e2e.client.post(
        '/api/v1/hasn/agent/runtime/runs',
        json={'runtime_profile_id': '100001-local', 'payload': {'input': 'hi'}},
    )
    assert r.status_code == 403, r.text


async def test_relay_empty_profile_id_400(e2e) -> None:
    e2e.ident.agent_hasn_id = e2e.agent_cloud
    e2e.ident.owner_hasn_id = e2e.owner1
    r = await e2e.client.post(
        '/api/v1/hasn/agent/runtime/runs',
        json={'runtime_profile_id': '   ', 'payload': {'input': 'hi'}},
    )
    assert r.status_code == 400, r.text


async def test_relay_owner_mismatch_forbidden(e2e) -> None:
    # JWT 声称是 cloud 分身但 owner 写成 owner2（≠ 行 owner1）→ 403（防越权派发）
    e2e.ident.agent_hasn_id = e2e.agent_cloud
    e2e.ident.owner_hasn_id = e2e.owner2
    r = await e2e.client.post(
        '/api/v1/hasn/agent/runtime/runs',
        json={'runtime_profile_id': '100001-cloud', 'payload': {'input': 'hi'}},
    )
    assert r.status_code == 403, r.text


@pytest.mark.parametrize('foreign_profile', ['cloud_peer_profile', 'other_owner_profile'])
async def test_relay_foreign_profile_forbidden_before_runtime_access(e2e, foreign_profile: str) -> None:
    e2e.ident.agent_hasn_id = e2e.agent_cloud
    e2e.ident.owner_hasn_id = e2e.owner1
    r = await e2e.client.post(
        '/api/v1/hasn/agent/runtime/runs',
        json={'runtime_profile_id': getattr(e2e, foreign_profile), 'payload': {'input': 'hi'}},
    )
    assert r.status_code == 403, r.text


async def test_relay_cloud_unreachable_yields_sse_error_not_fake_success(e2e, monkeypatch) -> None:
    # cloud 分身 + 合法 profile_id，但上游 runtime 不可达（base_url 空）→ 200 SSE，
    # body 为 event: error + runtime_unavailable（零 fake：不伪造成功）。
    monkeypatch.setattr(hasn_agent_runtime_dispatch_service, 'runtime_client', _offline_client())
    e2e.ident.agent_hasn_id = e2e.agent_cloud
    e2e.ident.owner_hasn_id = e2e.owner1
    r = await e2e.client.post(
        '/api/v1/hasn/agent/runtime/runs',
        json={'runtime_profile_id': e2e.cloud_profile, 'payload': {'input': 'hi'}},
    )
    assert r.status_code == 200, r.text
    assert 'event: error' in r.text
    assert 'runtime_unavailable' in r.text


# ---- 云端 runtime 健康端点 GET /api/v1/hasn/agent/runtime/health（修「云端分身显示离线」）----


async def test_health_local_agent_forbidden(e2e) -> None:
    # local 分身查云端健康面 → 403（应经本地探活，不走云端健康面）。
    e2e.ident.agent_hasn_id = e2e.agent_local
    e2e.ident.owner_hasn_id = e2e.owner1
    r = await e2e.client.get(
        '/api/v1/hasn/agent/runtime/health', params={'runtime_profile_id': '100001-local'}
    )
    assert r.status_code == 403, r.text


async def test_health_owner_mismatch_forbidden(e2e) -> None:
    # JWT owner ≠ 行 owner → 403（防越权读别 owner 分身的健康）。
    e2e.ident.agent_hasn_id = e2e.agent_cloud
    e2e.ident.owner_hasn_id = e2e.owner2
    r = await e2e.client.get(
        '/api/v1/hasn/agent/runtime/health', params={'runtime_profile_id': e2e.cloud_profile}
    )
    assert r.status_code == 403, r.text


async def test_health_cloud_unreachable_reports_offline_not_fake(e2e, monkeypatch) -> None:
    # 控制面不可达（base_url 空）→ online=False, health=offline（零 fake：如实报离线，不伪造在线）。
    monkeypatch.setattr(hasn_agent_runtime_dispatch_service, 'runtime_client', _offline_client())
    e2e.ident.agent_hasn_id = e2e.agent_cloud
    e2e.ident.owner_hasn_id = e2e.owner1
    r = await e2e.client.get(
        '/api/v1/hasn/agent/runtime/health', params={'runtime_profile_id': e2e.cloud_profile}
    )
    assert r.status_code == 200, r.text
    data = r.json()['data']
    assert data['online'] is False
    assert data['health'] == 'offline'


async def test_health_cloud_reachable_reports_online() -> None:
    # 控制面可达 → online=True, health=ok（对齐「关机后仍在线」：冷网关不判降级）。
    # 用「指向 stub 上游」的真实 client 走 service 映射逻辑；真机上游属 infra-gated。
    class _StubReachableClient:
        async def probe(self, trace_id: str | None = None) -> dict[str, str]:
            return {'status': 'online'}  # 控制面 200

        async def get_gateway_status(self, runtime_profile_id: str, trace_id: str | None = None) -> dict[str, bool]:
            return {'running': False}  # 网关懒启动未起 → detail 标 idle，但不降级

    runtime_client: Any = _StubReachableClient()
    svc = hasn_agent_runtime_dispatch_service.__class__(runtime_client=runtime_client)
    result = await svc.cloud_runtime_health(runtime_profile_id='100001-cloud')
    assert result['online'] is True
    assert result['health'] == 'ok'
    assert result['detail'] == 'gateway_idle'
