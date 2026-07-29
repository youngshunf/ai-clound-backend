"""文集订阅、出版物投影、更新通知与阅读去重的真实 PostgreSQL 回归。"""

from __future__ import annotations

import pytest

from sqlalchemy import select

from backend.app.hasn_community.model import HasnDocSpaceSubscriptions
from backend.app.hasn_community.service.community_service import community_service
from backend.app.hasn_community.service.doc_service import doc_service
from backend.app.hasn_community.service.notification_service import notification_service
from backend.database.redis import redis_client
from tests.hasn_community.conftest import seed_article, seed_human


@pytest.mark.asyncio
async def test_doc_space_subscription_is_idempotent_and_updates_authoritative_count(db):
    """重复订阅和取消都必须幂等，权威计数只随真实关系变化。"""
    author = await seed_human(db, nickname='订阅文集作者')
    reader = await seed_human(db, nickname='订阅读者')
    space = await doc_service.create_space(
        db,
        owner_hasn_id=author['hasn_id'],
        author_type='human',
        author_hasn_id=author['hasn_id'],
        owner_user_id=author['user_id'],
        title='订阅闭环文集',
        default_visibility='public',
    )

    first = await doc_service.subscribe(
        db,
        ident=space['space_id'],
        subscriber_hasn_id=reader['hasn_id'],
    )
    second = await doc_service.subscribe(
        db,
        ident=space['space_id'],
        subscriber_hasn_id=reader['hasn_id'],
    )
    assert first['is_subscribed'] is True
    assert second['is_subscribed'] is True
    assert first['subscribe_count'] == second['subscribe_count'] == 1
    relations = (
        await db.execute(
            select(HasnDocSpaceSubscriptions).where(
                HasnDocSpaceSubscriptions.space_id == space['space_id'],
                HasnDocSpaceSubscriptions.subscriber_hasn_id == reader['hasn_id'],
            )
        )
    ).scalars().all()
    assert len(relations) == 1

    removed = await doc_service.unsubscribe(
        db,
        ident=space['space_id'],
        subscriber_hasn_id=reader['hasn_id'],
    )
    removed_again = await doc_service.unsubscribe(
        db,
        ident=space['space_id'],
        subscriber_hasn_id=reader['hasn_id'],
    )
    assert removed['is_subscribed'] is False
    assert removed_again['is_subscribed'] is False
    assert removed['subscribe_count'] == removed_again['subscribe_count'] == 0


@pytest.mark.asyncio
async def test_doc_space_lists_project_outline_drafts_and_stable_subscribed_cursor(db):
    """发现/我的/已订阅列表共享真实目录骨架，草稿数只对 owner 暴露。"""
    author = await seed_human(db, nickname='出版物作者')
    reader = await seed_human(db, nickname='出版物读者')
    spaces = []
    for index in range(2):
        spaces.append(
            await doc_service.create_space(
                db,
                owner_hasn_id=author['hasn_id'],
                author_type='human',
                author_hasn_id=author['hasn_id'],
                owner_user_id=author['user_id'],
                title=f'出版物文集 {index}',
                default_visibility='public',
            )
        )
        await doc_service.subscribe(
            db,
            ident=spaces[-1]['space_id'],
            subscriber_hasn_id=reader['hasn_id'],
        )

    directory = await doc_service.create_node(
        db,
        space_id=spaces[0]['space_id'],
        actor_hasn_id=author['hasn_id'],
        node_type='directory',
        title='第一章',
    )
    published_article = await seed_article(
        db,
        author_hasn_id=author['hasn_id'],
        title='公开叶子',
    )
    draft_article = await seed_article(
        db,
        author_hasn_id=author['hasn_id'],
        title='待审叶子',
        status='pending_review',
    )
    for article_id, title in (
        (published_article, '公开叶子'),
        (draft_article, '待审叶子'),
    ):
        await doc_service.create_node(
            db,
            space_id=spaces[0]['space_id'],
            actor_hasn_id=author['hasn_id'],
            node_type='article',
            title=title,
            parent_node_id=directory['node_id'],
            article_id=article_id,
        )

    mine = await doc_service.list_mine(db, owner_hasn_id=author['hasn_id'])
    projected = next(item for item in mine if item['space_id'] == spaces[0]['space_id'])
    assert projected['outline'] == [{'title': '第一章', 'article_count': 2}]
    assert projected['draft_count'] == 1

    discovered = await doc_service.discover_public(
        db,
        viewer_hasn_id=reader['hasn_id'],
        limit=20,
    )
    public_projection = next(
        item for item in discovered['items'] if item['space_id'] == spaces[0]['space_id']
    )
    assert public_projection['is_subscribed'] is True
    assert public_projection['subscribe_count'] == 1
    assert 'draft_count' not in public_projection

    first = await doc_service.list_subscribed(
        db,
        subscriber_hasn_id=reader['hasn_id'],
        limit=1,
    )
    second = await doc_service.list_subscribed(
        db,
        subscriber_hasn_id=reader['hasn_id'],
        cursor=first['next_cursor'],
        limit=1,
    )
    ids = [first['items'][0]['space_id'], second['items'][0]['space_id']]
    assert len(set(ids)) == 2
    assert second['next_cursor'] is None


