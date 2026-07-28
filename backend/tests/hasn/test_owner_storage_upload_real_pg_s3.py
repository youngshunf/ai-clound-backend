from __future__ import annotations

import hashlib
import uuid

from collections.abc import AsyncIterator

import pytest

from sqlalchemy import text

from backend.app.hasn.service.owner_storage_service import OwnerStorageService
from backend.common.exception import errors
from backend.database.db import async_db_session
from backend.plugin.s3.service.storage_service import StorageService

pytestmark = pytest.mark.asyncio


def _identity(label: str) -> tuple[str, int]:
    suffix = int(uuid.uuid4().hex[:10], 16)
    return f'h_upload_{label}_{suffix:x}', 970_000_000 + suffix % 10_000_000


async def _chunks(payload: bytes, size: int = 7):
    for offset in range(0, len(payload), size):
        yield payload[offset : offset + size]


async def _seed_owner(owner: str, user_id: int, quota_bytes: int = 10 * 1024 * 1024) -> None:
    async with async_db_session.begin() as db:
        await db.execute(
            text(
                """
                INSERT INTO hasn_humans
                    (hasn_id, star_id, user_id, nickname, status, contact_policy, stats, created_time)
                VALUES
                    (:owner, :star, :user_id, :nickname, 'active', '{}'::jsonb, '{}'::jsonb, now())
                """
            ),
            {
                'owner': owner,
                'star': f'up{user_id}',
                'user_id': user_id,
                'nickname': f'上传集成测试_{owner[-12:]}',
            },
        )
        await db.execute(
            text(
                """
                INSERT INTO hasn_storage_accounts
                    (owner_hasn_id, quota_bytes, used_bytes, reserved_bytes, quota_source,
                     quota_version, quota_valid_until, state, created_time)
                VALUES
                    (:owner, :quota, 0, 0, 'admin_override', 'real-s3-test',
                     now() + interval '1 hour', 'active', now())
                """
            ),
            {'owner': owner, 'quota': quota_bytes},
        )


async def _cleanup_owners(owners: list[tuple[str, int]]) -> None:
    owner_ids = [owner for owner, _ in owners]
    async with async_db_session() as db:
        objects = (
            await db.execute(
                text(
                    """
                    SELECT DISTINCT storage_id, object_key
                    FROM hasn_storage_objects
                    WHERE owner_hasn_id = ANY(:owners)
                    """
                ),
                {'owners': owner_ids},
            )
        ).mappings().all()
        for obj in objects:
            await StorageService.delete_object(
                db,
                storage_id=int(obj['storage_id']),
                object_key=str(obj['object_key']),
            )

    async with async_db_session.begin() as db:
        for table in (
            'hasn_storage_entries',
            'hasn_asset_bindings',
            'hasn_assets',
            'hasn_storage_objects',
            'hasn_storage_reservations',
            'hasn_storage_jobs',
            'hasn_storage_accounts',
        ):
            await db.execute(
                text(f'DELETE FROM {table} WHERE owner_hasn_id = ANY(:owners)'),
                {'owners': owner_ids},
            )
        await db.execute(
            text('DELETE FROM hasn_humans WHERE hasn_id = ANY(:owners)'),
            {'owners': owner_ids},
        )


