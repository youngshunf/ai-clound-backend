"""统一社区搜索闭环：分组分页、隐私设置、黑名单和内容 ACL。"""
from __future__ import annotations

import pytest
from sqlalchemy import text

from backend.app.hasn_community.service.community_service import community_service
from backend.app.hasn_community.service.settings_service import community_settings_service
from backend.app.hasn_im.application.provider import get_transactional_relation_gateway
from backend.database.db import uuid4_str
from tests.hasn_community.conftest import seed_agent, seed_article, seed_human, seed_post


@pytest.mark.asyncio
async def test_grouped_search_respects_profile_visibility_and_content_acl(db):
    token = f'闭环检索{uuid4_str().replace("-", "")[:8]}'
    viewer = await seed_human(db, nickname='搜索者')
    visible = await seed_human(db, nickname=f'{token}公开作者')
    hidden = await seed_human(db, nickname=f'{token}隐藏作者')
    await community_settings_service.update_community_settings(
        db,
        hasn_id=hidden['hasn_id'],
        patch={'show_profile': False},
    )

    public_post = await seed_post(
        db,
        author_hasn_id=visible['hasn_id'],
        content=f'{token} 公开帖子',
    )
    private_post = await seed_post(
        db,
        author_hasn_id=visible['hasn_id'],
        content=f'{token} 私密帖子',
    )
    await db.execute(
        text(
            "UPDATE hasn_community.hasn_posts SET visibility = 'private' "
            'WHERE post_id = :post_id'
        ),
        {'post_id': private_post},
    )
    await seed_post(
        db,
        author_hasn_id=hidden['hasn_id'],
        content=f'{token} 隐藏作者帖子',
    )
    public_article = await seed_article(
        db,
        author_hasn_id=visible['hasn_id'],
        title=f'{token} 公开文章',
    )
    await seed_article(
        db,
        author_hasn_id=visible['hasn_id'],
        title=f'{token} 私密文章',
        visibility='private',
    )

    posts = await community_service.search_group(
        db,
        query=token,
        group='posts',
        viewer_user_id=viewer['user_id'],
        limit=10,
    )
    assert posts['total'] == 1
    assert [item['post_id'] for item in posts['items']] == [public_post]

    articles = await community_service.search_group(
        db,
        query=token,
        group='articles',
        viewer_user_id=viewer['user_id'],
        limit=10,
    )
    assert articles['total'] == 1
    assert [item['article_id'] for item in articles['items']] == [public_article]

    humans = await community_service.search_group(
        db,
        query=token,
        group='humans',
        viewer_user_id=viewer['user_id'],
        limit=1,
    )
    assert humans['total'] == 1
    assert humans['items'][0]['hasn_id'] == visible['hasn_id']
    assert humans['items'][0]['friendship_status'] == 'none'
    assert humans['next_cursor'] is None


@pytest.mark.asyncio
async def test_grouped_agent_search_filters_social_switch_and_paginates(db):
    token = f'分身检索{uuid4_str().replace("-", "")[:8]}'
    viewer = await seed_human(db, nickname='搜索者')
    owner = await seed_human(db, nickname='分身主人')
    first = await seed_agent(
        db,
        owner_hasn_id=owner['hasn_id'],
        display_name=f'{token}甲',
    )
    await seed_agent(
        db,
        owner_hasn_id=owner['hasn_id'],
        display_name=f'{token}乙',
    )
    hidden = await seed_agent(
        db,
        owner_hasn_id=owner['hasn_id'],
        display_name=f'{token}隐藏',
    )
    relation_gateway = get_transactional_relation_gateway(db)
    await relation_gateway.update_agent_communication_settings(
        owner_hasn_id=owner['hasn_id'],
        agent_hasn_id=hidden['hasn_id'],
        social_enabled=False,
    )

    page1 = await community_service.search_group(
        db,
        query=token,
        group='agents',
        viewer_user_id=viewer['user_id'],
        limit=1,
        relation_gateway=relation_gateway,
    )
    assert page1['total'] == 2
    assert len(page1['items']) == 1
    assert page1['items'][0]['type'] == 'agent'
    assert page1['items'][0]['online_status'] in {'online', 'offline'}
    assert page1['next_cursor'] == '1'

    page2 = await community_service.search_group(
        db,
        query=token,
        group='agents',
        viewer_user_id=viewer['user_id'],
        cursor=page1['next_cursor'],
        limit=1,
        relation_gateway=relation_gateway,
    )
    assert page2['total'] == 2
    assert page2['next_cursor'] is None
    assert page2['items'][0]['hasn_id'] != page1['items'][0]['hasn_id']
    assert hidden['hasn_id'] not in {
        page1['items'][0]['hasn_id'],
        page2['items'][0]['hasn_id'],
    }
    assert first['hasn_id'] in {
        page1['items'][0]['hasn_id'],
        page2['items'][0]['hasn_id'],
    }
