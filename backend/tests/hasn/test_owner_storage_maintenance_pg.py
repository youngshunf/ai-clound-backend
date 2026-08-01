from __future__ import annotations

import uuid

from datetime import timedelta

import pytest

from sqlalchemy import text

from backend.app.hasn.service.owner_storage_maintenance_service import (
    OwnerStorageMaintenanceService,
)
from backend.app.hasn.service.owner_storage_service import OwnerStorageService
from backend.database.db import async_db_session
from backend.plugin.s3.service.storage_service import StorageService
from backend.utils.timezone import timezone

pytestmark = pytest.mark.asyncio


def _identity() -> tuple[str, int]:
    suffix = int(uuid.uuid4().hex[:10], 16)
    return f'h_storage_maintenance_{suffix:x}', 996_000_000 + suffix % 3_000_000


async def _chunks(payload: bytes):
    yield payload


async def _seed_owner(owner: str, user_id: int) -> None:
    async with async_db_session.begin() as db:
        await db.execute(
            text(
                """
                INSERT INTO hasn_humans
                    (hasn_id, star_id, user_id, nickname, status, contact_policy, stats, created_time)
                VALUES
                    (:owner, :star, :user_id, :nickname, 'active',
                     '{}'::jsonb, '{}'::jsonb, now())
                """
            ),
            {
                'owner': owner,
                'star': f'sm{user_id}',
                'user_id': user_id,
                'nickname': f'存储维护测试_{owner[-10:]}',
            },
        )
        await db.execute(
            text(
                """
                INSERT INTO hasn_storage_accounts
                    (owner_hasn_id, quota_bytes, used_bytes, reserved_bytes, quota_source,
                     quota_version, quota_valid_until, state, created_time)
                VALUES
                    (:owner, 10485760, 0, 0, 'admin_override', 'maintenance-test',
                     now() + interval '1 hour', 'active', now())
                """
            ),
            {'owner': owner},
        )


async def _cleanup(owner: str) -> None:
    async with async_db_session() as db:
        objects = (
            await db.execute(
                text(
                    """
                    SELECT storage_id, object_key
                    FROM hasn_storage_objects
                    WHERE owner_hasn_id = :owner
                      AND state <> 'deleted'
                    """
                ),
                {'owner': owner},
            )
        ).mappings().all()
        for obj in objects:
            await StorageService.delete_object(
                db,
                storage_id=int(obj['storage_id']),
                object_key=str(obj['object_key']),
            )
    async with async_db_session.begin() as db:
        await db.execute(
            text(
                """
                DELETE FROM hasn_storage_migration_items
                WHERE job_id IN (
                    SELECT job_id FROM hasn_storage_jobs WHERE owner_hasn_id = :owner
                )
                """
            ),
            {'owner': owner},
        )
        for table in (
            'hasn_asset_bindings',
            'hasn_storage_entries',
            'hasn_assets',
            'hasn_storage_objects',
            'hasn_storage_reservations',
            'hasn_storage_jobs',
            'hasn_storage_accounts',
        ):
            await db.execute(
                text(f'DELETE FROM {table} WHERE owner_hasn_id = :owner'),
                {'owner': owner},
            )
        await db.execute(
            text(
                """
                DELETE FROM hasn_notification_im_command_outbox
                WHERE causation_id IN (
                    SELECT 'notification:' || id::text
                    FROM hasn_notifications
                    WHERE target_id = :owner
                )
                """
            ),
            {'owner': owner},
        )
        await db.execute(text('DELETE FROM hasn_notifications WHERE target_id = :owner'), {'owner': owner})
        await db.execute(text('DELETE FROM hasn_humans WHERE hasn_id = :owner'), {'owner': owner})


async def test_expired_reservation_is_released_only_when_no_asset_exists() -> None:
    owner, user_id = _identity()
    await _seed_owner(owner, user_id)
    storage = OwnerStorageService(async_db_session)
    maintenance = OwnerStorageMaintenanceService(async_db_session)
    try:
        reservation = await storage.reserve(
            owner_hasn_id=owner,
            requested_bytes=123,
            idempotency_key='expired-without-asset',
        )
        async with async_db_session.begin() as db:
            await db.execute(
                text(
                    """
                    UPDATE hasn_storage_reservations
                    SET expires_time = :expired
                    WHERE reservation_id = :reservation_id
                    """
                ),
                {
                    'reservation_id': reservation.reservation_id,
                    'expired': timezone.now() - timedelta(minutes=1),
                },
            )

        report = await maintenance.sweep_expired_reservations(limit=10)

        assert report == {'checked': 1, 'completed': 0, 'expired': 1}
        async with async_db_session() as db:
            status = (
                await db.execute(
                    text(
                        """
                        SELECT status
                        FROM hasn_storage_reservations
                        WHERE reservation_id = :reservation_id
                        """
                    ),
                    {'reservation_id': reservation.reservation_id},
                )
            ).scalar_one()
        assert status == 'expired'
        usage = await storage.usage(owner_hasn_id=owner)
        assert usage.reserved_bytes == 0
        assert usage.used_bytes == 0
    finally:
        await _cleanup(owner)