async def test_real_private_upload_is_owner_scoped_deduplicated_and_idempotent() -> None:
    owner_a = _identity('a')
    owner_b = _identity('b')
    await _seed_owner(*owner_a)
    await _seed_owner(*owner_b)
    service = OwnerStorageService(async_db_session)
    payload = f'real-owner-storage-{uuid.uuid4().hex}'.encode()
    expected_sha = hashlib.sha256(payload).hexdigest()
    try:
        first = await service.upload(
            owner_hasn_id=owner_a[0],
            chunks=_chunks(payload),
            declared_size=len(payload),
            filename='隐私 文件名.txt',
            mime='text/plain',
            category='user_upload',
            source_app='storage_test',
            idempotency_key='real-upload-first',
        )
        replay = await service.upload(
            owner_hasn_id=owner_a[0],
            chunks=_chunks(payload),
            declared_size=len(payload),
            filename='隐私 文件名.txt',
            mime='text/plain',
            category='user_upload',
            source_app='storage_test',
            idempotency_key='real-upload-first',
        )
        duplicate = await service.upload(
            owner_hasn_id=owner_a[0],
            chunks=_chunks(payload),
            declared_size=len(payload),
            filename='同内容副本.txt',
            mime='text/plain',
            category='user_upload',
            source_app='storage_test',
            idempotency_key='real-upload-second',
        )
        cross_owner = await service.upload(
            owner_hasn_id=owner_b[0],
            chunks=_chunks(payload),
            declared_size=len(payload),
            filename='另一个主人.txt',
            mime='text/plain',
            category='user_upload',
            source_app='storage_test',
            idempotency_key='real-upload-cross-owner',
        )

        assert replay.asset_id == first.asset_id
        assert duplicate.asset_id != first.asset_id
        assert duplicate.object_id == first.object_id
        assert cross_owner.object_id != first.object_id
        assert first.uri == f'hasn://asset/{first.asset_id}'

        async with async_db_session() as db:
            objects = (
                await db.execute(
                    text(
                        """
                        SELECT owner_hasn_id, object_id, object_key, size_bytes, sha256, ref_count, state
                        FROM hasn_storage_objects
                        WHERE owner_hasn_id = ANY(:owners)
                        ORDER BY owner_hasn_id
                        """
                    ),
                    {'owners': [owner_a[0], owner_b[0]]},
                )
            ).mappings().all()
            usages = (
                await db.execute(
                    text(
                        """
                        SELECT owner_hasn_id, used_bytes, reserved_bytes
                        FROM hasn_storage_accounts
                        WHERE owner_hasn_id = ANY(:owners)
                        ORDER BY owner_hasn_id
                        """
                    ),
                    {'owners': [owner_a[0], owner_b[0]]},
                )
            ).mappings().all()

        assert len(objects) == 2
        by_owner = {str(row['owner_hasn_id']): row for row in objects}
        assert by_owner[owner_a[0]]['ref_count'] == 2
        assert by_owner[owner_b[0]]['ref_count'] == 1
        for owner, row in by_owner.items():
            assert row['object_key'] == f"owners/{owner}/objects/{row['object_id']}"
            assert '隐私' not in row['object_key']
            assert expected_sha not in row['object_key']
            assert row['sha256'] == expected_sha
            assert row['size_bytes'] == len(payload)
            assert row['state'] == 'active'
        assert all(row['used_bytes'] == len(payload) for row in usages)
        assert all(row['reserved_bytes'] == 0 for row in usages)
    finally:
        await _cleanup_owners([owner_a, owner_b])


async def test_same_idempotency_key_rejects_different_content_with_same_size() -> None:
    """幂等键必须绑定请求载荷，不能只比较声明大小后复用已有对象。"""
    owner = _identity('idempotency_payload')
    await _seed_owner(*owner)
    service = OwnerStorageService(async_db_session)
    original = b'payload-A'
    conflicting = b'payload-B'
    try:
        first = await service.upload(
            owner_hasn_id=owner[0],
            chunks=_chunks(original),
            declared_size=len(original),
            filename='幂等载荷.txt',
            mime='text/plain',
            category='user_upload',
            source_app='storage_test',
            idempotency_key='same-size-different-content',
        )

        with pytest.raises(errors.ConflictError, match='STORAGE_IDEMPOTENCY_CONFLICT'):
            await service.upload(
                owner_hasn_id=owner[0],
                chunks=_chunks(conflicting),
                declared_size=len(conflicting),
                filename='幂等载荷.txt',
                mime='text/plain',
                category='user_upload',
                source_app='storage_test',
                idempotency_key='same-size-different-content',
            )

        async with async_db_session() as db:
            row = (
                await db.execute(
                    text(
                        """
                        SELECT o.storage_id, o.object_key, o.sha256, o.size_bytes
                        FROM hasn_storage_objects AS o
                        WHERE o.object_id = :object_id
                        """
                    ),
                    {'object_id': first.object_id},
                )
            ).mappings().one()
            storage = await StorageService.get_storage(db, int(row['storage_id']))
        stored_sha, stored_size = await StorageService.sha256_on_storage(
            storage,
            object_key=str(row['object_key']),
        )
        assert stored_sha == hashlib.sha256(original).hexdigest()
        assert row['sha256'] == stored_sha
        assert stored_size == row['size_bytes'] == len(original)
    finally:
        await _cleanup_owners([owner])


