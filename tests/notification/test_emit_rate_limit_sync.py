"""统一通知 P2-余：emit() 限频（防轰炸）+ notification.created sync_event（§5/§9）。

连真库，事务回滚隔离（零 Mock 零 Fake）。
"""
from __future__ import annotations

import pytest
from sqlalchemy import select, text

from backend.app.hasn.model.hasn_notifications import HasnNotifications
from backend.app.notification.service.notification_service import (
    NotificationService,
    notification_service,
)
from tests.notification.conftest import seed_human


async def _sync_event_count(db, owner_id: str) -> int:
    return (
        await db.execute(
            text(
                "SELECT count(*) FROM hasn_sync_events "
                "WHERE owner_id = :o AND event_type = 'notification.created'"
            ),
            {'o': owner_id},
        )
    ).scalar()


@pytest.mark.asyncio
async def test_emit_writes_sync_event_when_toast(db) -> None:
    """reminder 默认承载含 toast → 写 notification.created sync_event（供 daemon 投 toast）。"""
    owner = await seed_human(db, nickname='主人')
    await notification_service.emit(
        db,
        recipient_id=owner['hasn_id'],
        source={'kind': 'system', 'id': 'reminder-svc', 'display_name': '提醒'},
        category='reminder',
        type='event_reminder',
        title='日程提醒',
        payload={'target': {'type': 'event', 'id': 'e1'}},
    )
    assert await _sync_event_count(db, owner['hasn_id']) == 1


@pytest.mark.asyncio
async def test_emit_no_sync_event_when_no_toast(db) -> None:
    """social 默认承载是 center+badge（无 toast）→ 不写 sync_event。"""
    owner = await seed_human(db, nickname='主人')
    await notification_service.emit(
        db,
        recipient_id=owner['hasn_id'],
        source={'kind': 'user', 'id': 'u1', 'display_name': '甲'},
        category='social',
        type='community_like',
        title='甲赞了你的帖子',
        payload={'target': {'type': 'post', 'id': 'p1'}},
    )
    assert await _sync_event_count(db, owner['hasn_id']) == 0


@pytest.mark.asyncio
async def test_rate_limit_suppresses_noisy_carriers_but_keeps_center(db, monkeypatch) -> None:
    """同一 (recipient, app源) 近窗超阈值 → 压制 toast/push/card，但 center 权威行仍落（D1）。"""
    monkeypatch.setattr(NotificationService, 'RATE_MAX_PER_WINDOW', 3)
    owner = await seed_human(db, nickname='主人')
    ids: list[int] = []
    for i in range(4):
        nid = await notification_service.emit(
            db,
            recipient_id=owner['hasn_id'],
            source={'kind': 'app', 'id': 'app-1', 'display_name': '某应用'},
            category='reminder',
            type='event_reminder',
            title=f'提醒{i}',
            payload={'target': {'type': 'event', 'id': f'e{i}'}},
        )
        ids.append(nid)

    # 4 条 center 权威行都落（不漏事）
    notes = await notification_service.list_notifications(db, recipient_hasn_id=owner['hasn_id'])
    assert len(notes['items']) == 4

    # 第 4 条（已达阈值）被限频：noisy 承载关闭，center 仍在
    last = (
        await db.execute(select(HasnNotifications).where(HasnNotifications.id == ids[3]))
    ).scalar_one()
    assert last.delivery.get('rate_limited') is True
    assert last.delivery['channels']['toast'] is False
    assert last.delivery['channels']['push'] is False
    assert last.delivery['channels']['center'] is True

    # 第 1 条未限频：toast 仍开（窗口内尚未超阈值）
    first = (
        await db.execute(select(HasnNotifications).where(HasnNotifications.id == ids[0]))
    ).scalar_one()
    assert not first.delivery.get('rate_limited')
    assert first.delivery['channels']['toast'] is True

    # 被限频的第 4 条不写 toast sync_event；前 3 条写 → 共 3 个 sync_event
    assert await _sync_event_count(db, owner['hasn_id']) == 3


@pytest.mark.asyncio
async def test_rate_limit_skips_critical(db, monkeypatch) -> None:
    """critical（安全/系统告警）不限频——必达。"""
    monkeypatch.setattr(NotificationService, 'RATE_MAX_PER_WINDOW', 1)
    owner = await seed_human(db, nickname='主人')
    ids: list[int] = []
    for i in range(3):
        nid = await notification_service.emit(
            db,
            recipient_id=owner['hasn_id'],
            source={'kind': 'app', 'id': 'app-x', 'display_name': '某应用'},
            category='system',
            type='security',
            title=f'安全告警{i}',
            priority='critical',
            payload={'target': {'type': 'sec', 'id': f's{i}'}},
        )
        ids.append(nid)

    last = (
        await db.execute(select(HasnNotifications).where(HasnNotifications.id == ids[2]))
    ).scalar_one()
    assert not last.delivery.get('rate_limited')
    assert last.delivery['channels']['toast'] is True
