"""帖子详情可见性回归测试（service 层，真实 PG）。

回归 bug：分身发帖默认 pending_review，主人从卡片/草稿箱点「查看帖子」时，cloud get_post
此前硬过滤 `status == 'published'` → 主人看自己名下分身的草稿帖也 404。

可见性约定（本测试守卫）：
- published：任何人可看（含未登录 viewer）。
- 草稿/待审/退回（非 published 且非 deleted）：仅作者本人或（分身帖的）主人可看，他人 404。
- deleted：一律 404（含主人）。

零 mock：最小化用真实 service + 真实 PG（NullPool + rollback 清理）。
"""

from __future__ import annotations

import uuid

import pytest
import pytest_asyncio

from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

from backend.app.hasn.model.hasn_humans import HasnHumans
from backend.app.hasn_community.model.hasn_posts import HasnPosts
from backend.app.hasn_community.service.community_service import community_service
from backend.common.exception import errors
from backend.database.db import SQLALCHEMY_DATABASE_URL

_OWNER_UID = 970000 + int(uuid.uuid4().int % 20000)


def _uid() -> str:
    return uuid.uuid4().hex[:8]


@pytest_asyncio.fixture
async def ctx():
    engine = create_async_engine(SQLALCHEMY_DATABASE_URL, poolclass=NullPool)
    try:
        async with engine.connect() as conn:
            await conn.execute(select(1))
    except Exception as exc:
        await engine.dispose()
        pytest.skip(f'本地 PostgreSQL 不可达，跳过: {exc!r}')

    sess = async_sessionmaker(engine, expire_on_commit=False)()
    owner_hasn = f'h_owner_{_uid()}'
    sess.add(
        HasnHumans(hasn_id=owner_hasn, star_id=f's_{_uid()}', user_id=_OWNER_UID, nickname='Owner', status='active')
    )
    await sess.flush()
    try:
        yield sess, owner_hasn
    finally:
        await sess.rollback()
        await sess.close()
        await engine.dispose()


async def _add_post(sess, *, owner_hasn: str, status: str) -> str:
    post_id = f'p_{_uid()}'
    sess.add(
        HasnPosts(
            post_id=post_id,
            author_type='agent',
            author_hasn_id=f'a_{_uid()}',
            owner_hasn_id=owner_hasn,
            content='[E2E] 分身草稿帖。',
            status=status,
        )
    )
    await sess.flush()
    return post_id


@pytest.mark.asyncio
async def test_owner_can_open_own_pending_draft(ctx) -> None:
    """主人点开自己名下分身的待审草稿帖 → 返回详情（不再 404）。"""
    sess, owner_hasn = ctx
    post_id = await _add_post(sess, owner_hasn=owner_hasn, status='pending_review')

    detail = await community_service.get_post(sess, post_id=post_id, user_id=_OWNER_UID)
    assert detail['post_id'] == post_id
    assert detail['status'] == 'pending_review'


@pytest.mark.asyncio
async def test_published_post_visible_to_anyone(ctx) -> None:
    """已发布帖：未登录 viewer 也能看。"""
    sess, owner_hasn = ctx
    post_id = await _add_post(sess, owner_hasn=owner_hasn, status='published')

    detail = await community_service.get_post(sess, post_id=post_id, user_id=None)
    assert detail['post_id'] == post_id


@pytest.mark.asyncio
async def test_deleted_post_hidden_even_from_owner(ctx) -> None:
    """已删除帖：主人也不可见（404）。"""
    sess, owner_hasn = ctx
    post_id = await _add_post(sess, owner_hasn=owner_hasn, status='deleted')

    with pytest.raises(errors.NotFoundError):
        await community_service.get_post(sess, post_id=post_id, user_id=_OWNER_UID)


@pytest.mark.asyncio
async def test_others_draft_hidden(ctx) -> None:
    """他人名下的待审草稿：当前主人不可见（404，不泄露存在性）。"""
    sess, _owner_hasn = ctx
    post_id = await _add_post(sess, owner_hasn=f'h_other_{_uid()}', status='pending_review')

    with pytest.raises(errors.NotFoundError):
        await community_service.get_post(sess, post_id=post_id, user_id=_OWNER_UID)
