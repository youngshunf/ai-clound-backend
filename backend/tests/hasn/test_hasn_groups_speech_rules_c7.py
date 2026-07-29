"""doc10 群聊发言规则 + 分身群内准则 真实 HTTP/service E2E（真实 PostgreSQL，零 mock）。

覆盖 GS1 新增行为（施工清单 C7 矩阵）：
- effective_agent_policy 派生：多分身群 free→mention_only 强制降级、agent_member_count 出参；
- update_group 多分身群设置闸：目标 free 且分身>1 → 拒绝；
- allow_member_invite_agent 开关 round-trip + 序列化；
- 非主人拉分身邀请流程（§3.2）：落 pending、主人 accept 后入群、decline/cancel；
- agent_charter 白名单：仅分身主人可读写、他人视角剥离。

HTTP 面用 dependency_overrides 注入固定 owner（同 test_hasn_groups_http_e2e）；跨 owner 的
accept/decline 因单用户注入无法切换身份，直接调 service 层（真实逻辑所在）验证。
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
from backend.app.hasn.service.hasn_group_service import (
    effective_agent_policy,
    hasn_group_service,
)
from backend.app.hasn_im.application.local_gateway import PythonLocalImGateway
from backend.app.hasn_im.application.provider import get_im_gateway
from backend.common.exception.exception_handler import register_exception
from backend.common.security.jwt import DependsJwtAuth
from backend.database.db import (
    SQLALCHEMY_DATABASE_URL,
    get_db,
    get_db_transaction,
    get_im_db,
    get_im_db_transaction,
)

pytestmark = pytest.mark.asyncio

_APP = FastAPI()
_APP.add_middleware(ContextMiddleware, plugins=(RequestIdPlugin(),))
register_exception(_APP)
_APP.include_router(app_groups_router, prefix='/api/v1/hasn/app/groups')


def _uid() -> str:
    return uuid.uuid4().hex[:8]


async def test_effective_policy_pure() -> None:
    """纯函数：free 且 >1 分身 → mention_only；其余原样。"""
    assert effective_agent_policy('free', 0) == 'free'
    assert effective_agent_policy('free', 1) == 'free'
    assert effective_agent_policy('free', 2) == 'mention_only'
    assert effective_agent_policy('mention_only', 3) == 'mention_only'
    assert effective_agent_policy('silent', 5) == 'silent'
    assert effective_agent_policy('no_agent', 5) == 'no_agent'


@pytest_asyncio.fixture
async def env():
    engine = create_async_engine(SQLALCHEMY_DATABASE_URL, poolclass=NullPool)
    try:
        async with engine.connect() as conn:
            await conn.execute(select(1))
    except Exception as exc:
        await engine.dispose()
        pytest.skip(f'本地 PostgreSQL 不可达，跳过: {exc!r}')

    session_factory = async_sessionmaker(engine, expire_on_commit=False)
    session = session_factory()
    im_gateway = PythonLocalImGateway(session_factory)
    owner_uid = _uid()
    owner_hasn = f'h_o1_{owner_uid}'
    other_uid = _uid()
    other_hasn = f'h_o2_{other_uid}'
    user_id = 3_500_000 + int(uuid.uuid4().int % 100_000_000)
    # owner 本人 + 第二主人（拉分身邀请场景的分身主人）
    session.add_all([
        HasnHumans(hasn_id=owner_hasn, star_id=f'so1{owner_uid}', user_id=user_id, nickname=f'群主{owner_uid}', status='active'),
        HasnHumans(hasn_id=other_hasn, star_id=f'so2{other_uid}', user_id=user_id + 1, nickname=f'对方主人{other_uid}', status='active'),
    ])
    # owner 名下两个分身（用于 effective 降级 + charter），第二主人名下一个分身（用于邀请场景）
    a1 = f'a_own1_{_uid()}'
    a2 = f'a_own2_{_uid()}'
    a_other = f'a_oth_{_uid()}'
    session.add_all([
        HasnAgents(hasn_id=a1, star_id=f'ag{_uid()}', owner_id=owner_hasn, display_name=f'我的分身甲{owner_uid}', status='active'),
        HasnAgents(hasn_id=a2, star_id=f'ag{_uid()}', owner_id=owner_hasn, display_name=f'我的分身乙{owner_uid}', status='active'),
        HasnAgents(hasn_id=a_other, star_id=f'ag{_uid()}', owner_id=other_hasn, display_name=f'对方分身{other_uid}', status='active'),
    ])
    await session.commit()

    async def _yield_session():
        yield session

    async def _auth_inject(request: Request) -> str:
        request.scope['user'] = SimpleNamespace(id=user_id, hasn_id=owner_hasn)
        request.scope['auth'] = ['authenticated']
        return 'e2e-token'

    _APP.dependency_overrides[get_db] = _yield_session
    _APP.dependency_overrides[get_db_transaction] = _yield_session
    _APP.dependency_overrides[get_im_db] = _yield_session
    _APP.dependency_overrides[get_im_db_transaction] = _yield_session
    _APP.dependency_overrides[get_im_gateway] = lambda: im_gateway
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


async def test_effective_policy_and_setting_gate(env) -> None:
    """加第二个自有分身 → 生效策略 free→mention_only；设 free 被拒；减到 1 恢复。"""
    c = env.client
    created = _data(await c.post('/api/v1/hasn/app/groups', json={'title': f'多分身群{_uid()}', 'members': [{'hasn_id': env.a1}]}))
    gid = created['group_id']
    # 1 个分身：free 原样生效
    assert created['agent_member_count'] == 1
    assert created['agent_policy_effective'] == 'free'

    # 加第 2 个自有分身（主人本人拉 → 即时入群）→ 生效降级 mention_only
    added = _data(await c.post(f'/api/v1/hasn/app/groups/{gid}/members', json={'members': [{'hasn_id': env.a2}]}))
    assert added['agent_member_count'] == 2
    assert added['agent_policy'] == 'free', '存储值不改'
    assert added['agent_policy_effective'] == 'mention_only', '多分身群强制降级'

    # 多分身群设置闸：目标 free 被拒
    reject = await c.patch(f'/api/v1/hasn/app/groups/{gid}', json={'agent_policy': 'free'})
    assert _is_error(reject), '多分身群不允许设置为 free'

    # 详情也应带 effective + count
    detail = _data(await c.get(f'/api/v1/hasn/app/groups/{gid}'))
    assert detail['agent_member_count'] == 2 and detail['agent_policy_effective'] == 'mention_only'

    # 移除一个分身 → 恢复 free
    removed = _data(await c.delete(f'/api/v1/hasn/app/groups/{gid}/members/{env.a2}'))
    assert removed['member_count'] == 2  # owner + a1
    detail2 = _data(await c.get(f'/api/v1/hasn/app/groups/{gid}'))
    assert detail2['agent_member_count'] == 1 and detail2['agent_policy_effective'] == 'free', '减到 1 分身应恢复自由发言'


async def test_allow_member_invite_agent_toggle(env) -> None:
    """allow_member_invite_agent 默认 True；patch 关闭后 round-trip 序列化。"""
    c = env.client
    created = _data(await c.post('/api/v1/hasn/app/groups', json={'title': f'开关群{_uid()}', 'members': []}))
    gid = created['group_id']
    assert created['allow_member_invite_agent'] is True, '默认允许成员拉分身'

    updated = _data(await c.patch(f'/api/v1/hasn/app/groups/{gid}', json={'allow_member_invite_agent': False}))
    assert updated['allow_member_invite_agent'] is False

    detail = _data(await c.get(f'/api/v1/hasn/app/groups/{gid}'))
    assert detail['allow_member_invite_agent'] is False, '关闭态应持久 + 序列化'


async def test_non_owner_invite_flow(env) -> None:
    """owner 拉「对方的分身」→ 不直接入群、落 pending 邀请；service accept 后入群。"""
    c = env.client
    created = _data(await c.post('/api/v1/hasn/app/groups', json={'title': f'邀请群{_uid()}', 'members': []}))
    gid = created['group_id']
    base_count = created['member_count']

    # owner 拉对方分身 → 走邀请流程，不即时入群
    added = _data(await c.post(f'/api/v1/hasn/app/groups/{gid}/members', json={'members': [{'hasn_id': env.a_other}]}))
    assert added['member_count'] == base_count, '拉别人的分身不应即时增加成员数'
    invited = added.get('invited_agents') or []
    assert any(i['agent_hasn_id'] == env.a_other for i in invited), '应产出一条 pending 邀请'
    invite_id = next(i['invite_id'] for i in invited if i['agent_hasn_id'] == env.a_other)

    # 群详情 pending 列表可见
    detail = _data(await c.get(f'/api/v1/hasn/app/groups/{gid}'))
    pend = detail.get('pending_agent_invites') or []
    assert any(p['invite_id'] == invite_id and p['agent_hasn_id'] == env.a_other for p in pend), '详情应含 pending 邀请'
    assert all(m['hasn_id'] != env.a_other for m in detail['members']), '未同意前不在名册'

    # 重复发起幂等：不产生第二条 pending
    again = _data(await c.post(f'/api/v1/hasn/app/groups/{gid}/members', json={'members': [{'hasn_id': env.a_other}]}))
    assert not (again.get('invited_agents') or []), '重复发起不应再产出邀请（幂等）'

    # 越权 accept（owner 非分身主人）→ 拒绝
    with pytest.raises(Exception):
        await hasn_group_service.accept_agent_invite(
            env.session, actor_hasn_id=env.owner, group_id=gid, invite_id=invite_id
        )

    # 分身主人（第二主人）accept → 入群 + 名册出现
    accepted = await hasn_group_service.accept_agent_invite(
        env.session, actor_hasn_id=env.other, group_id=gid, invite_id=invite_id
    )
    assert accepted['status'] == 'accepted' and accepted['joined'] is True
    detail2 = _data(await c.get(f'/api/v1/hasn/app/groups/{gid}'))
    assert any(m['hasn_id'] == env.a_other for m in detail2['members']), 'accept 后分身应入群'
    assert not (detail2.get('pending_agent_invites') or []), 'accept 后 pending 清空'


async def test_invite_decline_and_cancel(env) -> None:
    """decline（主人拒）与 cancel（发起人撤）各自置态，且非本人不可操作。"""
    c = env.client
    created = _data(await c.post('/api/v1/hasn/app/groups', json={'title': f'拒撤群{_uid()}', 'members': []}))
    gid = created['group_id']

    # 场景 A：主人 decline
    added = _data(await c.post(f'/api/v1/hasn/app/groups/{gid}/members', json={'members': [{'hasn_id': env.a_other}]}))
    inv_a = added['invited_agents'][0]['invite_id']
    declined = await hasn_group_service.decline_agent_invite(env.session, actor_hasn_id=env.other, group_id=gid, invite_id=inv_a)
    assert declined['status'] == 'declined' and declined['joined'] is False
    d1 = _data(await c.get(f'/api/v1/hasn/app/groups/{gid}'))
    assert all(m['hasn_id'] != env.a_other for m in d1['members']), 'decline 后不入群'

    # decline 后可重新发起（pending 唯一索引只约束 pending 态）
    added2 = _data(await c.post(f'/api/v1/hasn/app/groups/{gid}/members', json={'members': [{'hasn_id': env.a_other}]}))
    inv_b = added2['invited_agents'][0]['invite_id']
    # 场景 B：发起人 cancel（owner 是 inviter）
    cancelled = await hasn_group_service.cancel_agent_invite(env.session, actor_hasn_id=env.owner, group_id=gid, invite_id=inv_b)
    assert cancelled['status'] == 'cancelled'
    # 非发起人 cancel 应拒（对方主人不是 inviter）
    added3 = _data(await c.post(f'/api/v1/hasn/app/groups/{gid}/members', json={'members': [{'hasn_id': env.a_other}]}))
    inv_c = added3['invited_agents'][0]['invite_id']
    with pytest.raises(Exception):
        await hasn_group_service.cancel_agent_invite(env.session, actor_hasn_id=env.other, group_id=gid, invite_id=inv_c)


async def test_agent_charter_whitelist(env) -> None:
    """charter 仅分身主人可写；详情里仅本人分身带 charter，他人分身剥离。"""
    c = env.client
    # 建群 + 自有分身 a1 入群
    created = _data(await c.post('/api/v1/hasn/app/groups', json={'title': f'准则群{_uid()}', 'members': [{'hasn_id': env.a1}]}))
    gid = created['group_id']

    # 主人给自己的分身写准则
    charter_text = '本群只聊技术，回复简短克制，不主动发言。'
    put = _data(await c.put(f'/api/v1/hasn/app/groups/{gid}/members/{env.a1}/charter', json={'charter': charter_text}))
    assert put['agent_charter'] == charter_text

    # 详情里本人分身带 charter
    detail = _data(await c.get(f'/api/v1/hasn/app/groups/{gid}'))
    my_agent = next(m for m in detail['members'] if m['hasn_id'] == env.a1)
    assert my_agent.get('agent_charter') == charter_text, '本人分身应回填准则'

    # 对他人分身写准则 → 拒绝（owner 非 a_other 的主人）
    forbid = await c.put(f'/api/v1/hasn/app/groups/{gid}/members/{env.a_other}/charter', json={'charter': 'x'})
    assert _is_error(forbid), '非分身主人不得设置其准则'

    # 超长准则被拒
    toolong = await c.put(f'/api/v1/hasn/app/groups/{gid}/members/{env.a1}/charter', json={'charter': 'x' * 4001})
    assert _is_error(toolong), '超过 4000 字应拒'

    # 清除准则（空串）
    cleared = _data(await c.put(f'/api/v1/hasn/app/groups/{gid}/members/{env.a1}/charter', json={'charter': ''}))
    assert cleared['agent_charter'] is None, '空串应清除准则'