async def test_expired_reservation_repairs_existing_asset_as_committed() -> None:
    owner, user_id = _identity()
    await _seed_owner(owner, user_id)
    storage = OwnerStorageService(async_db_session)
    maintenance = OwnerStorageMaintenanceService(async_db_session)
    payload = b'expired-reservation-committed-asset'
    try:
        asset = await storage.upload(
            owner_hasn_id=owner,
            chunks=_chunks(payload),
            declared_size=len(payload),
            filename='已落库.txt',
            mime='text/plain',
            category='dm_attachment',
            source_app='maintenance_test',
            idempotency_key='expired-with-asset',
        )
        async with async_db_session.begin() as db:
            await db.execute(
                text(
                    """
                    UPDATE hasn_storage_reservations
                    SET status = 'reserved',
                        result_asset_id = NULL,
                        expires_time = :expired
                    WHERE owner_hasn_id = :owner
                      AND idempotency_key = 'expired-with-asset'
                    """
                ),
                {'owner': owner, 'expired': timezone.now() - timedelta(minutes=1)},
            )
            await db.execute(
                text(
                    """
                    UPDATE hasn_storage_accounts
                    SET used_bytes = 0, reserved_bytes = :size
                    WHERE owner_hasn_id = :owner
                    """
                ),
                {'owner': owner, 'size': len(payload)},
            )

        report = await maintenance.sweep_expired_reservations(limit=10)

        assert report == {'checked': 1, 'completed': 1, 'expired': 0}
        async with async_db_session() as db:
            reservation = (
                await db.execute(
                    text(
                        """
                        SELECT status, result_asset_id
                        FROM hasn_storage_reservations
                        WHERE owner_hasn_id = :owner
                          AND idempotency_key = 'expired-with-asset'
                        """
                    ),
                    {'owner': owner},
                )
            ).mappings().one()
        assert reservation['status'] == 'committed'
        assert reservation['result_asset_id'] == asset.asset_id
        usage = await storage.usage(owner_hasn_id=owner)
        assert usage.reserved_bytes == 0
        assert usage.used_bytes == len(payload)
    finally:
        await _cleanup(owner)


async def test_unbound_sweep_trashes_business_attachment_but_keeps_user_upload() -> None:
    owner, user_id = _identity()
    await _seed_owner(owner, user_id)
    storage = OwnerStorageService(async_db_session)
    maintenance = OwnerStorageMaintenanceService(async_db_session)
    try:
        attachment = await storage.upload(
            owner_hasn_id=owner,
            chunks=_chunks(b'unbound-business-attachment'),
            declared_size=len(b'unbound-business-attachment'),
            filename='未发送附件.txt',
            mime='text/plain',
            category='dm_attachment',
            source_app='hasn_assets_app',
            idempotency_key='unbound-attachment',
        )
        managed = await storage.upload(
            owner_hasn_id=owner,
            chunks=_chunks(b'explicit-user-managed-file'),
            declared_size=len(b'explicit-user-managed-file'),
            filename='我的文件.txt',
            mime='text/plain',
            category='user_upload',
            source_app='owner_storage',
            idempotency_key='managed-upload',
        )
        async with async_db_session.begin() as db:
            await db.execute(
                text(
                    """
                    UPDATE hasn_assets
                    SET created_time = :old
                    WHERE asset_id IN (:attachment, :managed)
                    """
                ),
                {
                    'old': timezone.now() - timedelta(days=31),
                    'attachment': attachment.asset_id,
                    'managed': managed.asset_id,
                },
            )

        report = await maintenance.sweep_unbound_assets(limit=10, owner_hasn_id=owner)

        assert report == {'checked': 1, 'trashed': 1}
        async with async_db_session() as db:
            rows = (
                await db.execute(
                    text(
                        """
                        SELECT asset_id, lifecycle_status
                        FROM hasn_assets
                        WHERE asset_id IN (:attachment, :managed)
                        """
                    ),
                    {'attachment': attachment.asset_id, 'managed': managed.asset_id},
                )
            ).mappings().all()
            notification = (
                await db.execute(
                    text(
                        """
                        SELECT title, body, data, source, priority, delivery
                        FROM hasn_notifications
                        WHERE target_id = :owner
                          AND type = 'storage_unbound_asset_trashed'
                        """
                    ),
                    {'owner': owner},
                )
            ).mappings().one()
        states = {str(row['asset_id']): str(row['lifecycle_status']) for row in rows}
        assert states[attachment.asset_id] == 'trashed'
        assert states[managed.asset_id] == 'active'
        # 一轮清理只发一条聚合通知（`.one()` 已断言不重复），标题带件数、正文列出文件名。
        assert notification['title'] == '已清理 1 个未使用的云端附件'
        assert '未发送附件.txt' in str(notification['body'])
        assert notification['data']['trashed_count'] == 1
        assert notification['data']['trashed_names'] == ['未发送附件.txt']
        assert notification['data']['link'] == 'hasn://storage/trash'
        # 来源展示名为中文，且承载只留通知中心（不再建服务号会话、不弹 toast/系统推送）。
        assert notification['source']['display_name'] == '云存储'
        assert notification['priority'] == 'normal'
        channels = notification['delivery']['channels']
        assert channels['center'] is True
        assert channels['card_message'] is False
        assert channels['toast'] is False
        assert channels['push'] is False
    finally:
        await _cleanup(owner)
