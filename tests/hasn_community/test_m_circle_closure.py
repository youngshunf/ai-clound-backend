"""圈子核心社交闭环回归测试。

连接真实 PostgreSQL，覆盖发现投影、稳定游标、成员/内容治理通知与待审内容列表。
"""
from __future__ import annotations

from datetime import timedelta

import pytest
from sqlalchemy import text

from backend.app.hasn_community.service.circle_service import circle_service
from backend.app.hasn_community.service.community_service import community_service
from backend.app.hasn_community.service.notification_service import notification_service
from backend.utils.timezone import timezone
from tests.hasn_community.conftest import seed_article, seed_human, seed_post


async def _create_circle(
    db,
    *,
    owner: dict,
    name: str,
    join_policy: str = 'open',
    post_policy: str = 'members',
) -> dict:
    return await circle_service.create_circle(
        db,
        owner_hasn_id=owner['hasn_id'],
        owner_user_id=owner['user_id'],
        name=name,
        join_policy=join_policy,
        post_policy=post_policy,
    )


@pytest.mark.asyncio
async def test_discover_exposes_real_viewer_and_activity_projections(db):
    """发现页必须返回真实关系态、七日活跃、成员头像和管理者待审数。"""
    owner = await seed_human(db, nickname='圈主')
    applicant = await seed_human(db, nickname='申请人')
    circle = await _create_circle(
        db,
        owner=owner,
        name='审批投影圈',
        join_policy='approval',
    )
    await circle_service.join_circle(
        db,
        ident=circle['circle_id'],
        member_hasn_id=applicant['hasn_id'],
        member_type='human',
        owner_hasn_id=applicant['hasn_id'],
    )

    post_id = await seed_post(
        db,
        author_hasn_id=owner['hasn_id'],
        content='圈内真实动态',
        published_time=timezone.now() - timedelta(hours=2),
    )
    await db.execute(
        text(
            'UPDATE hasn_community.hasn_posts '
            'SET circle_id = :circle_id WHERE post_id = :post_id'
        ),
        {'circle_id': circle['circle_id'], 'post_id': post_id},
    )
    await db.flush()

    applicant_view = await circle_service.discover(
        db,
        viewer_hasn_id=applicant['hasn_id'],
        join_policy='approval',
        sort='active',
        limit=10,
    )
    item = applicant_view['items'][0]
    assert item['circle_id'] == circle['circle_id']
    assert item['my_role'] == 'member'
    assert item['my_status'] == 'pending'
    assert item['recent_count'] == 1
    assert item['last_active_time'] is not None
    assert len(item['activity_7d']) == 7
    assert sum(day['count'] for day in item['activity_7d']) == 1
    assert item['top_members'][0]['display_name'] == owner['nickname']
    assert item['pending_count'] is None

    owner_view = await circle_service.discover(
        db,
        viewer_hasn_id=owner['hasn_id'],
        join_policy='approval',
        sort='members',
        limit=10,
    )
    assert owner_view['items'][0]['pending_count'] == 1


@pytest.mark.asyncio
async def test_circle_members_and_mine_use_lossless_keyset_pagination(db):
    """成员列表和我的圈子在相同时间写入时也不能重复或漏项。"""
    owner = await seed_human(db, nickname='分页圈主')
    members = [await seed_human(db, nickname=f'分页成员{i}') for i in range(3)]
    circles = [
        await _create_circle(db, owner=owner, name=f'我的分页圈{i}')
        for i in range(3)
    ]
    for member in members:
        await circle_service.join_circle(
            db,
            ident=circles[0]['circle_id'],
            member_hasn_id=member['hasn_id'],
            member_type='human',
            owner_hasn_id=member['hasn_id'],
        )

    member_ids: list[str] = []
    cursor = None
    while True:
        page = await circle_service.list_members(
            db,
            ident=circles[0]['circle_id'],
            status='active',
            cursor=cursor,
            limit=2,
        )
        member_ids.extend(item['member_hasn_id'] for item in page['items'])
        cursor = page['next_cursor']
        if cursor is None:
            break
    assert len(member_ids) == 4
    assert len(set(member_ids)) == 4

    circle_ids: list[str] = []
    cursor = None
    while True:
        page = await circle_service.list_mine(
            db,
            member_hasn_id=owner['hasn_id'],
            cursor=cursor,
            limit=2,
        )
        circle_ids.extend(item['circle_id'] for item in page['items'])
        cursor = page['next_cursor']
        if cursor is None:
            break
    assert set(circle_ids) == {item['circle_id'] for item in circles}
    assert len(circle_ids) == len(set(circle_ids))


