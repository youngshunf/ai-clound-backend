from __future__ import annotations

import asyncio
import uuid

from datetime import timedelta

import pytest
from sqlalchemy import text

from backend.app.hasn.service.owner_storage_service import OwnerStorageService
from backend.common.exception import errors
from backend.database.db import async_db_session
from backend.utils.timezone import timezone

pytestmark = pytest.mark.asyncio


def _identity() -> tuple[str, int]:
    suffix = int(uuid.uuid4().hex[:10], 16)
    return f'h_storage_{suffix:x}', 980_000_000 + suffix % 10_000_000


async def _delete_owner(owner_hasn_id: str, user_id: int) -> None:
    async with async_db_session.begin() as db:
        await db.execute(
            text('DELETE FROM hasn_storage_reservations WHERE owner_hasn_id = :owner'),
            {'owner': owner_hasn_id},
        )
        await db.execute(
            text('DELETE FROM hasn_storage_accounts WHERE owner_hasn_id = :owner'),
            {'owner': owner_hasn_id},
        )
        await db.execute(
            text('DELETE FROM hasn_billing.user_subscription WHERE user_id = :user_id AND app_code = :app'),
            {'user_id': user_id, 'app': 'huanxing'},
        )
        await db.execute(
            text('DELETE FROM hasn_humans WHERE hasn_id = :owner'),
            {'owner': owner_hasn_id},
        )


async def _seed_human(owner_hasn_id: str, user_id: int) -> None:
    async with async_db_session.begin() as db:
        await db.execute(
            text(
                """
                INSERT INTO hasn_humans
                    (hasn_id, star_id, user_id, nickname, status, contact_policy, stats, created_time)
                VALUES
                    (:owner, :star_id, :user_id, :nickname, 'active', '{}'::jsonb, '{}'::jsonb, now())
                """
            ),
            {
                'owner': owner_hasn_id,
                'star_id': f'st{user_id}',
                'user_id': user_id,
                'nickname': f'存储测试主人_{owner_hasn_id[-12:]}',
            },
        )


async def _seed_override_account(owner_hasn_id: str, quota_bytes: int) -> None:
    async with async_db_session.begin() as db:
        await db.execute(
            text(
                """
                INSERT INTO hasn_storage_accounts
                    (owner_hasn_id, quota_bytes, used_bytes, reserved_bytes, quota_source,
                     quota_version, quota_valid_until, state, created_time)
                VALUES
                    (:owner, :quota, 0, 0, 'admin_override', 'test-override',
                     now() + interval '1 hour', 'active', now())
                """
            ),
            {'owner': owner_hasn_id, 'quota': quota_bytes},
        )


async def test_concurrent_reservations_cannot_oversell_quota() -> None:
    owner, user_id = _identity()
    await _seed_human(owner, user_id)
    await _seed_override_account(owner, 100)
    service = OwnerStorageService(async_db_session)
    try:
        results = await asyncio.gather(
            service.reserve(owner_hasn_id=owner, requested_bytes=60, idempotency_key='upload-a'),
            service.reserve(owner_hasn_id=owner, requested_bytes=60, idempotency_key='upload-b'),
            return_exceptions=True,
        )
        successes = [item for item in results if not isinstance(item, BaseException)]
        failures = [item for item in results if isinstance(item, BaseException)]

        assert len(successes) == 1
        assert len(failures) == 1
        failure = failures[0]
        assert isinstance(failure, errors.RequestError)
        assert failure.code == 507
        assert failure.msg == 'STORAGE_QUOTA_EXCEEDED'
        assert failure.data == {
            'quota_bytes': 100,
            'used_bytes': 0,
            'reserved_bytes': 60,
            'requested_bytes': 60,
        }

        usage = await service.usage(owner_hasn_id=owner)
        assert usage.reserved_bytes == 60
        assert usage.used_bytes == 0
    finally:
        await _delete_owner(owner, user_id)


async def test_reservation_idempotency_commit_and_release_are_exact() -> None:
    owner, user_id = _identity()
    await _seed_human(owner, user_id)
    await _seed_override_account(owner, 1000)
    service = OwnerStorageService(async_db_session)
    try:
        first = await service.reserve(owner_hasn_id=owner, requested_bytes=250, idempotency_key='same-call')
        replay = await service.reserve(owner_hasn_id=owner, requested_bytes=250, idempotency_key='same-call')
        assert replay.reservation_id == first.reservation_id
        assert replay.object_id == first.object_id
        assert (await service.usage(owner_hasn_id=owner)).reserved_bytes == 250

        committed = await service.commit_reservation(first.reservation_id, actual_bytes=200)
        assert committed.status == 'committed'
        usage = await service.usage(owner_hasn_id=owner)
        assert usage.used_bytes == 200
        assert usage.reserved_bytes == 0

        committed_replay = await service.commit_reservation(first.reservation_id, actual_bytes=200)
        assert committed_replay.status == 'committed'
        usage = await service.usage(owner_hasn_id=owner)
        assert usage.used_bytes == 200
        assert usage.reserved_bytes == 0

        pending = await service.reserve(owner_hasn_id=owner, requested_bytes=100, idempotency_key='release-call')
        released = await service.release_reservation(pending.reservation_id)
        assert released.status == 'released'
        await service.release_reservation(pending.reservation_id)
        usage = await service.usage(owner_hasn_id=owner)
        assert usage.used_bytes == 200
        assert usage.reserved_bytes == 0
    finally:
        await _delete_owner(owner, user_id)


