"""DS-P8 设计系统 Owner/WebUI 端 HTTP 路由测试（零 mock）。

经 ASGITransport 走完整 HTTP 栈 + 真实 PostgreSQL；最小子 app 只挂 designsystem app 路由。
鉴权依赖 `DependsJwtAuth` 被覆盖为注入「每测试唯一 user_id」（标准 FastAPI 测试法，非 mock 业务），
配合 seed 的 `HasnHumans` 行让 `_resolve_owner` 解析出主人 hasn_id。

覆盖 P8 验收（daemon webui-facing 回源通道）：
- owner list/get/revisions/owner-revision 真实可读（与 agent 端同一 service，仅身份来源不同）；
- 读类**无 scope 闸门**（owner 是自己库的权威）；
- owner 隔离（A 的设计系统 B 不可见、不可读）；
- 软删 owner-only；import 三入口接通（真实 shadcn 样例，网络不可达则 skip）；
- 统一信封 `{code,msg,data}` 外壳。
"""

from __future__ import annotations

import os
import uuid

from types import SimpleNamespace

import httpx
import pytest
import pytest_asyncio

from fastapi import FastAPI, Request
from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool
from starlette_context.middleware import ContextMiddleware
from starlette_context.plugins import RequestIdPlugin

from backend.app.hasn_core import HasnAgents, HasnHumans
from backend.app.hasn_designsystem.api.v1.app.designsystem import router as app_router
from backend.app.hasn_designsystem.service.design_system_service import Subject, design_system_service
from backend.common.exception.exception_handler import register_exception
from backend.common.security.jwt import DependsJwtAuth
from backend.database.db import SQLALCHEMY_DATABASE_URL, async_db_session, get_db, get_db_transaction

pytestmark = pytest.mark.asyncio

_PREFIX = '/api/v1/designsystem/app'

_APP = FastAPI()
_APP.add_middleware(ContextMiddleware, plugins=(RequestIdPlugin(),))
register_exception(_APP)
_APP.include_router(app_router, prefix=_PREFIX)


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


def _new_user_id() -> int:
    return 960_000_000 + int(uuid.uuid4().int % 20_000_000)


def _human(hasn_id: str, user_id: int) -> HasnHumans:
    # star_id / nickname 均有唯一索引，按 hasn_id 派生唯一值避免撞键。
    return HasnHumans(
        hasn_id=hasn_id, star_id=f's_{hasn_id}', user_id=user_id, nickname=f'DS主人_{hasn_id}', status='active'
    )


@pytest_asyncio.fixture
async def env():
    os.environ.setdefault('DESIGNSYSTEM_IMPORT_FAKEIP_PASSTHROUGH', '1')
    engine = create_async_engine(SQLALCHEMY_DATABASE_URL, poolclass=NullPool)
    try:
        async with engine.connect() as conn:
            await conn.execute(select(1))
    except Exception as exc:
        await engine.dispose()
        pytest.skip(f'本地 PostgreSQL 不可达，跳过: {exc!r}')

    session = async_sessionmaker(engine, expire_on_commit=False)()
    tag = uuid.uuid4().hex[:8]
    user_a = _new_user_id()
    user_b = _new_user_id()
    owner_a = f'h_dsa_{tag}'
    owner_b = f'h_dsb_{tag}'
    agent_a = f'a_{tag}'
    async with async_db_session.begin() as identity_db:
        identity_db.add(_human(owner_a, user_a))
        identity_db.add(_human(owner_b, user_b))
        identity_db.add(
            HasnAgents(
                hasn_id=agent_a,
                star_id=f's_ds_app_{tag}#agent',
                owner_id=owner_a,
                display_name='设计系统测试分身',
                agent_name=f'designsystem_app_{tag}',
                status='active',
            )
        )

    # 当前生效身份（测试内可切换以验证 owner 隔离）。
    auth_state = {'user_id': user_a}

    async def _yield_session():
        yield session

    async def _auth_inject(request: Request) -> str:
        request.scope['user'] = SimpleNamespace(id=auth_state['user_id'])
        request.scope['auth'] = ['authenticated']
        return 'e2e-token'

    _APP.dependency_overrides[get_db] = _yield_session
    _APP.dependency_overrides[get_db_transaction] = _yield_session
    _APP.dependency_overrides[DependsJwtAuth.dependency] = _auth_inject

    client = httpx.AsyncClient(transport=httpx.ASGITransport(app=_APP), base_url='http://e2e')
    try:
        yield SimpleNamespace(
            client=client,
            session=session,
            tag=tag,
            owner_a=owner_a,
            owner_b=owner_b,
            user_a=user_a,
            user_b=user_b,
            auth_state=auth_state,
        )
    finally:
        await client.aclose()
        _APP.dependency_overrides.clear()
        await session.rollback()
        await session.close()
        await engine.dispose()
        async with async_db_session.begin() as identity_db:
            await identity_db.execute(delete(HasnAgents).where(HasnAgents.hasn_id == agent_a))
            await identity_db.execute(
                delete(HasnHumans).where(HasnHumans.hasn_id.in_([owner_a, owner_b]))
            )


