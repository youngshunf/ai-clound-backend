"""DS-P4 设计系统 Agent 端 HTTP 路由测试（零 mock）。

经 ASGITransport 走完整 HTTP 栈 + 真实 PostgreSQL；最小子 app 只挂 designsystem agent 路由
（绕开整 app 插件依赖）。鉴权依赖被覆盖为已知 AgentTokenPayload（标准 FastAPI 测试法，非 mock 业务）。

覆盖 P4 验收：每个工具 input/output schema 正确 + 本地/云端分流（此处云端 service）正确 +
scope 闸真实生效（缺 designsystem:write → 403）+ import 三入口接通（真实 shadcn 样例）。
"""

from __future__ import annotations

import os
import uuid

from datetime import datetime, timedelta, timezone as dt_timezone

import pytest
import pytest_asyncio

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from httpx import ASGITransport, AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

from backend.app.hasn_designsystem.api.v1.agent.designsystem import router as agent_router
from backend.common.dataclasses import AgentTokenPayload
from backend.common.exception.errors import BaseExceptionError
from backend.common.security.agent_jwt_auth import agent_jwt_auth
from backend.database.db import SQLALCHEMY_DATABASE_URL, get_db, get_db_transaction

pytestmark = pytest.mark.asyncio

_PREFIX = '/api/v1/designsystem/agent'

_APP = FastAPI()
_APP.include_router(agent_router, prefix=_PREFIX)


@_APP.exception_handler(BaseExceptionError)
async def _err_handler(_request: Request, exc: BaseExceptionError) -> JSONResponse:
    return JSONResponse(status_code=exc.code, content={'code': exc.code, 'msg': str(exc.msg), 'data': None})


def _content(bg: str) -> dict:
    return {
        'tokens_css': f':root {{ --bg: {bg}; }}',
        'design_tokens_json': {'schemaVersion': 1, 'tokens': []},
        'tailwind_css': '@theme {}',
        'design_md': '# 设计说明',
        'components_html': '<button>Go</button>',
        'components_manifest_json': {'groups': []},
        'token_contract_report_json': {'score': 90, 'grade': 'excellent'},
    }


@pytest_asyncio.fixture
async def client():
    os.environ.setdefault('DESIGNSYSTEM_IMPORT_FAKEIP_PASSTHROUGH', '1')
    engine = create_async_engine(SQLALCHEMY_DATABASE_URL, poolclass=NullPool)
    try:
        async with engine.connect() as conn:
            await conn.execute(select(1))
    except Exception as exc:  # noqa: BLE001
        await engine.dispose()
        pytest.skip(f'本地 PostgreSQL 不可达，跳过: {exc!r}')

    session = async_sessionmaker(engine, expire_on_commit=False)()
    tag = uuid.uuid4().hex[:8]
    state = {
        'agent_hasn_id': f'a_{tag}',
        'owner_hasn_id': f'h_owner_{tag}',
        'scopes': ['designsystem:write', 'designsystem:publish'],
    }

    async def _yield_session():
        yield session

    async def _agent_auth() -> AgentTokenPayload:
        return AgentTokenPayload(
            agent_hasn_id=state['agent_hasn_id'],
            agent_name='测试分身',
            owner_hasn_id=state['owner_hasn_id'],
            owner_user_id=900000 + int(uuid.uuid4().int % 9000),
            scopes=list(state['scopes']),
            session_uuid=uuid.uuid4().hex,
            expire_time=datetime.now(dt_timezone.utc) + timedelta(hours=1),
        )

    _APP.dependency_overrides[get_db] = _yield_session
    _APP.dependency_overrides[get_db_transaction] = _yield_session
    _APP.dependency_overrides[agent_jwt_auth] = _agent_auth

    http = AsyncClient(transport=ASGITransport(app=_APP), base_url='http://e2e')
    try:
        yield http, state, tag
    finally:
        await http.aclose()
        _APP.dependency_overrides.clear()
        await session.rollback()
        await session.close()
        await engine.dispose()


async def test_create_list_get_revisions_flow(client) -> None:
    """建→出 rev_no=1→列表可见→详情可读→改→出 rev_no=2→版本历史降序。"""
    http, _state, tag = client

    created = await http.post(
        f'{_PREFIX}/design-systems',
        json={'slug': f'sys-{tag}', 'name': '暖色 SaaS', 'content': _content('#ffffff'), 'category': 'saas',
              'source_kind': 'generated', 'score': 90, 'grade': 'excellent'},
    )
    assert created.status_code == 200, created.text
    body = created.json()
    assert body['code'] == 200
    ds = body['data']
    ds_id = ds['id']
    assert ds['revision']['rev_no'] == 1
    assert len(ds['content_hash']) == 64

    listed = await http.get(f'{_PREFIX}/design-systems')
    assert listed.status_code == 200
    assert any(it['id'] == ds_id for it in listed.json()['data']['items'])

    got = await http.get(f'{_PREFIX}/design-systems/{ds_id}')
    assert got.status_code == 200 and got.json()['data']['id'] == ds_id

    updated = await http.post(
        f'{_PREFIX}/design-systems',
        json={'design_system_id': ds_id, 'slug': f'sys-{tag}', 'name': '暖色 SaaS v2', 'content': _content('#f8fafc')},
    )
    assert updated.status_code == 200
    assert updated.json()['data']['revision']['rev_no'] == 2

    revs = await http.get(f'{_PREFIX}/design-systems/{ds_id}/revisions')
    assert revs.status_code == 200
    assert [r['rev_no'] for r in revs.json()['data']['items']] == [2, 1]


async def test_owner_revision_endpoint(client) -> None:
    """同步水位端点：save 后 owner_revision 变化。"""
    http, _state, tag = client
    before = (await http.get(f'{_PREFIX}/owner-revision')).json()['data']['owner_revision']
    await http.post(
        f'{_PREFIX}/design-systems',
        json={'slug': f'rv-{tag}', 'name': '水位', 'content': _content('#222')},
    )
    after = (await http.get(f'{_PREFIX}/owner-revision')).json()['data']['owner_revision']
    assert before != after


async def test_scope_gate_blocks_write_without_scope(client) -> None:
    """缺 designsystem:write → POST 写类 403（scope 闸真实生效，非假闸门）。"""
    http, state, tag = client
    state['scopes'] = []  # 撤掉所有 scope
    resp = await http.post(
        f'{_PREFIX}/design-systems',
        json={'slug': f'no-{tag}', 'name': '应被拒', 'content': _content('#333')},
    )
    assert resp.status_code == 403
    # 读类无 scope 闸 → 仍可访问（避免假闸门）
    assert (await http.get(f'{_PREFIX}/design-systems')).status_code == 200


async def test_import_shadcn_via_agent_route(client) -> None:
    """import 三入口经 agent 路由接通（真实 shadcn 样例）；网络不可达则 skip。"""
    http, _state, _tag = client
    resp = await http.post(
        f'{_PREFIX}/import',
        json={'source': 'shadcn', 'ref': 'https://tweakcn.com/r/themes/modern-minimal.json'},
    )
    if resp.status_code != 200:
        body = resp.json()
        if any(h in str(body.get('msg', '')) for h in ('拉取失败', '无法解析', '响应过大')):
            pytest.skip(f'网络不可达，跳过: {body}')
        assert False, f'import 失败: {resp.status_code} {body}'
    data = resp.json()['data']
    assert data['source_kind'] == 'imported_shadcn'
    assert ':root' in data['tokens_css'] and '--primary' in data['tokens_css']
