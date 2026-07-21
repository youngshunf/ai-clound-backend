"""评论权限预判可见性真实 PG 集成测试（零 mock）。

福仔要求：已关闭评论时不显示评论输入框，改为提示「作者已关闭评论」，而不是让用户
点提交才被拒（422/400）。本测试验证 get_post/get_article 详情接口正确暴露
can_comment / comment_disabled_reason 字段（复用写路径同一套 _check_can_comment
判定），覆盖 closed/followers/all 三种 comment_policy 以及作者本人恒可评论的分支。

真实 PG :15432，独立 session + 末尾回滚不留脏数据。
"""

from __future__ import annotations

import uuid

import pytest
import pytest_asyncio

from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

from backend.app.hasn.model.hasn_humans import HasnHumans
from backend.app.hasn_community.model import HasnFollows
from backend.app.hasn_community.service.community_service import community_service
from backend.app.hasn_core import HasnAgents
from backend.database.db import SQLALCHEMY_DATABASE_URL

pytestmark = pytest.mark.asyncio


def _uid() -> str:
    return uuid.uuid4().hex[:8]


@pytest_asyncio.fixture
async def pg():
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


async def _make_human(pg, *, user_id: int, hasn_id: str) -> None:
    """建一行最小 HasnHumans，供 _resolve_human_hasn_id(user_id) 解析出 hasn_id。"""
    pg.add(HasnHumans(hasn_id=hasn_id, star_id=f'{_uid()}#star', user_id=user_id, nickname=f'探针用户_{_uid()}'))
    await pg.flush()


async def test_get_post_closed_policy_hides_input_for_others_but_not_author(pg) -> None:
    """closed：作者本人 can_comment=True；他人 can_comment=False 且带原因文案。"""
    author = f'h_cpv_author_{_uid()}'
    other = f'h_cpv_other_{_uid()}'
    await _make_human(pg, user_id=101, hasn_id=author)
    await _make_human(pg, user_id=102, hasn_id=other)

    post = await community_service.create_post(
        pg, user_id=101, hasn_id=author, content='closed 评论策略探针', tags=[], comment_policy='closed'
    )

    as_author = await community_service.get_post(pg, post_id=post['post_id'], user_id=101)
    assert as_author['can_comment'] is True
    assert as_author['comment_disabled_reason'] is None

    as_other = await community_service.get_post(pg, post_id=post['post_id'], user_id=102)
    assert as_other['can_comment'] is False
    assert as_other['comment_disabled_reason']

    as_anonymous = await community_service.get_post(pg, post_id=post['post_id'], user_id=None)
    assert as_anonymous['can_comment'] is False


async def test_get_post_followers_policy_follower_can_others_cannot(pg) -> None:
    """followers：已关注作者的评论者可评论，陌生人不可。"""
    author = f'h_cpv_author2_{_uid()}'
    follower = f'h_cpv_follower_{_uid()}'
    stranger = f'h_cpv_stranger_{_uid()}'
    await _make_human(pg, user_id=201, hasn_id=author)
    await _make_human(pg, user_id=202, hasn_id=follower)
    await _make_human(pg, user_id=203, hasn_id=stranger)
    pg.add(HasnFollows(follower_hasn_id=follower, target_type='human', target_hasn_id=author))
    await pg.flush()

    post = await community_service.create_post(
        pg, user_id=201, hasn_id=author, content='followers 评论策略探针', tags=[], comment_policy='followers'
    )

    as_follower = await community_service.get_post(pg, post_id=post['post_id'], user_id=202)
    assert as_follower['can_comment'] is True

    as_stranger = await community_service.get_post(pg, post_id=post['post_id'], user_id=203)
    assert as_stranger['can_comment'] is False
    assert as_stranger['comment_disabled_reason']


async def test_get_post_all_policy_everyone_can_comment(pg) -> None:
    """all（默认放行）：任何人 can_comment=True。"""
    author = f'h_cpv_author3_{_uid()}'
    other = f'h_cpv_other3_{_uid()}'
    await _make_human(pg, user_id=301, hasn_id=author)
    await _make_human(pg, user_id=302, hasn_id=other)

    post = await community_service.create_post(
        pg, user_id=301, hasn_id=author, content='all 评论策略探针', tags=[], comment_policy='all'
    )

    as_other = await community_service.get_post(pg, post_id=post['post_id'], user_id=302)
    assert as_other['can_comment'] is True
    assert as_other['comment_disabled_reason'] is None


