"""
A-2 安全红线回归：删除 as_agent_hasn_id 后，WebUI 发帖/发文恒为 human 身份。

对应 docs/.../13-社区设计补丁 §1.5。验证：
1. create_post / create_article 的签名不再接受 as_agent_hasn_id（越权路径已结构性消除）。
2. 实际落库的作者 = 当前 Owner 的 hasn_id、author_type='human'、generation_type='human'。
"""
from __future__ import annotations

import inspect

import pytest
from sqlalchemy import text

from backend.app.hasn_community.service.community_service import community_service
from tests.hasn_community.conftest import seed_human


def test_create_post_signature_has_no_as_agent_hasn_id():
    sig = inspect.signature(community_service.create_post)
    assert 'as_agent_hasn_id' not in sig.parameters


def test_create_article_signature_has_no_as_agent_hasn_id():
    sig = inspect.signature(community_service.create_article)
    assert 'as_agent_hasn_id' not in sig.parameters


@pytest.mark.asyncio
async def test_create_post_author_is_always_human(db):
    human = await seed_human(db, nickname='福仔')

    result = await community_service.create_post(
        db,
        user_id=human['user_id'],
        hasn_id=human['hasn_id'],
        content='这是一条 WebUI 发的真实帖子',
        tags=['测试'],
    )
    assert result['status'] == 'published'

    row = (
        await db.execute(
            text(
                'SELECT author_type, author_hasn_id, owner_hasn_id, generation_type '
                'FROM hasn_community.hasn_posts WHERE post_id = :pid'
            ),
            {'pid': result['post_id']},
        )
    ).one()
    assert row.author_type == 'human'
    assert row.author_hasn_id == human['hasn_id']
    assert row.owner_hasn_id == human['hasn_id']
    assert row.generation_type == 'human'


@pytest.mark.asyncio
async def test_create_article_author_is_always_human(db):
    human = await seed_human(db, nickname='福仔')

    result = await community_service.create_article(
        db,
        user_id=human['user_id'],
        hasn_id=human['hasn_id'],
        title='真实文章标题',
        content='# 正文\n\n这是真实文章内容。',
        tags=['测试'],
    )
    assert result['status'] == 'published'

    row = (
        await db.execute(
            text(
                'SELECT author_type, author_hasn_id, generation_type '
                'FROM hasn_community.hasn_articles WHERE article_id = :aid'
            ),
            {'aid': result['article_id']},
        )
    ).one()
    assert row.author_type == 'human'
    assert row.author_hasn_id == human['hasn_id']
    assert row.generation_type == 'human'
