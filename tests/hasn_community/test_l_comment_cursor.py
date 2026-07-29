"""评论列表稳定游标的真实 PostgreSQL 回归测试。"""
from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from sqlalchemy import update

from backend.app.hasn_community.model import HasnComments
from backend.app.hasn_community.service.community_service import community_service
from backend.common.exception import errors
from tests.hasn_community.conftest import seed_human, seed_post


async def _seed_comments_with_ties(db):
    """创建同时包含时间并列和热度并列的确定性评论集合。"""
    author = await seed_human(db, nickname='评论游标作者')
    commenter = await seed_human(db, nickname='评论游标参与者')
    post_id = await seed_post(
        db,
        author_hasn_id=author['hasn_id'],
        content='评论稳定分页测试帖',
    )
    base_time = datetime(2026, 1, 1, 8, 0, tzinfo=UTC)
    specs = [
        ('评论甲', base_time, 8),
        ('评论乙', base_time, 8),
        ('评论丙', base_time + timedelta(minutes=1), 3),
        ('评论丁', base_time + timedelta(minutes=2), 8),
        ('评论戊', base_time + timedelta(minutes=2), 3),
    ]
    comments = []
    for content, created_time, like_count in specs:
        created = await community_service.create_comment(
            db,
            target_type='post',
            target_id=post_id,
            user_id=commenter['user_id'],
            hasn_id=commenter['hasn_id'],
            content=content,
        )
        await db.execute(
            update(HasnComments)
            .where(HasnComments.comment_id == created['comment_id'])
            .values(created_time=created_time, like_count=like_count)
        )
        comments.append({
            'comment_id': created['comment_id'],
            'created_time': created_time,
            'like_count': like_count,
        })
    await db.flush()
    return post_id, comments


async def _collect_pages(db, *, post_id: str, sort: str) -> list[str]:
    """按真实 next_cursor 逐页读取，重复项会立即失败。"""
    cursor = None
    collected: list[str] = []
    for _ in range(10):
        page = await community_service.get_comments(
            db,
            target_type='post',
            target_id=post_id,
            sort=sort,
            cursor=cursor,
            limit=2,
        )
        page_ids = [item['comment_id'] for item in page['items']]
        assert not set(page_ids).intersection(collected)
        collected.extend(page_ids)
        cursor = page['next_cursor']
        if cursor is None:
            return collected
    pytest.fail('评论游标未能在有限页数内结束')


@pytest.mark.asyncio
@pytest.mark.parametrize('sort', ['time_asc', 'time_desc', 'hot'])
async def test_comment_cursor_is_stable_for_all_supported_sorts(db, sort: str):
    """时间与热度排序在并列键下仍完整遍历，且末页不再返回游标。"""
    post_id, comments = await _seed_comments_with_ties(db)
    if sort == 'time_asc':
        expected = sorted(
            comments,
            key=lambda item: (item['created_time'], item['comment_id']),
        )
    elif sort == 'time_desc':
        expected = sorted(
            comments,
            key=lambda item: (item['created_time'], item['comment_id']),
            reverse=True,
        )
    else:
        expected = sorted(
            comments,
            key=lambda item: (item['like_count'], item['created_time'], item['comment_id']),
            reverse=True,
        )

    collected = await _collect_pages(db, post_id=post_id, sort=sort)

    assert collected == [item['comment_id'] for item in expected]


@pytest.mark.asyncio
async def test_comment_cursor_rejects_malformed_value(db):
    """无效游标必须显式失败，不能静默回到首屏制造重复数据。"""
    post_id, _ = await _seed_comments_with_ties(db)

    with pytest.raises(errors.RequestError, match='评论分页游标无效'):
        await community_service.get_comments(
            db,
            target_type='post',
            target_id=post_id,
            cursor='legacy-comment-id',
            limit=2,
        )


@pytest.mark.asyncio
async def test_comments_return_viewer_like_state(db):
    """评论点赞态按当前查看者批量回填，其他查看者不得串态。"""
    author = await seed_human(db, nickname='帖子作者')
    commenter = await seed_human(db, nickname='评论作者')
    liker = await seed_human(db, nickname='点赞者')
    post_id = await seed_post(db, author_hasn_id=author['hasn_id'])
    comment = await community_service.create_comment(
        db,
        target_type='post',
        target_id=post_id,
        user_id=commenter['user_id'],
        hasn_id=commenter['hasn_id'],
        content='值得点赞的评论',
    )

    before = await community_service.get_comments(
        db,
        target_type='post',
        target_id=post_id,
        user_id=liker['user_id'],
    )
    assert before['items'][0]['is_liked'] is False

    await community_service.create_like(
        db,
        user_id=liker['user_id'],
        hasn_id=liker['hasn_id'],
        target_type='comment',
        target_id=comment['comment_id'],
    )

    after = await community_service.get_comments(
        db,
        target_type='post',
        target_id=post_id,
        user_id=liker['user_id'],
    )
    assert after['items'][0]['is_liked'] is True
    assert after['items'][0]['like_count'] == 1

    other_view = await community_service.get_comments(
        db,
        target_type='post',
        target_id=post_id,
        user_id=author['user_id'],
    )
    assert other_view['items'][0]['is_liked'] is False