async def test_unknown_length_stream_cannot_exceed_owner_quota() -> None:
    owner = _identity('unknown_length')
    await _seed_owner(*owner, quota_bytes=11)
    service = OwnerStorageService(async_db_session)
    payload = b'unknown-size'
    try:
        with pytest.raises(errors.RequestError, match='STORAGE_QUOTA_EXCEEDED') as failure:
            await service.upload(
                owner_hasn_id=owner[0],
                chunks=_chunks(payload, size=4),
                declared_size=None,
                filename='未知长度.txt',
                mime='text/plain',
                category='user_upload',
                source_app='storage_test',
                idempotency_key='real-upload-unknown-length',
            )

        assert failure.value.data['quota_bytes'] == 11
        assert failure.value.data['requested_bytes'] == len(payload)
        async with async_db_session() as db:
            account = (
                await db.execute(
                    text(
                        """
                        SELECT used_bytes, reserved_bytes
                        FROM hasn_storage_accounts
                        WHERE owner_hasn_id = :owner
                        """
                    ),
                    {'owner': owner[0]},
                )
            ).mappings().one()
            persisted_rows = (
                await db.execute(
                    text(
                        """
                        SELECT
                            (SELECT COUNT(*) FROM hasn_storage_reservations
                             WHERE owner_hasn_id = :owner)
                          + (SELECT COUNT(*) FROM hasn_storage_objects
                             WHERE owner_hasn_id = :owner)
                          + (SELECT COUNT(*) FROM hasn_assets
                             WHERE owner_hasn_id = :owner)
                          + (SELECT COUNT(*) FROM hasn_storage_entries
                             WHERE owner_hasn_id = :owner)
                          + (SELECT COUNT(*) FROM hasn_storage_jobs
                             WHERE owner_hasn_id = :owner)
                        """
                    ),
                    {'owner': owner[0]},
                )
            ).scalar_one()

        assert account == {'used_bytes': 0, 'reserved_bytes': 0}
        assert persisted_rows == 0
    finally:
        await _cleanup_owners([owner])


async def test_declared_size_over_remaining_quota_is_rejected_before_reading_stream() -> None:
    owner = _identity('declared_over_quota')
    await _seed_owner(*owner, quota_bytes=11)
    service = OwnerStorageService(async_db_session)

    async def unread_stream() -> AsyncIterator[bytes]:
        raise AssertionError('已知超额请求不应读取上传流')
        yield b'unreachable'

    try:
        with pytest.raises(errors.RequestError, match='STORAGE_QUOTA_EXCEEDED') as failure:
            await service.upload(
                owner_hasn_id=owner[0],
                chunks=unread_stream(),
                declared_size=12,
                filename='已知超额.txt',
                mime='text/plain',
                category='user_upload',
                source_app='storage_test',
                idempotency_key='real-upload-declared-over-quota',
            )

        assert failure.value.data['quota_bytes'] == 11
        assert failure.value.data['requested_bytes'] == 12
    finally:
        await _cleanup_owners([owner])