def _data(resp: httpx.Response):
    assert resp.status_code == 200, f'{resp.request.method} {resp.request.url} -> {resp.status_code}: {resp.text}'
    body = resp.json()
    assert body.get('code') == 200, f'envelope 非 200: {body}'
    return body['data']


async def test_owner_list_get_revisions_flow(env) -> None:
    """分身 save 一套 → owner（同主人）经 app 路由 list 可见、get 可读、revisions 降序、owner-revision 变化。"""
    c, s = env.client, env.session
    # 分身（owner_a 名下）落一套设计系统（owner 端不直接 save，由分身经 agent 通道生成）。
    saved = await design_system_service.save(
        s,
        subject=Subject.agent(f'a_{env.tag}', env.owner_a),
        design_system_id=None,
        slug=f'sys-{env.tag}',
        name='暖色 SaaS',
        content=_content('#ffffff'),
        category='saas',
        score=90,
        grade='excellent',
    )
    ds_id = saved['id']

    listed = _data(await c.get(f'{_PREFIX}/design-systems'))
    assert any(it['id'] == ds_id for it in listed['items'])

    got = _data(await c.get(f'{_PREFIX}/design-systems/{ds_id}'))
    assert got['id'] == ds_id
    assert got['current_revision']['tokens_css'] is not None  # 详情含当前版完整 token 内容

    # 再 save 一版 → 版本历史降序。
    await design_system_service.save(
        s,
        subject=Subject.agent(f'a_{env.tag}', env.owner_a),
        design_system_id=ds_id,
        slug=f'sys-{env.tag}',
        name='暖色 SaaS v2',
        content=_content('#f8fafc'),
    )
    revs = _data(await c.get(f'{_PREFIX}/design-systems/{ds_id}/revisions'))
    assert [r['rev_no'] for r in revs['items']] == [2, 1]
    rev_full = _data(await c.get(f'{_PREFIX}/revisions/{revs["items"][0]["id"]}'))
    assert rev_full['tokens_css'] is not None

    owner_rev = _data(await c.get(f'{_PREFIX}/owner-revision'))
    assert len(owner_rev['owner_revision']) == 64


async def test_owner_isolation(env) -> None:
    """A 的私有设计系统：A 经 app 路由可见可读；切到 B 身份 → list 不见、get 403。"""
    c, s = env.client, env.session
    saved = await design_system_service.save(
        s,
        subject=Subject.human(env.owner_a),
        design_system_id=None,
        slug=f'priv-{env.tag}',
        name='A 私有',
        content=_content('#101010'),
    )
    ds_id = saved['id']

    a_list = _data(await c.get(f'{_PREFIX}/design-systems'))
    assert any(it['id'] == ds_id for it in a_list['items'])

    # 切到 owner_b 身份。
    env.auth_state['user_id'] = env.user_b
    b_list = _data(await c.get(f'{_PREFIX}/design-systems'))
    assert all(it['id'] != ds_id for it in b_list['items'])
    forbidden = await c.get(f'{_PREFIX}/design-systems/{ds_id}')
    assert forbidden.status_code == 403, forbidden.text


async def test_owner_delete_soft_hides(env) -> None:
    """owner 经 app 路由软删 → list 不见、get 404。"""
    c, s = env.client, env.session
    saved = await design_system_service.save(
        s,
        subject=Subject.human(env.owner_a),
        design_system_id=None,
        slug=f'del-{env.tag}',
        name='待删',
        content=_content('#333'),
    )
    ds_id = saved['id']

    deleted = _data(await c.delete(f'{_PREFIX}/design-systems/{ds_id}'))
    assert deleted['deleted'] is True

    lst = _data(await c.get(f'{_PREFIX}/design-systems'))
    assert all(it['id'] != ds_id for it in lst['items'])
    assert (await c.get(f'{_PREFIX}/design-systems/{ds_id}')).status_code == 404


async def test_owner_import_shadcn(env) -> None:
    """import 三入口经 owner 路由接通（真实 shadcn 样例）；网络不可达则 skip。"""
    c = env.client
    resp = await c.post(
        f'{_PREFIX}/import', json={'source': 'shadcn', 'ref': 'https://tweakcn.com/r/themes/modern-minimal.json'}
    )
    if resp.status_code != 200:
        body = resp.json()
        if any(h in str(body.get('msg', '')) for h in ('拉取失败', '无法解析', '响应过大')):
            pytest.skip(f'网络不可达，跳过: {body}')
        raise AssertionError(f'import 失败: {resp.status_code} {body}')
    data = resp.json()['data']
    assert data['source_kind'] == 'imported_shadcn'
    assert ':root' in data['tokens_css'] and '--primary' in data['tokens_css']
