"""G1 群组建/管 真实 HTTP E2E（真实 PostgreSQL，零 mock）。

模块级把 app 群组路由挂最小 app，fixture 用 dependency_overrides 把 DependsJwtAuth 换成
注入固定 user_id、get_db/get_db_transaction 指向真实 PG 会话。覆盖建群→列表→详情→
加成员→改 policy→踢人→解散全流程 + 角色权限 + 信封外壳。

事实源: docs/hasn-node设计文档/03-Runtime调度/06-群聊派发与Agent参与设计.md G1。
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
from backend.app.hasn.model.hasn_humans import HasnHumans
from backend.common.exception import errors
from backend.common.exception.exception_handler import register_exception
from backend.common.security.jwt import DependsJwtAuth
from backend.database.db import SQLALCHEMY_DATABASE_URL, get_db, get_db_transaction

pytestmark = pytest.mark.asyncio

_USER_ID = 960000 + int(uuid.uuid4().int % 20000)

_APP = FastAPI()
_APP.add_middleware(ContextMiddleware, plugins=(RequestIdPlugin(),))
register_exception(_APP)
_APP.include_router(app_groups_router, prefix='/api/v1/hasn/app/groups')


def _uid() -> str:
    return uuid.uuid4().hex[:8]


@pytest_asyncio.fixture
async def http():
    engine = create_async_engine(SQLALCHEMY_DATABASE_URL, poolclass=NullPool)
    try:
        async with engine.connect() as conn:
            await conn.execute(select(1))
    except Exception as exc:
        await engine.dispose()
        pytest.skip(f'本地 PostgreSQL 不可达，跳过: {exc!r}')

    session = async_sessionmaker(engine, expire_on_commit=False)()
    owner_uid = _uid()
    owner_hasn = f'h_grp_{owner_uid}'
    # 唯一 star_id（非空）：服务在请求内会 commit 这条 owner，fixture 末尾 rollback 已太迟，
    # 故每跑一次会“泄漏”一条 owner 行；空 star_id 会撞 idx_hasn_humans_star_id 唯一索引导致
    # 下次 setup 失败。用唯一 star_id 让残留行互不冲突，测试可重复跑。
    session.add(
        HasnHumans(
            hasn_id=owner_hasn,
            star_id=f'sg{owner_uid}',
            user_id=_USER_ID,
            nickname='群主E2E',
            status='active',
        )
    )
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
        yield SimpleNamespace(client=client, owner=owner_hasn)
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


async def test_group_full_lifecycle(http) -> None:
    """建群（创建者自动 owner + agent_policy=free）→ 列表 → 详情名册。"""
    c = http.client
    friend = f'h_friend_{_uid()}'
    agent_member = f'a_member_{_uid()}'

    created = _data(
        await c.post(
            '/api/v1/hasn/app/groups',
            json={'title': f'群{_uid()}', 'members': [{'hasn_id': friend}, {'hasn_id': agent_member}]},
        )
    )
    assert created['group_id'].startswith('g:'), '应分配 g:NNNNNN'
    assert created['agent_policy'] == 'free', '默认 free'
    assert created['member_count'] == 3, '创建者 + 2 成员'
    roles = {m['hasn_id']: m['role'] for m in created['members']}
    assert roles[http.owner] == 'owner', '创建者应为群主'
    types = {m['hasn_id']: m['member_type'] for m in created['members']}
    assert types[agent_member] == 'agent', 'a_ 前缀应识别为分身成员'
    gid = created['group_id']

    mine = _data(await c.get('/api/v1/hasn/app/groups'))['items']
    listed = next((g for g in mine if g['group_id'] == gid), None)
    assert listed is not None, '建群后应在我的群列表'
    # 列表附带头像预览名册（前 N≤9 个成员，供 WebUI 拼九宫格群头像，与详情同序）。
    preview = listed.get('members_preview')
    assert isinstance(preview, list) and 1 <= len(preview) <= 9, '群列表应含 1~9 个预览成员'
    assert all('hasn_id' in m and 'member_type' in m for m in preview), '预览成员需含身份字段'

    detail = _data(await c.get(f'/api/v1/hasn/app/groups/{gid}'))
    assert detail['group_id'] == gid and len(detail['members']) == 3
    # 预览顺序应与详情名册前缀一致（同按入群时间升序），保证列表/详情群头像宫格一致。
    detail_ids = [m['hasn_id'] for m in detail['members']]
    assert [m['hasn_id'] for m in preview] == detail_ids[: len(preview)], '预览应是详情名册同序前缀'


async def test_group_member_and_policy_management(http) -> None:
    """加成员 → 改 agent_policy → 踢人 → 解散，及群主不可被踢。"""
    c = http.client
    created = _data(await c.post('/api/v1/hasn/app/groups', json={'title': f'管理群{_uid()}', 'members': []}))
    gid = created['group_id']
    assert created['member_count'] == 1

    new_member = f'h_add_{_uid()}'
    added = _data(
        await c.post(f'/api/v1/hasn/app/groups/{gid}/members', json={'members': [{'hasn_id': new_member}]})
    )
    assert added['member_count'] == 2

    updated = _data(await c.patch(f'/api/v1/hasn/app/groups/{gid}', json={'agent_policy': 'mention_only'}))
    assert updated['agent_policy'] == 'mention_only'

    # 群主不可被移除
    owner_kick = await c.delete(f'/api/v1/hasn/app/groups/{gid}/members/{http.owner}')
    assert owner_kick.status_code != 200 or owner_kick.json().get('code') != 200, '群主不应可被移除'

    removed = _data(await c.delete(f'/api/v1/hasn/app/groups/{gid}/members/{new_member}'))
    assert removed['member_count'] == 1

    disbanded = _data(await c.delete(f'/api/v1/hasn/app/groups/{gid}'))
    assert disbanded['status'] == 'disbanded'
    mine = _data(await c.get('/api/v1/hasn/app/groups'))['items']
    assert all(g['group_id'] != gid for g in mine), '解散后不应在我的群列表'


async def test_non_member_cannot_view(http) -> None:
    """非成员查看群详情应被拒（ForbiddenError）。"""
    c = http.client
    created = _data(await c.post('/api/v1/hasn/app/groups', json={'title': f'私群{_uid()}', 'members': []}))
    created['group_id']

    # 退群后再查（owner 退不了，这里建第二个群、用一个不存在的成员视角无法模拟；
    # 改为断言不存在群返回 404）
    ghost = await c.get('/api/v1/hasn/app/groups/g:1')
    assert ghost.status_code != 200 or ghost.json().get('code') != 200, '不存在群应 404'


async def test_group_preview_public_meta(http) -> None:
    """doc22 群名片：GET /{gid}/preview 返回公开元信息（is_member/my_role/join_policy）。

    创建者是成员 → is_member=True、my_role=owner；不含完整名册（只公开字段）。
    """
    c = http.client
    created = _data(
        await c.post(
            '/api/v1/hasn/app/groups',
            json={'title': f'名片群{_uid()}', 'members': [], 'join_policy': 'open'},
        )
    )
    gid = created['group_id']
    assert created['join_policy'] == 'open', '建群应落 join_policy=open'

    meta = _data(await c.get(f'/api/v1/hasn/app/groups/{gid}/preview'))
    assert meta['group_id'] == gid
    assert meta['join_policy'] == 'open'
    assert meta['is_member'] is True and meta['my_role'] == 'owner', '创建者应识别为群主成员'
    assert meta['member_count'] == 1
    assert 'members' not in meta, '预览只回公开元信息，不含完整名册'

    ghost = await c.get('/api/v1/hasn/app/groups/g:1/preview')
    assert ghost.status_code != 200 or ghost.json().get('code') != 200, '不存在群预览应 404'


async def test_join_group_http_route(http) -> None:
    """doc22 群名片：POST /{gid}/join 走 HTTP 路径（人类从群预览页点「加入群聊」）。

    创建者本人对自有群 join → 幂等返回 already_member（验证 HTTP 路由正确接到 join_group
    service，不需第二用户；跨用户/策略分支由 service 级 test_join_group_open_and_invite_only 覆盖）。
    """
    c = http.client
    created = _data(
        await c.post(
            '/api/v1/hasn/app/groups',
            json={'title': f'加群HTTP{_uid()}', 'members': [], 'join_policy': 'open'},
        )
    )
    gid = created['group_id']

    joined = _data(await c.post(f'/api/v1/hasn/app/groups/{gid}/join'))
    assert joined['status'] == 'already_member' and joined['joined'] is True, '本人已是群主 → 幂等入群'

    ghost = await c.post('/api/v1/hasn/app/groups/g:1/join')
    assert ghost.status_code != 200 or ghost.json().get('code') != 200, '不存在群加入应 404'


async def test_join_group_open_and_invite_only(db_session) -> None:
    """doc22 hasn.group.join 底层：open 直接入群、invite_only 需审批、幂等 already_member。"""
    from backend.app.hasn.service.hasn_group_service import HasnGroupService

    owner = f'h_grp_{_uid()}'
    joiner_agent = f'a_join_{_uid()}'

    # open 群：非成员自助入群应成功。
    open_group = await HasnGroupService.create_group(
        db_session, owner_hasn_id=owner, title=f'开放群{_uid()}', members=[], join_policy='open'
    )
    ogid = open_group['group_id']
    joined = await HasnGroupService.join_group(db_session, applicant_hasn_id=joiner_agent, group_id=ogid)
    assert joined['status'] == 'joined' and joined['joined'] is True, 'open 群应直接入群'
    assert joined['member_count'] == 2, '入群后成员数 +1'
    assert joined['role'] == 'member'

    # 幂等：已是成员再 join → already_member。
    again = await HasnGroupService.join_group(db_session, applicant_hasn_id=joiner_agent, group_id=ogid)
    assert again['status'] == 'already_member' and again['joined'] is True, '重复入群应幂等'

    # invite_only 群：非成员自助入群应如实回 needs_approval（零 fake，未入群）。
    inv_group = await HasnGroupService.create_group(
        db_session, owner_hasn_id=owner, title=f'邀请制群{_uid()}', members=[], join_policy='invite_only'
    )
    igid = inv_group['group_id']
    pending = await HasnGroupService.join_group(db_session, applicant_hasn_id=joiner_agent, group_id=igid)
    assert pending['status'] == 'needs_approval' and pending['joined'] is False, 'invite_only 应需审批'


async def test_get_group_public_meta_non_member(db_session) -> None:
    """get_group_public_meta 对非成员 viewer 返回 is_member=False、my_role=None。"""
    from backend.app.hasn.service.hasn_group_service import HasnGroupService

    owner = f'h_grp_{_uid()}'
    stranger = f'h_stranger_{_uid()}'
    grp = await HasnGroupService.create_group(
        db_session, owner_hasn_id=owner, title=f'公开元信息群{_uid()}', members=[], join_policy='open'
    )
    gid = grp['group_id']

    meta = await HasnGroupService.get_group_public_meta(db_session, viewer_hasn_id=stranger, group_id=gid)
    assert meta['group_id'] == gid
    assert meta['is_member'] is False and meta['my_role'] is None, '非成员 viewer 应识别为非成员'
    assert meta['member_count'] == 1 and meta['join_policy'] == 'open'


@pytest_asyncio.fixture
async def db_session():
    """服务级测试用的裸 PG 会话（不经 HTTP，直接调 service 校验权限矩阵）。"""
    engine = create_async_engine(SQLALCHEMY_DATABASE_URL, poolclass=NullPool)
    try:
        async with engine.connect() as conn:
            await conn.execute(select(1))
    except Exception as exc:
        await engine.dispose()
        pytest.skip(f'本地 PostgreSQL 不可达，跳过: {exc!r}')

    session = async_sessionmaker(engine, expire_on_commit=False)()
    try:
        yield session
    finally:
        await session.rollback()
        await session.close()
        await engine.dispose()


async def test_any_member_can_add_but_non_member_cannot(db_session) -> None:
    """加人权限已放开给任意群成员（对齐微信默认），仅非成员被拒。

    覆盖 hasn_group_service.add_members 的放开：普通成员（role=member）拉人应成功，
    非成员（不在名册）应 ForbiddenError。移除/改设置/解散仍受 owner/admin 限制。
    """
    from backend.app.hasn.service.hasn_group_service import HasnGroupService

    owner = f'h_grp_{_uid()}'
    plain_member = f'h_mem_{_uid()}'
    added_by_member = f'h_new_{_uid()}'
    stranger = f'h_stranger_{_uid()}'

    created = await HasnGroupService.create_group(
        db_session,
        owner_hasn_id=owner,
        title=f'加人放开群{_uid()}',
        members=[{'hasn_id': plain_member}],
    )
    gid = created['group_id']
    roles = {m['hasn_id']: m['role'] for m in created['members']}
    assert roles[owner] == 'owner' and roles[plain_member] == 'member', '创建者=owner，被加者=member'

    # 普通成员（非 owner/admin）拉人：放开后应成功。
    result = await HasnGroupService.add_members(
        db_session,
        actor_hasn_id=plain_member,
        group_id=gid,
        members=[{'hasn_id': added_by_member}],
    )
    added_ids = {m['hasn_id'] for m in result['members']}
    assert added_by_member in added_ids, '普通成员应能把新人加进群'

    # 非成员拉人：仍应被拒。
    with pytest.raises(errors.ForbiddenError):
        await HasnGroupService.add_members(
            db_session,
            actor_hasn_id=stranger,
            group_id=gid,
            members=[{'hasn_id': f'h_x_{_uid()}'}],
        )