async def test_get_article_closed_policy_hides_input_for_others_but_not_author(pg) -> None:
    """文章详情接口与帖子共用同一套判定：closed 时他人不可评论，作者本人可。"""
    author = f'h_cpv_art_author_{_uid()}'
    other = f'h_cpv_art_other_{_uid()}'
    await _make_human(pg, user_id=401, hasn_id=author)
    await _make_human(pg, user_id=402, hasn_id=other)

    article = await community_service.create_article(
        pg, user_id=401, hasn_id=author, title='closed 评论策略探针文章', content='正文', tags=[], comment_policy='closed'
    )

    as_author = await community_service.get_article(pg, user_id=401, hasn_id=author, article_id=article['article_id'])
    assert as_author['can_comment'] is True

    as_other = await community_service.get_article(pg, user_id=402, hasn_id=other, article_id=article['article_id'])
    assert as_other['can_comment'] is False
    assert as_other['comment_disabled_reason']


async def test_create_comment_returns_enriched_author_for_human(pg) -> None:
    """human 发评论：返回完整富化评论（对齐 get_comments 单条形状）。

    daemon 本地优先把 create 响应经 apply_authoritative_comment 原样写入镜像 source_json；
    若缺 author，webui 评论列表渲染 comment.author.hasn_id 直接崩（ErrorBoundary）。
    """
    commenter = f'h_ccs_commenter_{_uid()}'
    nickname = f'探针评论者_{_uid()}'
    pg.add(HasnHumans(hasn_id=commenter, star_id=f'{_uid()}#star', user_id=501, nickname=nickname))
    await pg.flush()

    result = await community_service.create_comment(
        pg,
        target_type='post',
        target_id=f'p_nonexistent_{_uid()}',
        hasn_id=commenter,
        user_id=501,
        content='富化形状探针',
    )

    assert result['comment_id']
    assert result['status'] == 'visible'
    assert result['content'] == '富化形状探针'
    assert result['parent_id'] is None
    assert result['like_count'] == 0
    author = result['author']
    assert author['hasn_id'] == commenter
    assert author['type'] == 'human'
    assert author['display_name'] == nickname
    assert 'avatar' in author


async def test_create_comment_returns_enriched_author_with_owner_for_agent(pg, monkeypatch: pytest.MonkeyPatch) -> None:
    """agent 发评论：author 带 owner 副行，且经 _enrich_authors 补 profession/online_status。"""
    # 与 fanout 测试 spy bump_owner 同理：stub 实时 presence，避免依赖真实 Redis
    # （全局 redis_client 的连接绑定在前序测试的 event loop 上，复用即 Event loop is closed）。
    from backend.app.hasn.service.ws_router import ws_router

    async def _offline_map(entity_ids: list[str]) -> dict[str, bool]:
        return {eid: False for eid in entity_ids}

    monkeypatch.setattr(ws_router, 'get_online_map', _offline_map)

    owner = f'h_ccs_owner_{_uid()}'
    agent = f'a_ccs_agent_{_uid()}'
    agent_name = f'探针分身_{_uid()}'
    pg.add(HasnHumans(hasn_id=owner, star_id=f'{_uid()}#star', user_id=601, nickname=f'探针主人_{_uid()}'))
    pg.add(
        HasnAgents(
            hasn_id=agent,
            owner_id=owner,
            star_id=f'{_uid()}#star',
            display_name=agent_name,
            profession='探针专家',
        )
    )
    await pg.flush()

    result = await community_service.create_comment(
        pg,
        target_type='post',
        target_id=f'p_nonexistent_{_uid()}',
        hasn_id=agent,
        content='agent 富化形状探针',
        author_type='agent',
        owner_hasn_id=owner,
        status='pending_review',
    )

    assert result['status'] == 'pending_review'
    author = result['author']
    assert author['hasn_id'] == agent
    assert author['type'] == 'agent'
    assert author['display_name'] == agent_name
    assert author['owner']['hasn_id'] == owner
    assert author['profession'] == '探针专家'
    assert author['online_status'] in ('online', 'offline')
