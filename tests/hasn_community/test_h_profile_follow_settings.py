"""社区边界设置真生效（show_profile / allow_follow）回归测试。

覆盖「将真实的设置落地」之主页可见性与被关注边界：
- get_profile：show_profile=False 后非本人查看抛 NotFoundError，本人仍可看，默认可看；
- create_follow：目标 human 关闭 allow_follow 后新增关注抛 RequestError，默认可关注，
  且关闭后「取消关注」仍可执行（不锁死已关注者）。

连真实 PG，事务回滚隔离。
"""
from __future__ import annotations

import pytest

from backend.app.hasn_community.service.community_service import community_service
from backend.app.hasn_community.service.settings_service import community_settings_service
from backend.common.exception import errors
from tests.hasn_community.conftest import seed_human


@pytest.mark.asyncio
async def test_show_profile_hides_from_others_but_not_self(db):
    owner = await seed_human(db, nickname='主页主人')
    viewer = await seed_human(db, nickname='访客')

    # 默认（未设置）→ 任何人可看
    default_view = await community_service.get_profile(
        db, hasn_id=owner['hasn_id'], viewer_user_id=viewer['user_id']
    )
    assert default_view['hasn_id'] == owner['hasn_id']

    # 关闭公开主页
    await community_settings_service.update_community_settings(
        db, hasn_id=owner['hasn_id'], patch={'show_profile': False}
    )

    # 非本人查看 → 不可见
    with pytest.raises(errors.NotFoundError):
        await community_service.get_profile(
            db, hasn_id=owner['hasn_id'], viewer_user_id=viewer['user_id']
        )

    # 本人查看 → 仍可见
    self_view = await community_service.get_profile(
        db, hasn_id=owner['hasn_id'], viewer_user_id=owner['user_id']
    )
    assert self_view['is_self'] is True


@pytest.mark.asyncio
async def test_allow_follow_gate(db):
    target = await seed_human(db, nickname='被关注者')
    follower = await seed_human(db, nickname='关注者')

    # 默认（未设置）→ 可关注
    await community_service.create_follow(
        db,
        user_id=follower['user_id'],
        hasn_id=follower['hasn_id'],
        target_type='human',
        target_hasn_id=target['hasn_id'],
    )

    # 关闭「允许被关注」
    await community_settings_service.update_community_settings(
        db, hasn_id=target['hasn_id'], patch={'allow_follow': False}
    )

    # 已关注者「取消关注」仍可执行（不锁死）
    await community_service.delete_follow(
        db,
        user_id=follower['user_id'],
        hasn_id=follower['hasn_id'],
        target_type='human',
        target_hasn_id=target['hasn_id'],
    )

    # 取关后再想关注 → 被拒
    with pytest.raises(errors.RequestError):
        await community_service.create_follow(
            db,
            user_id=follower['user_id'],
            hasn_id=follower['hasn_id'],
            target_type='human',
            target_hasn_id=target['hasn_id'],
        )
