"""圈子内容治理回归（零 mock，真实 PostgreSQL :15432）。"""

from __future__ import annotations

import uuid

import pytest

from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

from backend.app.hasn_community.model import HasnArticles, HasnCircleMembers, HasnCircles, HasnPosts
from backend.app.hasn_community.service.circle_service import CircleService
from backend.common.exception import errors
from backend.database.db import SQLALCHEMY_DATABASE_URL

pytestmark = pytest.mark.asyncio


async def _pg_reachable() -> bool:
    engine = create_async_engine(SQLALCHEMY_DATABASE_URL, poolclass=NullPool)
    try:
        async with engine.connect() as conn:
            await conn.execute(select(1))
    except Exception:
        return False
    else:
        return True
    finally:
        await engine.dispose()


async def test_moderate_circle_post_and_article() -> None:
    """圈主可分别治理帖子和文章，未知内容类型必须被拒绝。"""
    if not await _pg_reachable():
        pytest.skip('本地 PostgreSQL :15432 不可达，跳过')

    marker = uuid.uuid4().hex[:8]
    circle_id = f'cir_moderation_{marker}'
    owner_hasn_id = f'h_circle_owner_{marker}'
    post_id = f'p_circle_{marker}'
    article_id = f'a_circle_{marker}'
    engine = create_async_engine(SQLALCHEMY_DATABASE_URL, poolclass=NullPool)
    session_maker = async_sessionmaker(engine, expire_on_commit=False)
    try:
        async with session_maker() as db:
            db.add_all([
                HasnCircles(
                    circle_id=circle_id,
                    name=f'圈子治理回归{marker}',
                    slug=f'circle-moderation-{marker}',
                    owner_hasn_id=owner_hasn_id,
                    origin_workspace_kind='personal',
                    origin_workspace_id='920079',
                    visibility='public',
                    join_policy='approval',
                    post_policy='approval',
                    member_count=1,
                    content_count=2,
                    status='active',
                ),
                HasnCircleMembers(
                    circle_id=circle_id,
                    member_hasn_id=owner_hasn_id,
                    member_type='human',
                    owner_hasn_id=owner_hasn_id,
                    role='owner',
                    status='active',
                ),
                HasnPosts(
                    post_id=post_id,
                    author_type='human',
                    author_hasn_id=owner_hasn_id,
                    owner_hasn_id=owner_hasn_id,
                    origin_workspace_kind='personal',
                    origin_workspace_id='920079',
                    content=f'帖子治理回归 {marker}',
                    visibility='circle',
                    comment_policy='all',
                    generation_type='human',
                    status='pending_review',
                    circle_id=circle_id,
                ),
                HasnArticles(
                    article_id=article_id,
                    author_type='human',
                    author_hasn_id=owner_hasn_id,
                    owner_hasn_id=owner_hasn_id,
                    origin_workspace_kind='personal',
                    origin_workspace_id='920079',
                    title=f'文章治理回归 {marker}',
                    content='用于验证圈子文章治理分支。',
                    visibility='circle',
                    comment_policy='all',
                    generation_type='human',
                    status='pending_review',
                    circle_id=circle_id,
                ),
            ])
            await db.commit()

        async with session_maker() as db:
            post_result = await CircleService.moderate_content(
                db,
                ident=circle_id,
                content_type='post',
                content_id=post_id,
                actor_hasn_id=owner_hasn_id,
                action='approve',
            )
            assert post_result['status'] == 'published'
            await db.commit()

        async with session_maker() as db:
            article_result = await CircleService.moderate_content(
                db,
                ident=circle_id,
                content_type='article',
                content_id=article_id,
                actor_hasn_id=owner_hasn_id,
                action='hide',
            )
            assert article_result['status'] == 'hidden'
            await db.commit()

        async with session_maker() as db:
            with pytest.raises(errors.RequestError, match='未知内容类型'):
                await CircleService.moderate_content(
                    db,
                    ident=circle_id,
                    content_type='video',
                    content_id='v_unrecognized',
                    actor_hasn_id=owner_hasn_id,
                    action='approve',
                )
            post = (await db.execute(select(HasnPosts).where(HasnPosts.post_id == post_id))).scalar_one()
            article = (await db.execute(select(HasnArticles).where(HasnArticles.article_id == article_id))).scalar_one()
            assert post.status == 'published'
            assert post.published_time is not None
            assert article.status == 'hidden'
    finally:
        async with session_maker() as db:
            await db.execute(delete(HasnPosts).where(HasnPosts.post_id == post_id))
            await db.execute(delete(HasnArticles).where(HasnArticles.article_id == article_id))
            await db.execute(delete(HasnCircleMembers).where(HasnCircleMembers.circle_id == circle_id))
            await db.execute(delete(HasnCircles).where(HasnCircles.circle_id == circle_id))
            await db.commit()
        await engine.dispose()
