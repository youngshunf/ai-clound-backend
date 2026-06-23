"""社区「可被搜索」与黑名单双向过滤真生效回归测试。

覆盖「将真实的设置落地」之 searchable + 黑名单（doc-13 §2.3）：
- searchable=False：作者内容从「搜索结果」剔除，但不影响普通推荐流（设置只约束搜索）；
- 黑名单：信息流双向不可见（A 拉黑 B → A 看不到 B、B 也看不到 A）；
- 黑名单：关注双向被拒（任一方向拉黑都拦）；
- 黑名单：评论被拒（评论者与内容作者互拉黑）。

连真实 PG，事务回滚隔离。用 ORM（schema-aware）写入帖子，不用 conftest.seed_post
（其裸 INSERT 不带 hasn_community schema 前缀）。
"""
from __future__ import annotations

import pytest

from backend.app.hasn_community.model import HasnPosts
from backend.app.hasn_community.service.community_service import community_service
from backend.app.hasn_community.service.settings_service import community_settings_service
from backend.common.exception import errors
from backend.database.db import uuid4_str
from tests.hasn_community.conftest import seed_human


async def _seed_published_post(db, *, author_hasn_id: str, content: str) -> str:
    """经 ORM（schema-aware）插一条已发布 human 帖，返回 post_id。"""
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


def _post_ids(feed: dict) -> set[str]:
    return {it['post_id'] for it in feed['items'] if it.get('post_id')}


@pytest.mark.asyncio
async def test_searchable_excludes_from_search_only(db):
    """searchable=False 的作者内容从搜索剔除；普通推荐流不受此约束。"""
    alice = await seed_human(db, nickname='可搜到的人')
    bob = await seed_human(db, nickname='不愿被搜到的人')
    token = f'唯一搜索词{uuid4_str()[:8]}'

    post_a = await _seed_published_post(db, author_hasn_id=alice['hasn_id'], content=f'{token} 来自 alice')
    post_b = await _seed_published_post(db, author_hasn_id=bob['hasn_id'], content=f'{token} 来自 bob')

    # 默认（均 searchable=True）→ 搜索两条都在
    res0 = await community_service.search(db, query=token)
    assert {post_a, post_b} <= _post_ids(res0)

    # bob 关闭「可被搜索」
    await community_settings_service.update_community_settings(
        db, hasn_id=bob['hasn_id'], patch={'searchable': False}
    )

    # 搜索：只剩 alice，bob 被剔除
    res1 = await community_service.search(db, query=token)
    ids = _post_ids(res1)
    assert post_a in ids
    assert post_b not in ids

    # 普通推荐流（非搜索）：bob 仍在——searchable 只约束搜索结果
    feed = await community_service.get_feed(db, feed_type='recommend', q=token)
    assert {post_a, post_b} <= _post_ids(feed)


@pytest.mark.asyncio
async def test_block_hides_feed_bidirectionally(db):
    """A 拉黑 B 后：A 看不到 B 的内容，B 也看不到 A 的内容（双向）。"""
    alice = await seed_human(db, nickname='拉黑发起者')
    bob = await seed_human(db, nickname='被拉黑者')
    token = f'黑名单流{uuid4_str()[:8]}'

    post_a = await _seed_published_post(db, author_hasn_id=alice['hasn_id'], content=f'{token} alice')
    post_b = await _seed_published_post(db, author_hasn_id=bob['hasn_id'], content=f'{token} bob')

    # 拉黑前：彼此可见
    feed_a0 = await community_service.get_feed(db, user_id=alice['user_id'], feed_type='recommend', q=token)
    feed_b0 = await community_service.get_feed(db, user_id=bob['user_id'], feed_type='recommend', q=token)
    assert post_b in _post_ids(feed_a0)
    assert post_a in _post_ids(feed_b0)

    # alice 拉黑 bob
    await community_settings_service.add_block(
        db, blocker_hasn_id=alice['hasn_id'], blocked_hasn_id=bob['hasn_id']
    )

    # 拉黑后：A 看不到 B（自己发的仍在），B 看不到 A（自己发的仍在）
    feed_a1 = await community_service.get_feed(db, user_id=alice['user_id'], feed_type='recommend', q=token)
    feed_b1 = await community_service.get_feed(db, user_id=bob['user_id'], feed_type='recommend', q=token)
    ids_a = _post_ids(feed_a1)
    ids_b = _post_ids(feed_b1)
    assert post_b not in ids_a and post_a in ids_a
    assert post_a not in ids_b and post_b in ids_b


@pytest.mark.asyncio
async def test_block_rejects_follow_both_directions(db):
    """拉黑关系下，任一方向发起关注都被拒。"""
    alice = await seed_human(db, nickname='拉黑发起者')
    bob = await seed_human(db, nickname='被拉黑者')

    await community_settings_service.add_block(
        db, blocker_hasn_id=alice['hasn_id'], blocked_hasn_id=bob['hasn_id']
    )

    # 被拉黑者想关注拉黑者 → 拒
    with pytest.raises(errors.RequestError):
        await community_service.create_follow(
            db, user_id=bob['user_id'], hasn_id=bob['hasn_id'],
            target_type='human', target_hasn_id=alice['hasn_id'],
        )

    # 拉黑者想关注被拉黑者 → 同样拒（双向）
    with pytest.raises(errors.RequestError):
        await community_service.create_follow(
            db, user_id=alice['user_id'], hasn_id=alice['hasn_id'],
            target_type='human', target_hasn_id=bob['hasn_id'],
        )


@pytest.mark.asyncio
async def test_block_rejects_comment(db):
    """评论者与内容作者互为拉黑 → 评论被拒；无拉黑则正常评论。"""
    author = await seed_human(db, nickname='帖子作者')
    commenter = await seed_human(db, nickname='评论者')
    post_id = await _seed_published_post(db, author_hasn_id=author['hasn_id'], content='可评论的帖子')

    # 无拉黑：评论正常
    ok = await community_service.create_comment(
        db, target_type='post', target_id=post_id, hasn_id=commenter['hasn_id'],
        content='第一条评论', user_id=commenter['user_id'], author_type='human', status='visible',
    )
    assert ok['comment_id']

    # 作者拉黑评论者 → 再评论被拒
    await community_settings_service.add_block(
        db, blocker_hasn_id=author['hasn_id'], blocked_hasn_id=commenter['hasn_id']
    )
    with pytest.raises(errors.RequestError):
        await community_service.create_comment(
            db, target_type='post', target_id=post_id, hasn_id=commenter['hasn_id'],
            content='拉黑后评论', user_id=commenter['user_id'], author_type='human', status='visible',
        )
