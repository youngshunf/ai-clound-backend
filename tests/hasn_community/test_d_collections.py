"""
D-backend 回归：收藏夹 CRUD + collect/uncollect + 计数维护 + is_collected。
doc-13 §3.2/§2.4。连真实 PG，事务回滚隔离。
"""
from __future__ import annotations

import pytest
from sqlalchemy import text

from backend.app.hasn_community.service.community_service import community_service
from tests.hasn_community.conftest import seed_human, seed_post


@pytest.mark.asyncio
async def test_collect_auto_creates_default_collection(db):
    owner = await seed_human(db, nickname='收藏者')
    author = await seed_human(db, nickname='作者')
    pid = await seed_post(db, author_hasn_id=author['hasn_id'], content='被收藏的帖子')

    # 无收藏夹时收藏 → 自动建默认收藏夹
    res = await community_service.collect(
        db, owner_hasn_id=owner['hasn_id'], target_type='post', target_id=pid
    )
    assert res['is_collected'] is True
    assert res['collection_id']

    cols = await community_service.list_collections(db, owner_hasn_id=owner['hasn_id'])
    assert len(cols['items']) == 1
    assert cols['items'][0]['name'] == '默认收藏夹'
    assert cols['items'][0]['item_count'] == 1

    # 帖子 collect_count +1
    cc = (
        await db.execute(
            text('SELECT collect_count FROM hasn_community.hasn_posts WHERE post_id = :p'),
            {'p': pid},
        )
    ).scalar()
    assert cc == 1


@pytest.mark.asyncio
async def test_collect_idempotent(db):
    owner = await seed_human(db, nickname='收藏者')
    author = await seed_human(db, nickname='作者')
    pid = await seed_post(db, author_hasn_id=author['hasn_id'])

    await community_service.collect(db, owner_hasn_id=owner['hasn_id'], target_type='post', target_id=pid)
    await community_service.collect(db, owner_hasn_id=owner['hasn_id'], target_type='post', target_id=pid)

    cols = await community_service.list_collections(db, owner_hasn_id=owner['hasn_id'])
    assert cols['items'][0]['item_count'] == 1  # 不重复计数
    cc = (
        await db.execute(
            text('SELECT collect_count FROM hasn_community.hasn_posts WHERE post_id = :p'),
            {'p': pid},
        )
    ).scalar()
    assert cc == 1


@pytest.mark.asyncio
async def test_uncollect_decrements_counts(db):
    owner = await seed_human(db, nickname='收藏者')
    author = await seed_human(db, nickname='作者')
    pid = await seed_post(db, author_hasn_id=author['hasn_id'])

    await community_service.collect(db, owner_hasn_id=owner['hasn_id'], target_type='post', target_id=pid)
    res = await community_service.uncollect(
        db, owner_hasn_id=owner['hasn_id'], target_type='post', target_id=pid
    )
    assert res['is_collected'] is False

    cols = await community_service.list_collections(db, owner_hasn_id=owner['hasn_id'])
    assert cols['items'][0]['item_count'] == 0
    cc = (
        await db.execute(
            text('SELECT collect_count FROM hasn_community.hasn_posts WHERE post_id = :p'),
            {'p': pid},
        )
    ).scalar()
    assert cc == 0


@pytest.mark.asyncio
async def test_create_list_delete_collection(db):
    owner = await seed_human(db, nickname='收藏者')
    created = await community_service.create_collection(
        db, owner_hasn_id=owner['hasn_id'], name='技术收藏', is_public=True
    )
    assert created['name'] == '技术收藏'

    cols = await community_service.list_collections(db, owner_hasn_id=owner['hasn_id'])
    assert any(c['collection_id'] == created['collection_id'] for c in cols['items'])

    await community_service.delete_collection(
        db, owner_hasn_id=owner['hasn_id'], collection_id=created['collection_id']
    )
    cols2 = await community_service.list_collections(db, owner_hasn_id=owner['hasn_id'])
    assert all(c['collection_id'] != created['collection_id'] for c in cols2['items'])


@pytest.mark.asyncio
async def test_collection_items_preview(db):
    owner = await seed_human(db, nickname='收藏者')
    author = await seed_human(db, nickname='作者')
    pid = await seed_post(db, author_hasn_id=author['hasn_id'], content='这是一段可以预览的帖子内容')

    res = await community_service.collect(
        db, owner_hasn_id=owner['hasn_id'], target_type='post', target_id=pid
    )
    items = await community_service.get_collection_items(
        db, owner_hasn_id=owner['hasn_id'], collection_id=res['collection_id']
    )
    assert len(items['items']) == 1
    assert items['items'][0]['target_id'] == pid
    assert '可以预览' in items['items'][0]['preview']


@pytest.mark.asyncio
async def test_delete_others_collection_forbidden(db):
    owner = await seed_human(db, nickname='本人')
    other = await seed_human(db, nickname='他人')
    created = await community_service.create_collection(
        db, owner_hasn_id=owner['hasn_id'], name='私人收藏'
    )
    from backend.common.exception import errors

    with pytest.raises(errors.NotFoundError):
        await community_service.delete_collection(
            db, owner_hasn_id=other['hasn_id'], collection_id=created['collection_id']
        )


@pytest.mark.asyncio
async def test_collection_detail_public_visible_to_others(db):
    """公开收藏夹：非本人也能看详情（owner 信息 + 内容项），is_owner=False。"""
    owner = await seed_human(db, nickname='收藏夹主人')
    viewer = await seed_human(db, nickname='访客')
    author = await seed_human(db, nickname='作者')
    pid = await seed_post(db, author_hasn_id=author['hasn_id'], content='公开收藏的内容')

    col = await community_service.create_collection(
        db, owner_hasn_id=owner['hasn_id'], name='公开夹', is_public=True
    )
    await community_service.collect(
        db,
        owner_hasn_id=owner['hasn_id'],
        target_type='post',
        target_id=pid,
        collection_id=col['collection_id'],
    )

    detail = await community_service.get_collection_detail(
        db, viewer_hasn_id=viewer['hasn_id'], collection_id=col['collection_id']
    )
    assert detail['collection']['name'] == '公开夹'
    assert detail['collection']['is_owner'] is False
    assert detail['collection']['owner']['hasn_id'] == owner['hasn_id']
    assert any(it['target_id'] == pid for it in detail['items'])


@pytest.mark.asyncio
async def test_collection_detail_private_hidden_from_others(db):
    """私有收藏夹：非本人访问 → 404（不泄露存在）。"""
    owner = await seed_human(db, nickname='收藏夹主人')
    viewer = await seed_human(db, nickname='访客')
    col = await community_service.create_collection(
        db, owner_hasn_id=owner['hasn_id'], name='私密夹', is_public=False
    )
    from backend.common.exception import errors

    with pytest.raises(errors.NotFoundError):
        await community_service.get_collection_detail(
            db, viewer_hasn_id=viewer['hasn_id'], collection_id=col['collection_id']
        )


@pytest.mark.asyncio
async def test_collection_detail_private_visible_to_owner(db):
    """私有收藏夹：本人可见，is_owner=True。"""
    owner = await seed_human(db, nickname='收藏夹主人')
    col = await community_service.create_collection(
        db, owner_hasn_id=owner['hasn_id'], name='私密夹', is_public=False
    )
    detail = await community_service.get_collection_detail(
        db, viewer_hasn_id=owner['hasn_id'], collection_id=col['collection_id']
    )
    assert detail['collection']['is_owner'] is True
    assert detail['collection']['name'] == '私密夹'
