"""community_ext 进程内 HTTP E2E（实施/95 Phase 6，真实 PG，零 mock）。

不重启共享服务、不伪造 token：模块级把真实 open+app 路由挂到最小 app（include_router
在导入期完成，依赖图一次构建），fixture 仅用 dependency_overrides 把 DependsJwtAuth 换成
注入已 seed 的 Owner、把 get_db / get_db_transaction 换成同一个真实 PG 会话（NullPool +
末尾 rollback，不污染库）。经 ASGITransport 走完整 FastAPI HTTP 栈（路径/查询/请求体解析 +
依赖注入 + 统一信封），覆盖三套子系统关键流——正是漏过「session 依赖放 TYPE_CHECKING」
HTTP bug 的那一层。
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

from backend.app.hasn.model.hasn_humans import HasnHumans
from backend.app.hasn_community.api.v1.app.community_ext import router as app_router
from backend.app.hasn_community.api.v1.open.community_ext import router as open_router
from backend.common.security.jwt import DependsJwtAuth
from backend.database.db import SQLALCHEMY_DATABASE_URL, get_db, get_db_transaction

pytestmark = pytest.mark.asyncio

_USER_ID = 970000 + int(uuid.uuid4().int % 20000)

# 模块级构建（依赖图一次成型，避免在 async fixture 内 include_router 触发的重建怪味）。
_APP = FastAPI()
_APP.include_router(open_router, prefix='/api/v1/community/open')
_APP.include_router(app_router, prefix='/api/v1/community/app')


def _uid() -> str:
    return uuid.uuid4().hex[:8]


@pytest_asyncio.fixture
async def http():
    engine = create_async_engine(SQLALCHEMY_DATABASE_URL, poolclass=NullPool)
    try:
        async with engine.connect() as conn:
            await conn.execute(select(1))
    except Exception as exc:  # noqa: BLE001
        await engine.dispose()
        pytest.skip(f'本地 PostgreSQL 不可达，跳过: {exc!r}')

    session = async_sessionmaker(engine, expire_on_commit=False)()
    owner_hasn = f'h_e2e_{_uid()}'
    # star_id 唯一索引：用唯一值，空串会与库中既有空串行撞 idx_hasn_humans_star_id。
    session.add(HasnHumans(hasn_id=owner_hasn, star_id=f's_{_uid()}', user_id=_USER_ID, nickname='E2E Owner', status='active'))
    await session.flush()

    async def _yield_session():
        yield session

    async def _auth_inject(request: Request):
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


async def test_topic_flow_over_http(http) -> None:
    """话题：resolve 归一 → 关注 → 我关注 → 详情 is_following。"""
    c = http.client
    base = f'T{_uid()}'
    items = _data(await c.post('/api/v1/community/app/topics/resolve', json={'names': [base.upper(), base.lower(), f'  {base}  ']}))['items']
    assert len({i['topic_id'] for i in items}) == 1, '同义异写应归一'
    topic_id = items[0]['topic_id']
    _data(await c.post(f'/api/v1/community/app/topics/{topic_id}/follow'))
    following = _data(await c.get('/api/v1/community/app/topics/following'))['items']
    assert any(t['topic_id'] == topic_id for t in following), '关注后应出现在我关注'
    detail = _data(await c.get(f'/api/v1/community/app/topics/{topic_id}'))
    assert detail['is_following'] is True


async def test_circle_flow_over_http(http) -> None:
    """圈子：建公开开放圈（建者自动 owner）→ 详情 my_role=owner → 我的圈含它。"""
    c = http.client
    created = _data(await c.post('/api/v1/community/app/circles', json={'name': f'Circle{_uid()}', 'visibility': 'public', 'join_policy': 'open', 'post_policy': 'members'}))
    circle_id = created['circle_id']
    detail = _data(await c.get(f'/api/v1/community/app/circles/{circle_id}'))
    assert detail['my_role'] == 'owner' and detail['member_count'] >= 1
    mine = _data(await c.get('/api/v1/community/app/circles/mine'))['items']
    assert any(x['circle_id'] == circle_id for x in mine), '建圈后应在我的圈'


async def test_doc_flow_over_http(http) -> None:
    """文档：建文集 → 建目录 → 树含目录+is_owner → 节点设密码 → open 解锁换 grant_token。"""
    c = http.client
    space = _data(await c.post('/api/v1/community/app/docs/spaces', json={'title': f'Space{_uid()}', 'default_visibility': 'public'}))
    space_id = space['space_id']
    node = _data(await c.post(f'/api/v1/community/app/docs/spaces/{space_id}/nodes', json={'node_type': 'directory', 'title': '第一章'}))
    node_id = node['node_id']
    tree = _data(await c.get(f'/api/v1/community/app/docs/spaces/{space_id}/tree'))
    assert tree['is_owner'] is True
    assert any(n['node_id'] == node_id for n in tree['tree']), '树应含新建目录'
    pwd = 'unlock-' + _uid()
    updated = _data(await c.put(f'/api/v1/community/app/docs/nodes/{node_id}', json={'visibility': 'password', 'password': pwd}))
    assert updated['has_password'] is True or updated.get('visibility') == 'password'
    grant = _data(await c.post(f'/api/v1/community/open/docs/nodes/{node_id}/unlock', json={'password': pwd}))
    assert grant.get('grant_token'), 'unlock 应返回 grant_token'
