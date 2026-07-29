"""
C-backend 回归：通知触发矩阵 + 列表/已读/未读数 + Agent relay。
doc-13 §2.1 / §3.1。连真实 PG，事务回滚隔离。
"""
from __future__ import annotations

import pytest

from backend.app.hasn_community.service.community_service import community_service
from backend.app.hasn_community.service.notification_service import notification_service
from tests.hasn_community.conftest import seed_agent, seed_human, seed_post


@pytest.mark.asyncio
async def test_like_notifies_author(db):
    author = await seed_human(db, nickname='作者')
    liker = await seed_human(db, nickname='点赞者')
    pid = await seed_post(db, author_hasn_id=author['hasn_id'], content='被赞的帖子')

    await community_service.create_like(
        db, user_id=liker['user_id'], hasn_id=liker['hasn_id'], target_type='post', target_id=pid
    )

    notes = await notification_service.list_notifications(db, recipient_hasn_id=author['hasn_id'])
    assert len(notes['items']) == 1
    n = notes['items'][0]
    assert n['type'] == 'community_like'
    assert n['actor']['display_name'] == liker['nickname']
    assert n['target'] == {'type': 'post', 'id': pid}
    assert n['read'] is False


@pytest.mark.asyncio
async def test_self_like_no_notification(db):
    author = await seed_human(db, nickname='作者')
    pid = await seed_post(db, author_hasn_id=author['hasn_id'])

    await community_service.create_like(
        db, user_id=author['user_id'], hasn_id=author['hasn_id'], target_type='post', target_id=pid
    )
    notes = await notification_service.list_notifications(db, recipient_hasn_id=author['hasn_id'])
    assert len(notes['items']) == 0  # 自赞不通知


@pytest.mark.asyncio
async def test_like_agent_content_relays_to_owner(db):
    owner = await seed_human(db, nickname='主人')
    agent = await seed_agent(db, owner_hasn_id=owner['hasn_id'], display_name='星二哥')
    liker = await seed_human(db, nickname='路人')
    # Agent 作者的帖子
    pid = await seed_post(
        db, author_hasn_id=agent['hasn_id'], author_type='agent',
        owner_hasn_id=owner['hasn_id'], content='Agent 的帖子',
    )

    await community_service.create_like(
        db, user_id=liker['user_id'], hasn_id=liker['hasn_id'], target_type='post', target_id=pid
    )

    # Agent 收到一条
    agent_notes = await notification_service.list_notifications(db, recipient_hasn_id=agent['hasn_id'])
    assert len(agent_notes['items']) == 1
    # 主人收到 relay 一条
    owner_notes = await notification_service.list_notifications(db, recipient_hasn_id=owner['hasn_id'])
    assert len(owner_notes['items']) == 1
    assert owner_notes['items'][0]['relay_from'] == agent['hasn_id']


@pytest.mark.asyncio
async def test_comment_like_notifies_comment_author_and_self_like_is_suppressed(db):
    post_author = await seed_human(db, nickname='帖子作者')
    commenter = await seed_human(db, nickname='评论作者')
    liker = await seed_human(db, nickname='评论点赞者')
    pid = await seed_post(db, author_hasn_id=post_author['hasn_id'])
    comment = await community_service.create_comment(
        db,
        target_type='post',
        target_id=pid,
        user_id=commenter['user_id'],
        hasn_id=commenter['hasn_id'],
        content='被赞的评论',
    )

    await community_service.create_like(
        db,
        user_id=liker['user_id'],
        hasn_id=liker['hasn_id'],
        target_type='comment',
        target_id=comment['comment_id'],
    )

    notes = await notification_service.list_notifications(
        db,
        recipient_hasn_id=commenter['hasn_id'],
    )
    assert len(notes['items']) == 1
    assert notes['items'][0]['target'] == {
        'type': 'comment',
        'id': comment['comment_id'],
    }
    assert notes['items'][0]['link'] == f'/community/posts/{pid}#comment-{comment["comment_id"]}'

    own_comment = await community_service.create_comment(
        db,
        target_type='post',
        target_id=pid,
        user_id=commenter['user_id'],
        hasn_id=commenter['hasn_id'],
        content='自赞评论',
    )
    await community_service.create_like(
        db,
        user_id=commenter['user_id'],
        hasn_id=commenter['hasn_id'],
        target_type='comment',
        target_id=own_comment['comment_id'],
    )
    after_self_like = await notification_service.list_notifications(
        db,
        recipient_hasn_id=commenter['hasn_id'],
    )
    assert len(after_self_like['items']) == 1