@pytest.mark.asyncio
async def test_circle_feed_cursor_is_stable_across_posts_and_articles(db):
    """帖子和文章发布时间相同时，跨类型游标仍按三元组稳定翻页。"""
    owner = await seed_human(db, nickname='内容圈主')
    circle = await _create_circle(db, owner=owner, name='稳定内容流圈')
    published_time = timezone.now() - timedelta(minutes=5)
    post_ids = [
        await seed_post(
            db,
            author_hasn_id=owner['hasn_id'],
            content=f'同刻帖子{i}',
            published_time=published_time,
        )
        for i in range(2)
    ]
    article_ids = [
        await seed_article(
            db,
            author_hasn_id=owner['hasn_id'],
            title=f'同刻文章{i}',
            published_time=published_time,
        )
        for i in range(2)
    ]
    await db.execute(
        text(
            'UPDATE hasn_community.hasn_posts SET circle_id = :circle_id '
            'WHERE post_id = ANY(:post_ids)'
        ),
        {'circle_id': circle['circle_id'], 'post_ids': post_ids},
    )
    await db.execute(
        text(
            'UPDATE hasn_community.hasn_articles SET circle_id = :circle_id '
            'WHERE article_id = ANY(:article_ids)'
        ),
        {'circle_id': circle['circle_id'], 'article_ids': article_ids},
    )
    await db.flush()

    seen: list[tuple[str, str]] = []
    cursor = None
    while True:
        page = await circle_service.get_circle_feed(
            db,
            circle['circle_id'],
            cursor=cursor,
            limit=1,
            viewer_hasn_id=owner['hasn_id'],
        )
        assert len(page['items']) <= 1
        if page['items']:
            item = page['items'][0]
            content_type = 'article' if item.get('article_id') else 'post'
            content_id = item.get('article_id') or item['post_id']
            seen.append((content_type, content_id))
        cursor = page['next_cursor']
        if cursor is None:
            break
    assert set(seen) == {
        *(('post', post_id) for post_id in post_ids),
        *(('article', article_id) for article_id in article_ids),
    }
    assert len(seen) == len(set(seen)) == 4


@pytest.mark.asyncio
async def test_circle_governance_emits_application_invite_and_review_notifications(db):
    """入圈申请、审批结果、邀请和内容审批都必须落统一通知。"""
    owner = await seed_human(db, nickname='治理圈主')
    applicant = await seed_human(db, nickname='治理申请人')
    invitee = await seed_human(db, nickname='受邀者')
    circle = await _create_circle(
        db,
        owner=owner,
        name='通知闭环圈',
        join_policy='approval',
        post_policy='approval',
    )

    await circle_service.join_circle(
        db,
        ident=circle['circle_id'],
        member_hasn_id=applicant['hasn_id'],
        member_type='human',
        owner_hasn_id=applicant['hasn_id'],
    )
    owner_notes = await notification_service.list_notifications(
        db,
        recipient_hasn_id=owner['hasn_id'],
    )
    assert owner_notes['items'][0]['type'] == 'circle_join_pending'
    assert owner_notes['items'][0]['target'] == {
        'type': 'circle',
        'id': circle['circle_id'],
    }

    await circle_service.moderate_member(
        db,
        ident=circle['circle_id'],
        target_hasn_id=applicant['hasn_id'],
        actor_hasn_id=owner['hasn_id'],
        action='approve',
    )
    applicant_notes = await notification_service.list_notifications(
        db,
        recipient_hasn_id=applicant['hasn_id'],
    )
    assert applicant_notes['items'][0]['type'] == 'circle_join_approved'

    await circle_service.invite(
        db,
        ident=circle['circle_id'],
        actor_hasn_id=owner['hasn_id'],
        invitee_hasn_id=invitee['hasn_id'],
    )
    invitee_notes = await notification_service.list_notifications(
        db,
        recipient_hasn_id=invitee['hasn_id'],
    )
    assert invitee_notes['items'][0]['type'] == 'circle_invited'

    post = await community_service.create_post(
        db,
        user_id=applicant['user_id'],
        hasn_id=applicant['hasn_id'],
        content='等待圈子审核的帖子',
        circle_id=circle['circle_id'],
    )
    assert post['status'] == 'pending_review'
    pending = await circle_service.list_pending_content(
        db,
        ident=circle['circle_id'],
        actor_hasn_id=owner['hasn_id'],
        limit=10,
    )
    assert pending['items'][0]['post_id'] == post['post_id']

    await circle_service.moderate_content(
        db,
        ident=circle['circle_id'],
        content_type='post',
        content_id=post['post_id'],
        actor_hasn_id=owner['hasn_id'],
        action='approve',
    )
    applicant_notes = await notification_service.list_notifications(
        db,
        recipient_hasn_id=applicant['hasn_id'],
    )
    assert applicant_notes['items'][0]['type'] == 'circle_content_approved'
    detail = await circle_service.get_circle(
        db,
        circle['circle_id'],
        viewer_hasn_id=owner['hasn_id'],
    )
    assert detail['content_count'] == 1
