"""doc08 §3.4 RT2.5 群聊披露档（agent_group_trust_level）真实 HTTP/service E2E（真实 PostgreSQL，零 mock）。

覆盖 GT1 施工清单 C6 矩阵：
- 主人为自己分身设群披露档 3 → 200，群详情回读 3；
- 非主人设他人分身档 → 403（owner 检查在成员检查之前）；
- 分身不在群 → 404；
- 档位 1 / 5 / 0 → 拒绝（RequestError·范围校验，仅 {2,3,4}）；
- 白名单：主人读群详情只见自己分身的 agent_group_trust_level，他人分身行剥离此字段；
- 默认值：未设置过的分身行序列化为 2。

跨 owner 的 accept（把「对方的分身」拉进群）因单用户 HTTP 注入无法切换身份，直接调 service 层
（与 test_hasn_groups_speech_rules_c7 同法）验证白名单剥离。
"""
from __future__ import annotations

import uuid

from types import SimpleNamespace

import httpx
import pytest
import pytest_asyncio

from fastapi import FastAPI, Request
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool
from starlette_context.middleware import ContextMiddleware
from starlette_context.plugins import RequestIdPlugin

from backend.app.hasn.api.v1.app.hasn_groups import router as app_groups_router
from backend.app.hasn.model.hasn_agents import HasnAgents
from backend.app.hasn.model.hasn_humans import HasnHumans
from backend.app.hasn.service.hasn_group_service import hasn_group_service
from backend.common.exception.exception_handler import register_exception
from backend.common.security.jwt import DependsJwtAuth
from backend.database.db import SQLALCHEMY_DATABASE_URL, get_db, get_db_transaction

pytestmark = pytest.mark.asyncio

_USER_ID = 990000 + int(uuid.uuid4().int % 20000)

_APP = FastAPI()
_APP.add_middleware(ContextMiddleware, plugins=(RequestIdPlugin(),))
register_exception(_APP)
_APP.include_router(app_groups_router, prefix='/api/v1/hasn/app/groups')


def _uid() -> str:
    return uuid.uuid4().hex[:8]


@pytest_asyncio.fixture
async def env():
    engine = create_async_engine(SQLALCHEMY_DATABASE_URL, poolclass=NullPool)
    try:
        async with engine.connect() as conn:
            await conn.execute(select(1))
    except Exception as exc:
        await engine.dispose()
        pytest.skip(f'本地 PostgreSQL 不可达，跳过: {exc!r}')

    session = async_sessionmaker(engine, expire_on_commit=False)()
    owner_uid = _uid()
    owner_hasn = f'h_o1_{owner_uid}'
    other_uid = _uid()
    other_hasn = f'h_o2_{other_uid}'
    session.add_all([
        HasnHumans(hasn_id=owner_hasn, star_id=f'so1{owner_uid}', user_id=_USER_ID, nickname='群主', status='active'),
        HasnHumans(hasn_id=other_hasn, star_id=f'so2{other_uid}', user_id=_USER_ID + 1, nickname='对方主人', status='active'),
    ])
    # owner 名下两个分身（a1 入群、a2 用于「不在群 → 404」），第二主人名下一个分身（白名单剥离场景）
    a1 = f'a_own1_{_uid()}'
    a2 = f'a_own2_{_uid()}'
    a_other = f'a_oth_{_uid()}'
    session.add_all([
        HasnAgents(hasn_id=a1, star_id=f'ag{_uid()}', owner_id=owner_hasn, display_name='我的分身甲', status='active'),
        HasnAgents(hasn_id=a2, star_id=f'ag{_uid()}', owner_id=owner_hasn, display_name='我的分身乙', status='active'),
        HasnAgents(hasn_id=a_other, star_id=f'ag{_uid()}', owner_id=other_hasn, display_name='对方分身', status='active'),
    ])
    await session.flush()

    async def _yield_session():
        yield session

    async def _auth_inject(request: Request) -> str:
        request.scope['user'] = SimpleNamespace(id=_USER_ID)
        request.scope['auth'] = ['authenticated']
        return 'e2e-token'

    _APP.dependency_overrides[get_db] = _yield_session
    _APP.dependency_overrides[get_db_transaction] = _yield_session
    _APP.dependency_overrides[DependsJwtAuth.dependency] = _auth_inject

    client = httpx.AsyncClient(transport=httpx.ASGITransport(app=_APP), base_url='http://e2e')
    try:
        yield SimpleNamespace(
            client=client, session=session, owner=owner_hasn, other=other_hasn,
            a1=a1, a2=a2, a_other=a_other,
        )
    finally:
        await client.aclose()
        _APP.dependency_overrides.clear()
        await session.rollback()
        await session.close()
        await engine.dispose()


def _data(resp: httpx.Response):
    assert resp.status_code == 200, f'{resp.request.method} {resp.request.url} -> {resp.status_code}: {resp.text}'
    body = resp.json()
    assert body.get('code') == 200, f'envelope 非 200: {body}'
    return body['data']


def _is_error(resp: httpx.Response) -> bool:
    return resp.status_code != 200 or resp.json().get('code') != 200