@pytest.mark.asyncio
async def test_published_article_in_subscribed_space_emits_update_notification(db):
    """公开文章落位后，订阅者收到可跳回权威文集的更新通知。"""
    author = await seed_human(db, nickname='更新作者')
    reader = await seed_human(db, nickname='更新订阅者')
    space = await doc_service.create_space(
        db,
        owner_hasn_id=author['hasn_id'],
        author_type='human',
        author_hasn_id=author['hasn_id'],
        owner_user_id=author['user_id'],
        title='会更新的文集',
        default_visibility='public',
    )
    await doc_service.subscribe(
        db,
        ident=space['space_id'],
        subscriber_hasn_id=reader['hasn_id'],
    )

    created = await community_service.create_article(
        db,
        user_id=author['user_id'],
        hasn_id=author['hasn_id'],
        title='新一期文章',
        content='订阅者应该收到更新。',
        doc_placement={'space_id': space['space_id']},
    )
    notes = await notification_service.list_notifications(
        db,
        recipient_hasn_id=reader['hasn_id'],
    )
    assert notes['items'][0]['type'] == 'community_doc_space_updated'
    assert notes['items'][0]['target'] == {
        'type': 'doc_space',
        'id': space['space_id'],
    }
    assert created['article_id'] in notes['items'][0]['preview']
    assert notes['items'][0]['link'] == f'/community/docs/{space["space_id"]}'


@pytest.mark.asyncio
async def test_doc_space_view_count_uses_real_redis_thirty_minute_window(db):
    """同一读者 30 分钟内重复阅读只写一次权威计数。"""
    author = await seed_human(db, nickname='阅读作者')
    reader = await seed_human(db, nickname='阅读访客')
    space = await doc_service.create_space(
        db,
        owner_hasn_id=author['hasn_id'],
        author_type='human',
        author_hasn_id=author['hasn_id'],
        owner_user_id=author['user_id'],
        title='阅读统计文集',
        default_visibility='public',
    )
    redis_key = doc_service.view_dedup_key(space['space_id'], reader['hasn_id'])
    await redis_client.delete(redis_key)
    try:
        await doc_service.get_tree(
            db,
            space_ident=space['space_id'],
            viewer_hasn_id=reader['hasn_id'],
        )
        await doc_service.get_tree(
            db,
            space_ident=space['space_id'],
            viewer_hasn_id=reader['hasn_id'],
        )
        detail = await doc_service.get_space(
            db,
            space['space_id'],
            viewer_hasn_id=reader['hasn_id'],
        )
        assert detail['view_count'] == 1
    finally:
        await redis_client.delete(redis_key)
