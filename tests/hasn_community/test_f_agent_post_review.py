"""分身社区内容审核开关（community_settings.agent_post_review）回归测试。

覆盖「将真实的设置落地」之「发帖是否需主人审核」可配置化：
- 设置默认 agent_post_review=True（维持出厂「分身内容进 pending_review 待审」行为）；
- get_agent_post_review helper：默认 True / patch False 后 False / 未知主人保守 True；
- 分身发帖 handler：审核开→pending_review；审核关→published（published_time 落值）；
- 分身评论 handler：审核开→pending_review；审核关→visible（直接公开）。

连真实 PG，事务回滚隔离（conftest db fixture：create_savepoint，handler 内 commit 也只释放 savepoint）。
"""
from __future__ import annotations

from types import SimpleNamespace

import pytest

from backend.app.hasn_community.model import HasnPosts
from backend.app.hasn_community.service.community_tool_handlers import (
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
async def test_agent_create_post_review_off_is_published(db):
    """审核关：分身发帖直接 published 并落 published_time。"""
    owner = await seed_human(db, nickname='主人')
    await community_settings_service.update_community_settings(
        db, hasn_id=owner['hasn_id'], patch={'agent_post_review': False}
    )
    agent = await seed_agent(db, owner_hasn_id=owner['hasn_id'], display_name='发帖分身')
    payload = _agent_payload(
        agent_hasn_id=agent['hasn_id'], owner_hasn_id=owner['hasn_id'], owner_user_id=owner['user_id']
    )

    result = await handle_community_create_post(db, payload, {'content': '[E2E] 审核关的帖子。'})
    assert result['status'] == 'published'
    assert result['message'] == '帖子已发布'

    # 已发布帖在公共信息流可见（验证 published_time 真落了值，能被 published 流取到）
    detail = await __import__(
        'backend.app.hasn_community.service.community_service', fromlist=['community_service']
    ).community_service.get_post(db, post_id=result['post_id'], user_id=None)
    assert detail['status'] == 'published'


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
