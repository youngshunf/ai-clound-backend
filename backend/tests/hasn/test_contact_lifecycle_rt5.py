"""doc08 RT5 云端：关系生命周期补全（D4 删除好友 + B7 请求/关系过期）。

不依赖真实 PG：
1. 删除联系人：patch DAO + AsyncMock db，验证 remove_contact 三步（双向删边 / 会话标不可达 /
   中性通知）都被正确接线；再驱动 delete_contact 端点验证 404 / 403 / 授权放行。
2. 过期兜底：mock db.execute 喂混合（超期 + 未超期）行，断言只有超期的被置 expired / archived。
"""
from __future__ import annotations

from datetime import timedelta
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from fastapi import HTTPException

from backend.app.hasn.api.v1.app import contacts as contacts_api
from backend.app.hasn.service import hasn_contacts_service as svc
from backend.app.hasn.service.hasn_contacts_service import (
    CONTACT_REQUEST_EXPIRE_DAYS,
    HasnContactsService,
)
from backend.app.hasn_im.application.provider import get_relation_gateway
from backend.utils.timezone import timezone


# ── 一、删除联系人 remove_contact（D4·修 B5） ──────────────


@pytest.mark.asyncio
async def test_remove_contact_human_peer_bidirectional_and_notify() -> None:
    """human peer：双向删边 + 会话标不可达 + 通知对方本人（peer_owner_id 为空回落 peer_id）。"""
    contact: Any = SimpleNamespace(peer_id='h_peer', peer_owner_id=None, relation_type='social')
    db = AsyncMock()
    with (
        patch.object(svc.hasn_contacts_dao, 'delete_relation_bidirectional', AsyncMock(return_value=2)) as del_mock,
        patch.object(svc.hasn_conversations_dao, 'mark_direct_unreachable', AsyncMock(return_value=1)) as mark_mock,
        patch.object(HasnContactsService, '_push_relation_removed', AsyncMock()) as push_mock,
    ):
        result = await HasnContactsService.remove_contact(db, owner_id='h_owner', contact=contact)

    del_mock.assert_awaited_once_with(db, 'h_owner', 'h_peer', 'social')
    mark_mock.assert_awaited_once_with(db, 'h_owner', 'h_peer')
    # 通知对方本人（human peer 无 peer_owner_id → 回落 peer_id）
    push_mock.assert_awaited_once_with('h_peer', 'h_owner')
    db.commit.assert_awaited_once()
    assert result == {
        'deleted_edges': 2,
        'conversations_marked': 1,
        'peer_id': 'h_peer',
        'notified': True,
    }


@pytest.mark.asyncio
async def test_remove_contact_agent_peer_notifies_owner() -> None:
    """agent peer：通知对方分身的**主人**（peer_owner_id），不通知分身本体。"""
    contact: Any = SimpleNamespace(peer_id='a_agent', peer_owner_id='h_master', relation_type='social')
    db = AsyncMock()
    with (
        patch.object(svc.hasn_contacts_dao, 'delete_relation_bidirectional', AsyncMock(return_value=1)),
        patch.object(svc.hasn_conversations_dao, 'mark_direct_unreachable', AsyncMock(return_value=1)),
        patch.object(HasnContactsService, '_push_relation_removed', AsyncMock()) as push_mock,
    ):
        result = await HasnContactsService.remove_contact(db, owner_id='h_owner', contact=contact)

    push_mock.assert_awaited_once_with('h_master', 'h_owner')
    assert result['notified'] is True


@pytest.mark.asyncio
async def test_remove_contact_self_peer_not_notified() -> None:
    """peer 归属人就是自己（notify_target==owner）→ 不发通知（不给自己推关系解除）。"""
    contact: Any = SimpleNamespace(peer_id='a_my_agent', peer_owner_id='h_owner', relation_type='social')
    db = AsyncMock()
    with (
        patch.object(svc.hasn_contacts_dao, 'delete_relation_bidirectional', AsyncMock(return_value=1)),
        patch.object(svc.hasn_conversations_dao, 'mark_direct_unreachable', AsyncMock(return_value=0)),
        patch.object(HasnContactsService, '_push_relation_removed', AsyncMock()) as push_mock,
    ):
        result = await HasnContactsService.remove_contact(db, owner_id='h_owner', contact=contact)

    push_mock.assert_not_awaited()
    assert result['notified'] is False


