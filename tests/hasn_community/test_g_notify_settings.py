"""社区通知开关（community_settings.notify.*）真生效回归测试。

覆盖「将真实的设置落地」之「通知开关真生效」：
- get_notify_enabled helper：默认 True / 关闭后 False / 非 human 收件人 True / 未知 True；
- _emit 把关：收件人关闭对应互动通知 → 不落库（list_notifications 取不到）；
  开启（默认）→ 正常落库；草稿待审核（不在通知矩阵）不受开关约束恒发。

连真实 PG（含共享表 hasn_notifications），事务回滚隔离。
"""
from __future__ import annotations

import pytest

from backend.app.hasn_community.service.notification_service import notification_service
from backend.app.hasn_community.service.settings_service import community_settings_service
from tests.hasn_community.conftest import seed_human


@pytest.mark.asyncio
async def test_get_notify_enabled_states(db):
    """默认 True；关闭某键后该键 False、其余仍 True；非 human / 未知收件人恒 True。"""
    human = await seed_human(db, nickname='收件人')
    hid = human['hasn_id']

    # 默认全开
    assert await community_settings_service.get_notify_enabled(db, recipient_hasn_id=hid, notify_key='like') is True
    assert await community_settings_service.get_notify_enabled(db, recipient_hasn_id=hid, notify_key='follow') is True

    # 关闭 like，follow 不受影响
    await community_settings_service.update_community_settings(
        db, hasn_id=hid, patch={'notify': {'like': False}}
    )
    assert await community_settings_service.get_notify_enabled(db, recipient_hasn_id=hid, notify_key='like') is False
    assert await community_settings_service.get_notify_enabled(db, recipient_hasn_id=hid, notify_key='follow') is True

    # 非 human / 未知收件人：无偏好，不抑制
    assert await community_settings_service.get_notify_enabled(
        db, recipient_hasn_id='h_unknown_xyz', notify_key='like'
    ) is True


@pytest.mark.asyncio
async def test_like_notification_suppressed_when_disabled(db):
    """收件人关闭点赞通知 → 点赞不落库；开启（默认）→ 落库。"""
    author = await seed_human(db, nickname='作者')
    actor = await seed_human(db, nickname='点赞者')

    async def emit_like() -> None:
        await notification_service.notify_content_interaction(
            db,
            ntype='community_like',
            actor_hasn_id=actor['hasn_id'],
            content_type='post',
            content_id='p_notify_test',
            author_hasn_id=author['hasn_id'],
            author_type='human',
            owner_hasn_id=None,
        )

    # 关闭点赞通知 → 不落库
    await community_settings_service.update_community_settings(
        db, hasn_id=author['hasn_id'], patch={'notify': {'like': False}}
    )
    await emit_like()
    listing = await notification_service.list_notifications(db, recipient_hasn_id=author['hasn_id'])
    assert listing['items'] == []

    # 重新开启 → 落库
    await community_settings_service.update_community_settings(
        db, hasn_id=author['hasn_id'], patch={'notify': {'like': True}}
    )
    await emit_like()
    listing2 = await notification_service.list_notifications(db, recipient_hasn_id=author['hasn_id'])
    assert len(listing2['items']) == 1
    assert listing2['items'][0]['type'] == 'community_like'


@pytest.mark.asyncio
async def test_draft_pending_not_gated_by_notify(db):
    """草稿待主人审核不在通知矩阵 → 即便关掉所有互动通知也照常送达主人。"""
    owner = await seed_human(db, nickname='主人')
    await community_settings_service.update_community_settings(
        db,
        hasn_id=owner['hasn_id'],
        patch={'notify': {'like': False, 'comment': False, 'follow': False, 'collect': False}},
    )

    await notification_service.notify_draft_pending(
        db,
        owner_hasn_id=owner['hasn_id'],
        agent_hasn_id='a_some_agent',
        content_type='post',
        content_id='p_draft_test',
        preview='待审核内容',
    )
    listing = await notification_service.list_notifications(db, recipient_hasn_id=owner['hasn_id'])
    assert len(listing['items']) == 1
    assert listing['items'][0]['type'] == 'community_draft_pending'
