"""分身社区内容主人确认边界与评论审核开关回归测试。

覆盖施工方案 96 的不可绕过边界：
- Agent 发帖、写文章恒进 pending_review，主人设置不能把协作产物直接公开；
- 设置默认 agent_post_review=True，继续控制 Agent 评论是否需审核；
- get_agent_post_review helper：默认 True / patch False 后 False / 未知主人保守 True；
- 分身评论 handler：审核开→pending_review；审核关→visible（直接公开）。

连真实 PG，事务回滚隔离（conftest db fixture：create_savepoint，handler 内 commit 也只释放 savepoint）。
"""
from __future__ import annotations

from types import SimpleNamespace

import pytest
from sqlalchemy import select

from backend.app.hasn_community.model import HasnArticles, HasnPosts
from backend.app.hasn_community.service.community_tool_handlers import (
    handle_community_create_article,
    handle_community_create_comment,
    handle_community_create_post,
)
from backend.app.hasn_community.service.settings_service import community_settings_service
from backend.database.db import uuid4_str
from tests.hasn_community.conftest import seed_agent, seed_human


async def _seed_published_post(db, *, author_hasn_id: str, content: str) -> str:
    """经 ORM（schema-aware）插一条已发布 human 帖，返回 post_id。

    直接走 ORM 路径，覆盖 CommunityBase 的 schema=hasn_community 映射。
    """
    from backend.utils.timezone import timezone

    post_id = f'p_{uuid4_str()[:12]}'
    db.add(
        HasnPosts(
            post_id=post_id,
            author_type='human',
            author_hasn_id=author_hasn_id,
            owner_hasn_id=author_hasn_id,
            origin_workspace_kind='personal',
            content=content,
            visibility='public',
            comment_policy='all',
            generation_type='human',
            status='published',
            published_time=timezone.now(),
        )
    )
    await db.flush()
    return post_id


def _agent_payload(*, agent_hasn_id: str, owner_hasn_id: str, owner_user_id: int) -> SimpleNamespace:
    """构造 handler 读取的最小 Agent 凭据载荷（duck-typing AgentTokenPayload）。"""
    return SimpleNamespace(
        agent_hasn_id=agent_hasn_id,
        owner_hasn_id=owner_hasn_id,
        owner_user_id=owner_user_id,
        agent_name='测试分身',
    )


@pytest.mark.asyncio
async def test_agent_post_review_default_and_helper(db):
    """默认 True；patch False 持久化；helper 三态（默认/已配/未知）正确。"""
    owner = await seed_human(db, nickname='主人')

    # 设置默认含 agent_post_review=True
    s0 = await community_settings_service.get_community_settings(db, hasn_id=owner['hasn_id'])
    assert s0['agent_post_review'] is True

    # helper：默认 True
    assert await community_settings_service.get_agent_post_review(db, owner_hasn_id=owner['hasn_id']) is True

    # 未知主人：保守 True
    assert await community_settings_service.get_agent_post_review(db, owner_hasn_id='h_not_exist') is True

    # patch 关闭审核 → 持久化 + helper 反映
    s1 = await community_settings_service.update_community_settings(
        db, hasn_id=owner['hasn_id'], patch={'agent_post_review': False}
    )
    assert s1['agent_post_review'] is False
    assert await community_settings_service.get_agent_post_review(db, owner_hasn_id=owner['hasn_id']) is False


@pytest.mark.asyncio
async def test_agent_create_post_review_on_is_pending(db):
    """审核开（默认）：分身发帖进 pending_review，不公开。"""
    owner = await seed_human(db, nickname='主人')
    agent = await seed_agent(db, owner_hasn_id=owner['hasn_id'], display_name='发帖分身')
    payload = _agent_payload(
        agent_hasn_id=agent['hasn_id'], owner_hasn_id=owner['hasn_id'], owner_user_id=owner['user_id']
    )

    result = await handle_community_create_post(db, payload, {'content': '[E2E] 审核开的帖子。'})
    assert result['status'] == 'pending_review'
    assert '审核' in result['message']


@pytest.mark.asyncio
async def test_agent_post_and_article_stay_pending_when_comment_review_is_off(db):
    """关闭评论审核也不能让 Agent 帖子或文章绕过主人确认直接公开。"""
    owner = await seed_human(db, nickname='主人')
    await community_settings_service.update_community_settings(
        db, hasn_id=owner['hasn_id'], patch={'agent_post_review': False}
    )
    agent = await seed_agent(db, owner_hasn_id=owner['hasn_id'], display_name='发帖分身')
    payload = _agent_payload(
        agent_hasn_id=agent['hasn_id'], owner_hasn_id=owner['hasn_id'], owner_user_id=owner['user_id']
    )

    post_result = await handle_community_create_post(db, payload, {'content': '[E2E] 强制待确认的帖子。'})
    article_result = await handle_community_create_article(
        db,
        payload,
        {'title': '强制待确认文章', 'content': '## 正文\n\n主人确认前不可公开。'},
    )
    assert post_result['status'] == 'pending_review'
    assert article_result['status'] == 'pending_review'

    post = (
        await db.execute(
            select(HasnPosts).where(HasnPosts.post_id == post_result['post_id'])
        )
    ).scalar_one()
    article = (
        await db.execute(
            select(HasnArticles).where(HasnArticles.article_id == article_result['article_id'])
        )
    ).scalar_one()
    assert post.published_time is None
    assert article.published_time is None


@pytest.mark.asyncio
async def test_agent_create_comment_review_toggle(db):
    """审核开→评论 pending_review；审核关→评论 visible（直接公开）。"""
    owner = await seed_human(db, nickname='主人')
    agent = await seed_agent(db, owner_hasn_id=owner['hasn_id'], display_name='评论分身')
    target_post = await _seed_published_post(db, author_hasn_id=owner['hasn_id'], content='被评论的帖子')
    payload = _agent_payload(
        agent_hasn_id=agent['hasn_id'], owner_hasn_id=owner['hasn_id'], owner_user_id=owner['user_id']
    )

    # 审核开（默认）→ pending_review
    on = await handle_community_create_comment(
        db, payload, {'target_type': 'post', 'target_id': target_post, 'content': '审核开的评论'}
    )
    assert on['status'] == 'pending_review'

    # 审核关 → visible
    await community_settings_service.update_community_settings(
        db, hasn_id=owner['hasn_id'], patch={'agent_post_review': False}
    )
    off = await handle_community_create_comment(
        db, payload, {'target_type': 'post', 'target_id': target_post, 'content': '审核关的评论'}
    )
    assert off['status'] == 'visible'