# ── 二、delete_contact 端点授权（404 / 403 / 放行） ─────────


@pytest.mark.asyncio
async def test_delete_contact_endpoint_404_when_missing() -> None:
    db = AsyncMock()
    with patch.object(contacts_api.hasn_contacts_dao, 'get', AsyncMock(return_value=None)):
        with pytest.raises(HTTPException) as exc:
            await contacts_api.delete_contact(
                contact_id=1,
                db=db,
                auth={'hasn_id': 'h_owner'},
                relation_gateway=get_relation_gateway(),
            )
    assert exc.value.status_code == 404


@pytest.mark.asyncio
async def test_delete_contact_endpoint_403_when_not_owner() -> None:
    contact = SimpleNamespace(id=1, owner_id='h_someone_else', peer_id='h_peer',
                              peer_owner_id=None, relation_type='social')
    db = AsyncMock()
    with patch.object(contacts_api.hasn_contacts_dao, 'get', AsyncMock(return_value=contact)):
        with pytest.raises(HTTPException) as exc:
            await contacts_api.delete_contact(
                contact_id=1,
                db=db,
                auth={'hasn_id': 'h_owner'},
                relation_gateway=get_relation_gateway(),
            )
    assert exc.value.status_code == 403


# ── 三、请求过期 sweep（B7·只过期真正超期的） ─────────────


def _fake_execute_result(rows: list) -> MagicMock:
    """构造一个 db.execute 返回值：.scalars().all() == rows。"""
    result = MagicMock()
    result.scalars.return_value.all.return_value = rows
    return result


@pytest.mark.asyncio
async def test_sweep_expired_contact_requests_only_overdue() -> None:
    now = timezone.now()
    overdue = SimpleNamespace(
        status='pending', created_time=now - timedelta(days=CONTACT_REQUEST_EXPIRE_DAYS + 5), decided_at=None,
    )
    fresh = SimpleNamespace(
        status='pending', created_time=now - timedelta(days=5), decided_at=None,
    )
    db = AsyncMock()
    db.execute = AsyncMock(return_value=_fake_execute_result([overdue, fresh]))

    count = await HasnContactsService.sweep_expired_contact_requests(db)

    assert count == 1
    assert overdue.status == 'expired'
    assert overdue.decided_at is not None  # 回填过期时间（审计）
    assert fresh.status == 'pending'  # 未超期不动
    db.flush.assert_awaited_once()


@pytest.mark.asyncio
async def test_sweep_expired_contact_requests_none_overdue_no_flush() -> None:
    now = timezone.now()
    fresh = SimpleNamespace(status='pending', created_time=now - timedelta(days=1), decided_at=None)
    db = AsyncMock()
    db.execute = AsyncMock(return_value=_fake_execute_result([fresh]))

    count = await HasnContactsService.sweep_expired_contact_requests(db)

    assert count == 0
    assert fresh.status == 'pending'
    db.flush.assert_not_awaited()  # 无变更不 flush


# ── 四、联系人 auto_expire sweep（B7·到期自动断） ──────────


@pytest.mark.asyncio
async def test_sweep_expired_auto_expire_contacts_only_past_due() -> None:
    now = timezone.now()
    past_due = SimpleNamespace(status='connected', auto_expire=now - timedelta(days=1))
    future = SimpleNamespace(status='connected', auto_expire=now + timedelta(days=10))
    db = AsyncMock()
    db.execute = AsyncMock(return_value=_fake_execute_result([past_due, future]))

    count = await HasnContactsService.sweep_expired_auto_expire_contacts(db)

    assert count == 1
    assert past_due.status == 'archived'   # 到期自动断
    assert future.status == 'connected'    # 未到期不动
    db.flush.assert_awaited_once()


@pytest.mark.asyncio
async def test_sweep_expired_relation_lifecycle_aggregates_and_commits() -> None:
    """总兜底：聚合两类过期数并在有变更时提交一次。"""
    db = AsyncMock()
    with (
        patch.object(HasnContactsService, 'sweep_expired_contact_requests', AsyncMock(return_value=3)),
        patch.object(HasnContactsService, 'sweep_expired_auto_expire_contacts', AsyncMock(return_value=2)),
    ):
        result = await HasnContactsService.sweep_expired_relation_lifecycle(db)

    assert result == {'requests_expired': 3, 'contacts_expired': 2}
    db.commit.assert_awaited_once()