async def test_finalize_failure_records_and_executes_owner_scoped_orphan_cleanup() -> None:
    owner = _identity('orphan')
    await _seed_owner(*owner)
    service = OwnerStorageService(async_db_session)
    payload = f'orphan-cleanup-{uuid.uuid4().hex}'.encode()
    idempotency_key = 'real-upload-finalize-failure'
    reservation = await service.reserve(
        owner_hasn_id=owner[0],
        requested_bytes=len(payload),
        idempotency_key=idempotency_key,
    )
    object_key = f'owners/{owner[0]}/objects/{reservation.object_id}'
    storage_id: int | None = None
    try:
        async with async_db_session.begin() as db:
            storage = await StorageService.get_write_storage(db, access='private')
            storage_id = int(storage.id)
            await db.execute(
                text(
                    """
                    INSERT INTO hasn_storage_objects
                        (object_id, owner_hasn_id, storage_id, object_key, key_layout, access,
                         size_bytes, sha256, billable_to_owner, ref_count, state,
                         created_time, updated_time)
                    VALUES
                        (:object_id, :owner, :storage_id, :object_key, 'owner_scoped', 'private',
                         0, NULL, TRUE, 0, 'error', now(), now())
                    """
                ),
                {
                    'object_id': reservation.object_id,
                    'owner': owner[0],
                    'storage_id': storage_id,
                    'object_key': f'owners/{owner[0]}/seeded-conflict/{reservation.object_id}',
                },
            )

        with pytest.raises(errors.GatewayError, match='STORAGE_UPLOAD_FAILED'):
            await service.upload(
                owner_hasn_id=owner[0],
                chunks=_chunks(payload),
                declared_size=len(payload),
                filename='终结失败.txt',
                mime='text/plain',
                category='user_upload',
                source_app='storage_test',
                idempotency_key=idempotency_key,
            )

        async with async_db_session() as db:
            account = (
                await db.execute(
                    text(
                        """
                        SELECT used_bytes, reserved_bytes
                        FROM hasn_storage_accounts
                        WHERE owner_hasn_id = :owner
                        """
                    ),
                    {'owner': owner[0]},
                )
            ).mappings().one()
            reservation_status = (
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
            job = (
                await db.execute(
                    text(
                        """
                        SELECT job_id, status, payload
                        FROM hasn_storage_jobs
                        WHERE owner_hasn_id = :owner
                          AND job_type = 'orphan_cleanup'
                          AND payload ->> 'reservation_id' = :reservation_id
                        """
                    ),
                    {
                        'owner': owner[0],
                        'reservation_id': reservation.reservation_id,
                    },
                )
            ).mappings().one()
            persisted_storage = await StorageService.get_storage(db, storage_id)

        assert account == {'used_bytes': 0, 'reserved_bytes': 0}
        assert reservation_status == 'released'
        assert job['status'] == 'pending'
        assert job['payload']['object_key'] == object_key
        assert (
            await StorageService.stat_on_storage(
                persisted_storage,
                object_key=object_key,
            )
        ).size == len(payload)

        assert (
            await service.process_jobs(
                job_type='orphan_cleanup',
                limit=1,
                owner_hasn_id=owner[0],
            )
            == 1
        )
        with pytest.raises(errors.ServerError, match='S3 对象元数据读取失败'):
            await StorageService.stat_on_storage(
                persisted_storage,
                object_key=object_key,
            )
        async with async_db_session() as db:
            job_status = (
                await db.execute(
                    text('SELECT status FROM hasn_storage_jobs WHERE job_id = :job_id'),
                    {'job_id': job['job_id']},
                )
            ).scalar_one()
        assert job_status == 'succeeded'
    finally:
        if storage_id is not None:
            async with async_db_session() as db:
                cleanup_storage = await StorageService.get_storage(db, storage_id)
            await StorageService.delete_on_storage(cleanup_storage, object_key=object_key)
        await _cleanup_owners([owner])


async def test_orphan_cleanup_skips_location_owned_by_live_object() -> None:
    """补偿作业执行前必须重验位置，不能删除已被活跃资产接管的对象。"""
    owner = _identity('orphan_guard')
    await _seed_owner(*owner)
    service = OwnerStorageService(async_db_session)
    payload = f'orphan-live-guard-{uuid.uuid4().hex}'.encode()
    try:
        stored = await service.upload(
            owner_hasn_id=owner[0],
            chunks=_chunks(payload),
            declared_size=len(payload),
            filename='活跃对象.txt',
            mime='text/plain',
            category='user_upload',
            source_app='storage_test',
            idempotency_key='orphan-live-guard-upload',
        )
        async with async_db_session.begin() as db:
            location = (
                await db.execute(
                    text(
                        """
                        SELECT storage_id, object_key
                        FROM hasn_storage_objects
                        WHERE object_id = :object_id
                        """
                    ),
                    {'object_id': stored.object_id},
                )
            ).mappings().one()
            await service._insert_orphan_cleanup_job(
                db,
                owner_hasn_id=owner[0],
                storage_id=int(location['storage_id']),
                object_key=str(location['object_key']),
                reservation_id='test-live-location',
                reason='测试过期补偿作业',
            )
            storage = await StorageService.get_storage(db, int(location['storage_id']))

        assert await service.process_jobs(
            job_type='orphan_cleanup',
            limit=1,
            owner_hasn_id=owner[0],
        ) == 1
        assert (
            await StorageService.stat_on_storage(
                storage,
                object_key=str(location['object_key']),
            )
        ).size == len(payload)
    finally:
        await _cleanup_owners([owner])


async def test_real_public_upload_uses_opaque_owner_scope(monkeypatch: pytest.MonkeyPatch) -> None:
    owner = _identity('public')
    await _seed_owner(*owner)
    monkeypatch.setenv('OWNER_SCOPE_SALT', f'test-salt-{uuid.uuid4().hex}')
    service = OwnerStorageService(async_db_session)
    payload = b'\x89PNG\r\nowner-storage-real-public'
    try:
        result = await service.upload(
            owner_hasn_id=owner[0],
            chunks=_chunks(payload),
            declared_size=len(payload),
            filename='avatar.png',
            mime='image/png',
            category='user_avatar',
            source_app='profile',
            idempotency_key='real-public-avatar',
        )
        async with async_db_session() as db:
            row = (
                await db.execute(
                    text(
                        """
                        SELECT object_key, access, billable_to_owner
                        FROM hasn_storage_objects
                        WHERE object_id = :object_id
                        """
                    ),
                    {'object_id': result.object_id},
                )
            ).mappings().one()
        assert row['access'] == 'public'
        assert row['billable_to_owner'] is True
        assert owner[0] not in row['object_key']
        assert row['object_key'].startswith('owners/')
        assert f"/objects/{result.object_id}" in row['object_key']
    finally:
        await _cleanup_owners([owner])


async def test_same_owner_same_content_can_have_private_and_public_objects(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """去重身份包含 access，公开与私有对象不能被错误唯一约束互斥。"""
    owner = _identity('access_dedupe')
    await _seed_owner(*owner)
    monkeypatch.setenv('OWNER_SCOPE_SALT', f'test-salt-{uuid.uuid4().hex}')
    service = OwnerStorageService(async_db_session)
    payload = b'\x89PNG\r\nsame-owner-private-public'
    try:
        private = await service.upload(
            owner_hasn_id=owner[0],
            chunks=_chunks(payload),
            declared_size=len(payload),
            filename='私有图片.png',
            mime='image/png',
            category='user_upload',
            source_app='storage_test',
            idempotency_key='access-dedupe-private',
        )
        public = await service.upload(
            owner_hasn_id=owner[0],
            chunks=_chunks(payload),
            declared_size=len(payload),
            filename='公开头像.png',
            mime='image/png',
            category='user_avatar',
            source_app='storage_test',
            idempotency_key='access-dedupe-public',
        )

        assert private.object_id != public.object_id
        async with async_db_session() as db:
            rows = (
                await db.execute(
                    text(
                        """
                        SELECT access, object_key
                        FROM hasn_storage_objects
                        WHERE object_id IN (:private_object_id, :public_object_id)
                        ORDER BY access
                        """
                    ),
                    {
                        'private_object_id': private.object_id,
                        'public_object_id': public.object_id,
                    },
                )
            ).mappings().all()
        assert [str(row['access']) for row in rows] == ['private', 'public']
        assert owner[0] in str(rows[0]['object_key'])
        assert owner[0] not in str(rows[1]['object_key'])
    finally:
        await _cleanup_owners([owner])