@pytest.mark.asyncio
async def test_comment_like_on_agent_comment_relays_to_owner(db):
    owner = await seed_human(db, nickname='分身主人')
    agent = await seed_agent(db, owner_hasn_id=owner['hasn_id'], display_name='评论分身')
    post_author = await seed_human(db, nickname='帖子作者')
    liker = await seed_human(db, nickname='评论点赞者')
    pid = await seed_post(db, author_hasn_id=post_author['hasn_id'])
    comment = await community_service.create_comment(
        db,
        target_type='post',
        target_id=pid,
        hasn_id=agent['hasn_id'],
        author_type='agent',
        owner_hasn_id=owner['hasn_id'],
        user_id=None,
        content='分身评论',
        status='visible',
    )

    await community_service.create_like(
        db,
        user_id=liker['user_id'],
        hasn_id=liker['hasn_id'],
        target_type='comment',
        target_id=comment['comment_id'],
    )

    agent_notes = await notification_service.list_notifications(
        db,
        recipient_hasn_id=agent['hasn_id'],
    )
    owner_notes = await notification_service.list_notifications(
        db,
        recipient_hasn_id=owner['hasn_id'],
    )
    assert len(agent_notes['items']) == 1
    assert len(owner_notes['items']) == 1
    assert owner_notes['items'][0]['relay_from'] == agent['hasn_id']
    assert owner_notes['items'][0]['target'] == {
        'type': 'comment',
        'id': comment['comment_id'],
    }
    assert owner_notes['items'][0]['link'] == (
        f'/community/posts/{pid}#comment-{comment["comment_id"]}'
    )


@pytest.mark.asyncio
async def test_follow_notifies_target(db):
    target = await seed_human(db, nickname='被关注者')
    follower = await seed_human(db, nickname='关注者')

    await community_service.create_follow(
        db, user_id=follower['user_id'], hasn_id=follower['hasn_id'],
        target_type='human', target_hasn_id=target['hasn_id'],
    )
    notes = await notification_service.list_notifications(db, recipient_hasn_id=target['hasn_id'])
    assert len(notes['items']) == 1
    assert notes['items'][0]['type'] == 'community_follow'


@pytest.mark.asyncio
async def test_comment_notifies_author_and_parent(db):
    author = await seed_human(db, nickname='帖子作者')
    commenter1 = await seed_human(db, nickname='评论者甲')
    commenter2 = await seed_human(db, nickname='评论者乙')
    pid = await seed_post(db, author_hasn_id=author['hasn_id'], content='讨论帖')

    # 甲评论
    c1 = await community_service.create_comment(
        db, target_type='post', target_id=pid, user_id=commenter1['user_id'],
        hasn_id=commenter1['hasn_id'], content='甲的评论',
    )
    # 乙回复甲
    await community_service.create_comment(
        db, target_type='post', target_id=pid, user_id=commenter2['user_id'],
        hasn_id=commenter2['hasn_id'], content='乙回复甲', parent_id=c1['comment_id'],
    )

    # 作者收到两条（甲评论 + 乙评论），甲收到一条（被回复）
    author_notes = await notification_service.list_notifications(db, recipient_hasn_id=author['hasn_id'])
    assert len(author_notes['items']) >= 1
    c1_notes = await notification_service.list_notifications(db, recipient_hasn_id=commenter1['hasn_id'])
    assert any(n['type'] == 'community_comment' for n in c1_notes['items'])


@pytest.mark.asyncio
async def test_unread_count_and_mark_read(db):
    author = await seed_human(db, nickname='作者')
    liker = await seed_human(db, nickname='点赞者')
    pid = await seed_post(db, author_hasn_id=author['hasn_id'])

    await community_service.create_like(
        db, user_id=liker['user_id'], hasn_id=liker['hasn_id'], target_type='post', target_id=pid
    )

    uc = await notification_service.unread_count(db, recipient_hasn_id=author['hasn_id'])
    assert uc['total'] == 1
    assert uc['by_type'].get('community_like') == 1

    notes = await notification_service.list_notifications(db, recipient_hasn_id=author['hasn_id'])
    nid = notes['items'][0]['id']
    await notification_service.mark_read(db, recipient_hasn_id=author['hasn_id'], notification_id=nid)

    uc2 = await notification_service.unread_count(db, recipient_hasn_id=author['hasn_id'])
    assert uc2['total'] == 0


@pytest.mark.asyncio
async def test_mark_all_read(db):
    author = await seed_human(db, nickname='作者')
    for i in range(3):
        liker = await seed_human(db, nickname=f'点赞者{i}')
        pid = await seed_post(db, author_hasn_id=author['hasn_id'], content=f'帖子{i}')
        await community_service.create_like(
            db, user_id=liker['user_id'], hasn_id=liker['hasn_id'], target_type='post', target_id=pid
        )

    affected = await notification_service.mark_all_read(db, recipient_hasn_id=author['hasn_id'])
    assert affected == 3
    uc = await notification_service.unread_count(db, recipient_hasn_id=author['hasn_id'])
    assert uc['total'] == 0


@pytest.mark.asyncio
async def test_mark_read_others_forbidden(db):
    author = await seed_human(db, nickname='作者')
    liker = await seed_human(db, nickname='点赞者')
    other = await seed_human(db, nickname='他人')
    pid = await seed_post(db, author_hasn_id=author['hasn_id'])
    await community_service.create_like(
        db, user_id=liker['user_id'], hasn_id=liker['hasn_id'], target_type='post', target_id=pid
    )
    notes = await notification_service.list_notifications(db, recipient_hasn_id=author['hasn_id'])
    nid = notes['items'][0]['id']

    from backend.common.exception import errors

    with pytest.raises(errors.NotFoundError):
        await notification_service.mark_read(db, recipient_hasn_id=other['hasn_id'], notification_id=nid)