async def test_same_idempotency_key_rejects_different_size() -> None:
    owner, user_id = _identity()
    await _seed_human(owner, user_id)
    await _seed_override_account(owner, 1000)
    service = OwnerStorageService(async_db_session)
    try:
        await service.reserve(owner_hasn_id=owner, requested_bytes=100, idempotency_key='size-conflict')
        with pytest.raises(errors.ConflictError, match='STORAGE_IDEMPOTENCY_CONFLICT'):
            await service.reserve(owner_hasn_id=owner, requested_bytes=101, idempotency_key='size-conflict')
    finally:
        await _delete_owner(owner, user_id)


async def test_missing_account_is_not_reported_as_quota_exceeded() -> None:
    owner, user_id = _identity()
    await _seed_human(owner, user_id)
    service = OwnerStorageService(async_db_session)
    try:
        with pytest.raises(errors.ServerError, match='STORAGE_ACCOUNT_NOT_READY') as exc_info:
            await service.reserve_existing_account(
                owner_hasn_id=owner,
                requested_bytes=10,
                idempotency_key='missing-account',
            )
        assert exc_info.value.code == 500
    finally:
        await _delete_owner(owner, user_id)


async def test_account_initialization_uses_free_catalog_policy() -> None:
    owner, user_id = _identity()
    await _seed_human(owner, user_id)
    service = OwnerStorageService(async_db_session)
    try:
        usage = await service.usage(owner_hasn_id=owner)
        async with async_db_session() as db:
            expected = (
                await db.execute(
                    text(
                        """
                        SELECT (quota_json ->> 'storage_bytes')::bigint
                        FROM hasn_billing.billing_plan
                        WHERE offering_key = 'llm:tier'
                          AND status = 'active'
                          AND (plan_key = 'free' OR quota_json ->> 'tier' = 'free')
                        ORDER BY id DESC
                        LIMIT 1
                        """
                    )
                )
            ).scalar_one()
        assert usage.quota_bytes == expected
        assert usage.quota_source == 'free_policy'
        assert usage.quota_version.startswith('billing_plan:')
        assert usage.quota_valid_until is None
    finally:
        await _delete_owner(owner, user_id)


async def test_lazy_derivation_activates_scheduled_contract_without_event() -> None:
    owner, user_id = _identity()
    now = timezone.now()
    switch_time = now - timedelta(seconds=1)
    old_quota = 1000
    new_quota = 400
    await _seed_human(owner, user_id)
    async with async_db_session.begin() as db:
        await db.execute(
            text(
                """
                INSERT INTO hasn_billing.user_subscription
                    (user_id, tier, monthly_credits, current_credits, used_credits, purchased_credits,
                     billing_cycle_start, billing_cycle_end, status, app_code, contract_no,
                     contract_start_at, contract_end_at, cycle_seconds, cycle_count, plan_snapshot)
                VALUES
                    (:user_id, 'flagship', 0, 0, 0, 0, :old_start, :switch_time, 'expired',
                     'huanxing', :old_contract, :old_start, :switch_time, 2592000, 1,
                     jsonb_build_object('storage_bytes', CAST(:old_quota AS bigint))),
                    (:user_id, 'pro', 0, 0, 0, 0, :switch_time, :new_end, 'scheduled',
                     'huanxing', :new_contract, :switch_time, :new_end, 2592000, 1,
                     jsonb_build_object('storage_bytes', CAST(:new_quota AS bigint)))
                """
            ),
            {
                'user_id': user_id,
                'old_start': now - timedelta(days=30),
                'switch_time': switch_time,
                'new_end': now + timedelta(days=30),
                'old_contract': f'old-{uuid.uuid4().hex}',
                'new_contract': f'new-{uuid.uuid4().hex}',
                'old_quota': old_quota,
                'new_quota': new_quota,
            },
        )
        await db.execute(
            text(
                """
                INSERT INTO hasn_storage_accounts
                    (owner_hasn_id, quota_bytes, used_bytes, reserved_bytes, quota_source,
                     quota_version, quota_valid_until, state, created_time)
                VALUES
                    (:owner, :old_quota, 500, 0, 'subscription', 'expired-contract',
                     :switch_time, 'active', now())
                """
            ),
            {'owner': owner, 'old_quota': old_quota, 'switch_time': switch_time},
        )

    service = OwnerStorageService(async_db_session)
    try:
        usage = await service.usage(owner_hasn_id=owner, now=now)
        assert usage.quota_bytes == new_quota
        assert usage.quota_source == 'subscription'
        assert usage.state == 'over_quota'
        assert usage.used_bytes == 500

        with pytest.raises(errors.RequestError, match='STORAGE_QUOTA_EXCEEDED'):
            await service.reserve(owner_hasn_id=owner, requested_bytes=1, idempotency_key='after-downgrade')
    finally:
        await _delete_owner(owner, user_id)
