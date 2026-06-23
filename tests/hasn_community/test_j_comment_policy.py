"""社区评论策略（default_comment_policy 默认 + comment_policy 强制）真生效回归测试。

覆盖「将真实的设置落地」之评论策略：
- 发帖未显式指定 comment_policy → 回落主人默认 default_comment_policy；显式传值则覆盖；
- closed：仅作者可评论，他人被拒；
- followers：仅作者的关注者可评论，关注后放行；作者本人恒可评论。

连真实 PG，事务回滚隔离。
"""
from __future__ import annotations

import pytest
from sqlalchemy import select

from backend.app.hasn_community.model import HasnPosts
from backend.app.hasn_community.service.community_service import community_service
from backend.app.hasn_community.service.settings_service import community_settings_service
from backend.common.exception import errors
from tests.hasn_community.conftest import seed_human


async def _policy_of(db, post_id: str) -> str:
    post = (
        await db.execute(select(HasnPosts).where(HasnPosts.post_id == post_id))
    ).scalar_one()
    return post.comment_policy


@pytest.mark.asyncio
async def test_default_comment_policy_applied_to_new_post(db):
    """未传 comment_policy → 用主人默认；显式传值则覆盖默认。"""
    owner = await seed_human(db, nickname='策略主人')

    # 主人默认设为 followers
    await community_settings_service.update_community_settings(
        db, hasn_id=owner['hasn_id'], patch={'default_comment_policy': 'followers'}
    )

    # 不传 comment_policy → 回落主人默认 followers
    r1 = await community_service.create_post(
        db, user_id=owner['user_id'], hasn_id=owner['hasn_id'], content='默认策略帖'
    )
    assert await _policy_of(db, r1['post_id']) == 'followers'

    # 显式传 all → 覆盖默认
    r2 = await community_service.create_post(
        db, user_id=owner['user_id'], hasn_id=owner['hasn_id'], content='显式策略帖', comment_policy='all'
    )
    assert await _policy_of(db, r2['post_id']) == 'all'


@pytest.mark.asyncio
async def test_closed_policy_blocks_others_but_not_author(db):
    """closed：他人评论被拒，作者本人仍可评论。"""
    author = await seed_human(db, nickname='闭评作者')
    other = await seed_human(db, nickname='路人')
    post = await community_service.create_post(
        db, user_id=author['user_id'], hasn_id=author['hasn_id'], content='已闭评的帖', comment_policy='closed'
    )
    post_id = post['post_id']

    # 作者本人可评论自己内容
    ok = await community_service.create_comment(
        db, target_type='post', target_id=post_id, hasn_id=author['hasn_id'],
        content='作者自评', user_id=author['user_id'], author_type='human', status='visible',
    )
    assert ok['comment_id']

    # 他人被拒
    with pytest.raises(errors.RequestError):
        await community_service.create_comment(
            db, target_type='post', target_id=post_id, hasn_id=other['hasn_id'],
            content='想评论', user_id=other['user_id'], author_type='human', status='visible',
        )


@pytest.mark.asyncio
async def test_followers_policy_requires_following(db):
    """followers：非关注者被拒；关注后放行。"""
    author = await seed_human(db, nickname='仅粉丝可评作者')
    fan = await seed_human(db, nickname='粉丝')
    post = await community_service.create_post(
        db, user_id=author['user_id'], hasn_id=author['hasn_id'], content='仅关注者可评', comment_policy='followers'
    )
    post_id = post['post_id']

    # 未关注 → 被拒
    with pytest.raises(errors.RequestError):
        await community_service.create_comment(
            db, target_type='post', target_id=post_id, hasn_id=fan['hasn_id'],
            content='还没关注就想评', user_id=fan['user_id'], author_type='human', status='visible',
        )

    # 关注作者后 → 放行
    await community_service.create_follow(
        db, user_id=fan['user_id'], hasn_id=fan['hasn_id'],
        target_type='human', target_hasn_id=author['hasn_id'],
    )
    ok = await community_service.create_comment(
        db, target_type='post', target_id=post_id, hasn_id=fan['hasn_id'],
        content='关注后来评论', user_id=fan['user_id'], author_type='human', status='visible',
    )
    assert ok['comment_id']
