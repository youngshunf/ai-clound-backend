"""帖子删除（草稿箱「删除」）回归测试。

回归 bug：草稿箱点删除返回 405 Method Not Allowed——app/community.py 只注册了
`GET /posts/{post_id}`、`PUT /posts/{post_id}/publish`，从未注册 `DELETE /posts/{post_id}`，
而文章侧 `DELETE /articles/{article_id}` 一直存在。daemon 代理 `DELETE …/app/posts/{id}`
打到只有 GET 的路径上，FastAPI 返回 405（路径在、方法不在）。

覆盖三层：
1. 路由内省守卫——DELETE 必须在注册表里（405 回归即此处先红）。
2. 真实 HTTP + 真实 PG——主人删除自己名下分身草稿帖 → 200 + 软删除。
3. service 层——越权 403 / 不存在 404（这两条走异常分支，需全局 middleware 才能映射 HTTP
   状态，故在 service 层直接断言抛错，避免重建整套 middleware）。

不重启共享 8020、不伪造 token、零 mock：模块级把真实 app 路由挂到最小 app，fixture 用
dependency_overrides 把 DependsJwtAuth 换成注入已 seed 的 Owner、get_db / get_db_transaction
换成同一个真实 PG 会话（NullPool + rollback 清理）。
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
from backend.app.hasn_community.api.v1.app.community import router as app_router
from backend.app.hasn_community.model.hasn_posts import HasnPosts
from backend.app.hasn_community.service.community_service import community_service
from backend.common.exception import errors
from backend.common.security.jwt import DependsJwtAuth
from backend.database.db import SQLALCHEMY_DATABASE_URL, get_db, get_db_transaction

_USER_ID = 1_200_000_000 + int(uuid.uuid4().int % 800_000_000)

# 模块级构建（依赖图一次成型）。
_APP = FastAPI()
_APP.include_router(app_router, prefix='/api/v1/community/app')


def _uid() -> str:
    return uuid.uuid4().hex[:8]


def test_delete_post_route_registered() -> None:
    """路由内省守卫：DELETE /posts/{post_id} 必须注册（405 回归即此处先红）。"""
    methods: set[str] = set()
    for r in _APP.routes:
        if getattr(r, 'path', '') == '/api/v1/community/app/posts/{post_id}':
            methods |= set(getattr(r, 'methods'))
    assert 'DELETE' in methods, f'DELETE 未注册，仅 {methods}——即 405 回归'


def _draft_post(owner_hasn: str) -> tuple[str, HasnPosts]:
    """Agent 待审核草稿帖：owner_hasn_id = 主人，author 为分身。"""
    post_id = f'p_{_uid()}'
    return post_id, HasnPosts(
        post_id=post_id,
        author_type='agent',
        author_hasn_id=f'a_{_uid()}',
        owner_hasn_id=owner_hasn,
        content='[E2E] 草稿帖待删除。',
        status='pending_review',
    )


@pytest_asyncio.fixture
async def session():
    engine = create_async_engine(SQLALCHEMY_DATABASE_URL, poolclass=NullPool)
    try:
        async with engine.connect() as conn:
            await conn.execute(select(1))
    except Exception as exc:
        await engine.dispose()
        pytest.skip(f'本地 PostgreSQL 不可达，跳过: {exc!r}')

    sess = async_sessionmaker(engine, expire_on_commit=False)()
    owner_hasn = f'h_pd_{_uid()}'
    sess.add(
        HasnHumans(hasn_id=owner_hasn, star_id=f's_{_uid()}', user_id=_USER_ID, nickname='PD Owner', status='active')
    )
    await sess.flush()
    try:
        yield SimpleNamespace(db=sess, owner=owner_hasn)
    finally:
        await sess.rollback()
        await sess.close()
        await engine.dispose()


@pytest_asyncio.fixture
async def http(session):
    async def _yield_session():
        yield session.db

    async def _auth_inject(request: Request) -> str:
        request.scope['user'] = SimpleNamespace(id=_USER_ID)
        request.scope['auth'] = ['authenticated']
        return 'pd-token'

    _APP.dependency_overrides[get_db] = _yield_session
    _APP.dependency_overrides[get_db_transaction] = _yield_session
    _APP.dependency_overrides[DependsJwtAuth.dependency] = _auth_inject

    client = httpx.AsyncClient(transport=httpx.ASGITransport(app=_APP), base_url='http://pd')
    try:
        yield SimpleNamespace(client=client, owner=session.owner, db=session.db)
    finally:
        await client.aclose()
        _APP.dependency_overrides.clear()


@pytest.mark.asyncio
async def test_delete_draft_post_soft_deletes_over_http(http) -> None:
    """主人删除自己名下分身的草稿帖：200 + 软删除（status=deleted）。"""
    post_id, post = _draft_post(http.owner)
    http.db.add(post)
    await http.db.flush()

    resp = await http.client.request('DELETE', f'/api/v1/community/app/posts/{post_id}')
    assert resp.status_code == 200, f'{resp.status_code}: {resp.text}'
    body = resp.json()
    assert body.get('code') == 200, body
    assert body['data'] == {'post_id': post_id, 'status': 'deleted'}

    refreshed = (
        await http.db.execute(select(HasnPosts).where(HasnPosts.post_id == post_id))
    ).scalar_one()
    assert refreshed.status == 'deleted'


@pytest.mark.asyncio
async def test_delete_post_not_found(session) -> None:
    """不存在的帖子 → NotFoundError（HTTP 层映射 404，不再是 405）。"""
    with pytest.raises(errors.NotFoundError):
        await community_service.delete_post(
            session.db, user_id=_USER_ID, hasn_id=session.owner, post_id=f'p_{_uid()}'
        )


@pytest.mark.asyncio
async def test_delete_post_forbidden_for_non_owner(session) -> None:
    """越权：删除不属于自己的帖子 → ForbiddenError（HTTP 层映射 403）。"""
    post_id = f'p_{_uid()}'
    session.db.add(
        HasnPosts(
            post_id=post_id,
            author_type='human',
            author_hasn_id=f'h_other_{_uid()}',
            owner_hasn_id=f'h_other_{_uid()}',
            content='别人的帖子',
            status='published',
        )
    )
    await session.db.flush()

    with pytest.raises(errors.ForbiddenError):
        await community_service.delete_post(
            session.db, user_id=_USER_ID, hasn_id=session.owner, post_id=post_id
        )