async def _set_level(c: httpx.AsyncClient, gid: str, agent: str, level: int) -> httpx.Response:
    return await c.put(f'/api/v1/hasn/app/groups/{gid}/members/{agent}/trust-level', json={'trust_level': level})


async def test_owner_set_own_agent_trust_level_and_readback(env) -> None:
    """主人给自己分身设群披露档 3 → 200，群详情回读 3。"""
    c = env.client
    created = _data(await c.post('/api/v1/hasn/app/groups', json={'title': f'披露档群{_uid()}', 'members': [{'hasn_id': env.a1}]}))
    gid = created['group_id']

    # 默认值：未设置过 → 序列化为 2
    detail0 = _data(await c.get(f'/api/v1/hasn/app/groups/{gid}'))
    my0 = next(m for m in detail0['members'] if m['hasn_id'] == env.a1)
    assert my0.get('agent_group_trust_level') == 2, '未设置的自有分身应默认披露档 2'

    # 主人设自己分身档 3
    put = _data(await _set_level(c, gid, env.a1, 3))
    assert put['agent_group_trust_level'] == 3 and put['agent_hasn_id'] == env.a1

    # 详情回读 3
    detail = _data(await c.get(f'/api/v1/hasn/app/groups/{gid}'))
    my = next(m for m in detail['members'] if m['hasn_id'] == env.a1)
    assert my.get('agent_group_trust_level') == 3, '自有分身应回填披露档 3'


async def test_non_owner_set_others_agent_forbidden(env) -> None:
    """非分身主人设他人分身档 → 403（owner 检查先于成员检查）。"""
    c = env.client
    created = _data(await c.post('/api/v1/hasn/app/groups', json={'title': f'越权群{_uid()}', 'members': [{'hasn_id': env.a1}]}))
    gid = created['group_id']
    # 当前登录主人是 owner，a_other 属第二主人 → owner 检查失败 403
    forbid = await _set_level(c, gid, env.a_other, 3)
    assert _is_error(forbid), '非分身主人不得设置其群披露档'
    assert forbid.status_code == 403 or forbid.json().get('code') == 403


async def test_agent_not_in_group_404(env) -> None:
    """自有分身但不在本群 → 404。"""
    c = env.client
    created = _data(await c.post('/api/v1/hasn/app/groups', json={'title': f'缺员群{_uid()}', 'members': [{'hasn_id': env.a1}]}))
    gid = created['group_id']
    # a2 是 owner 的分身（owner 检查过）但从未入群 → 成员检查 404
    missing = await _set_level(c, gid, env.a2, 3)
    assert _is_error(missing), '分身不在群应拒'
    assert missing.status_code == 404 or missing.json().get('code') == 404


async def test_out_of_range_levels_rejected(env) -> None:
    """档位 1 / 5 / 0 → 拒绝（仅 {2,3,4} 合法·范围校验）。"""
    c = env.client
    created = _data(await c.post('/api/v1/hasn/app/groups', json={'title': f'档位群{_uid()}', 'members': [{'hasn_id': env.a1}]}))
    gid = created['group_id']
    for bad in (1, 5, 0):
        resp = await _set_level(c, gid, env.a1, bad)
        assert _is_error(resp), f'非法档位 {bad} 应被拒'
    # 合法档位仍可设，确认前面被拒不是把值写坏
    ok = _data(await _set_level(c, gid, env.a1, 4))
    assert ok['agent_group_trust_level'] == 4


async def test_trust_level_whitelist_strips_others(env) -> None:
    """白名单：主人读群详情只见自己分身的 agent_group_trust_level，他人分身行剥离此字段。"""
    c = env.client
    created = _data(await c.post('/api/v1/hasn/app/groups', json={'title': f'白名单群{_uid()}', 'members': [{'hasn_id': env.a1}]}))
    gid = created['group_id']

    # owner 拉「对方的分身」→ 落 pending；分身主人（第二主人）service 层 accept 入群
    added = _data(await c.post(f'/api/v1/hasn/app/groups/{gid}/members', json={'members': [{'hasn_id': env.a_other}]}))
    invite_id = next(i['invite_id'] for i in (added.get('invited_agents') or []) if i['agent_hasn_id'] == env.a_other)
    accepted = await hasn_group_service.accept_agent_invite(
        env.session, actor_hasn_id=env.other, group_id=gid, invite_id=invite_id
    )
    assert accepted['joined'] is True

    # 主人给自己分身设档 3；对方分身不设（默认 2，但对 owner 视角不可见）
    _data(await _set_level(c, gid, env.a1, 3))

    detail = _data(await c.get(f'/api/v1/hasn/app/groups/{gid}'))
    mine = next(m for m in detail['members'] if m['hasn_id'] == env.a1)
    theirs = next(m for m in detail['members'] if m['hasn_id'] == env.a_other)
    assert mine.get('agent_group_trust_level') == 3, '本人分身应见披露档'
    assert 'agent_group_trust_level' not in theirs, '他人分身披露档应剥离（owner 私有字段）'
