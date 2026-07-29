from __future__ import annotations

import copy
import uuid

import httpx
import pytest

from sqlalchemy import text

from backend.app.hasn.service.hasn_asset_service import hasn_asset_service
from backend.app.hasn.service.owner_storage_maintenance_service import (
    OwnerStorageMaintenanceService,
)
from backend.app.hasn.service.owner_storage_service import OwnerStorageService
from backend.common.exception import errors
from backend.database.db import async_db_session
from backend.plugin.s3.model import S3Storage
from backend.plugin.s3.service.storage_service import StorageService

pytestmark = pytest.mark.asyncio


async def _chunks(payload: bytes):
    yield payload


async def _seed_owner_and_target() -> tuple[str, int, int]:
    suffix = int(uuid.uuid4().hex[:10], 16)
    owner = f'h_storage_migration_{suffix:x}'
    user_id = 992_000_000 + suffix % 3_000_000
    async with async_db_session.begin() as db:
        await db.execute(
            text(
                """
                DELETE FROM hasn_audit_log
                WHERE details ->> 'owner_hasn_id' = :owner
                """
            ),
            {'owner': owner},
        )
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
                'star': f'mg{user_id}',
                'user_id': user_id,
                'nickname': f'存储迁移测试_{owner[-10:]}',
            },
        )
        await db.execute(
            text(
                """
                INSERT INTO hasn_storage_accounts
                    (owner_hasn_id, quota_bytes, used_bytes, reserved_bytes, quota_source,
                     quota_version, quota_valid_until, state, created_time)
                VALUES
                    (:owner, 10485760, 0, 0, 'admin_override', 'migration-test',
                     now() + interval '1 hour', 'active', now())
                """
            ),
            {'owner': owner},
        )
        source = (
            await db.execute(
                text(
                    """
                    SELECT name, endpoint, access_key, secret_key, bucket, access,
                           sign_strategy, prefix, region, cdn_domain, remark
                    FROM s3_storage
                    WHERE access = 'private'
                    ORDER BY id
                    LIMIT 1
                    """
                )
            )
        ).mappings().one()
        target_id = (
            await db.execute(
                text(
                    """
                    INSERT INTO s3_storage
                        (name, endpoint, access_key, secret_key, bucket, access,
                         sign_strategy, prefix, region, cdn_domain, remark, created_time)
                    VALUES
                        (:name, :endpoint, :access_key, :secret_key, :bucket, :access,
                         :sign_strategy, :prefix, :region, :cdn_domain, :remark, now())
                    RETURNING id
                    """
                ),
                {
                    **dict(source),
                    'name': f'迁移真实测试_{suffix:x}',
                    'access': 'migration_target',
                    'prefix': f'huanxing-storage-migration-test/{suffix:x}',
                    'remark': '用户云存储真实迁移测试临时配置',
                },
            )
        ).scalar_one()
    return owner, user_id, int(target_id)


async def _cleanup(owner: str, target_storage_id: int) -> None:
    async with async_db_session() as db:
        locations = (
            await db.execute(
                text(
                    """
                    SELECT storage_id, object_key
                    FROM hasn_storage_objects
                    WHERE owner_hasn_id = :owner
                    UNION
                    SELECT source_storage_id, source_object_key
                    FROM hasn_storage_migration_items
                    WHERE job_id IN (
                        SELECT job_id FROM hasn_storage_jobs WHERE owner_hasn_id = :owner
                    )
                    UNION
                    SELECT target_storage_id, target_object_key
                    FROM hasn_storage_migration_items
                    WHERE job_id IN (
                        SELECT job_id FROM hasn_storage_jobs WHERE owner_hasn_id = :owner
                    )
                    """
                ),
                {'owner': owner},
            )
        ).all()
        storages: dict[int, S3Storage] = {}
        for storage_id, object_key in locations:
            storage = storages.get(int(storage_id))
            if storage is None:
                storage = copy.copy(await StorageService.get_storage(db, int(storage_id)))
                storages[int(storage_id)] = storage
            await StorageService.delete_on_storage(storage, object_key=str(object_key))
    async with async_db_session.begin() as db:
        await db.execute(
            text("DELETE FROM hasn_audit_log WHERE details ->> 'owner_hasn_id' = :owner"),
            {'owner': owner},
        )
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
        await db.execute(text('DELETE FROM hasn_humans WHERE hasn_id = :owner'), {'owner': owner})
        await db.execute(text('DELETE FROM s3_storage WHERE id = :storage_id'), {'storage_id': target_storage_id})


async def test_owner_migration_switches_without_changing_asset_and_can_rollback() -> None:
    owner, _user_id, target_storage_id = await _seed_owner_and_target()
    service = OwnerStorageService(async_db_session)
    payload = f'real-storage-migration-{uuid.uuid4().hex}'.encode()
    try:
        stored = await service.upload(
            owner_hasn_id=owner,
            chunks=_chunks(payload),
            declared_size=len(payload),
            filename='迁移文件.txt',
            mime='text/plain',
            category='private_doc',
            source_app='migration_test',
            idempotency_key='migration-source',
        )
        async with async_db_session() as db:
            source = (
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
            source_storage = copy.copy(
                await StorageService.get_storage(db, int(source['storage_id']))
            )
        async with async_db_session.begin() as db:
            await db.execute(
                text("UPDATE s3_storage SET access = 'private' WHERE id = :storage_id"),
                {'storage_id': target_storage_id},
            )

        job = await service.create_migration(
            owner_hasn_id=owner,
            target_storage_by_access={'private': target_storage_id},
            observation_seconds=3600,
            audit_actor_id='admin:migration-real-test',
        )
        assert job['status'] == 'pending'
        assert job['total_items'] == 1
        assert await service.pause_migration(
            owner_hasn_id=owner,
            job_id=str(job['job_id']),
            audit_actor_id='admin:migration-real-test',
        ) == {'job_id': job['job_id'], 'status': 'paused'}
        paused = await service.migration_status(
            owner_hasn_id=owner,
            job_id=str(job['job_id']),
        )
        assert paused['status'] == 'paused'
        assert await service.process_jobs(job_type='storage_migration', limit=10) == 0
        assert await service.resume_migration(
            owner_hasn_id=owner,
            job_id=str(job['job_id']),
            audit_actor_id='admin:migration-real-test',
        ) == {'job_id': job['job_id'], 'status': 'pending'}
        assert await service.process_jobs(job_type='storage_migration', limit=10) == 1
        assert await service.process_jobs(job_type='storage_migration', limit=10) == 0

        async with async_db_session() as db:
            migrated = (
                await db.execute(
                    text(
                        """
                        SELECT o.storage_id, o.object_key, o.key_layout,
                               i.source_storage_id, i.source_object_key,
                               i.target_storage_id, i.target_object_key, i.verify_status
                        FROM hasn_storage_objects AS o
                        JOIN hasn_storage_migration_items AS i ON i.object_id = o.object_id
                        WHERE i.job_id = :job_id
                        """
                    ),
                    {'job_id': job['job_id']},
                )
            ).mappings().one()
            target_storage = copy.copy(
                await StorageService.get_storage(db, int(migrated['target_storage_id']))
            )
            audit_actions = list(
                (
                    await db.execute(
                        text(
                            """
                            SELECT action
                            FROM hasn_audit_log
                            WHERE details ->> 'owner_hasn_id' = :owner
                            ORDER BY id
                            """
                        ),
                        {'owner': owner},
                    )
                ).scalars()
            )
        assert migrated['storage_id'] == target_storage_id
        assert migrated['object_key'] == migrated['target_object_key']
        assert migrated['key_layout'] == 'owner_scoped'
        assert migrated['verify_status'] == 'switched'
        assert audit_actions == [
            'storage_migration_create',
            'storage_migration_pause',
            'storage_migration_resume',
        ]
        assert (
            await StorageService.stat_on_storage(
                source_storage,
                object_key=str(source['object_key']),
            )
        ).size == len(payload)
        assert (
            await StorageService.stat_on_storage(
                target_storage,
                object_key=str(migrated['target_object_key']),
            )
        ).size == len(payload)

        async with async_db_session() as db:
            resolved = await hasn_asset_service.resolve(
                db,
                requester_hasn_id=owner,
                asset_ids=[stored.asset_id],
            )
        assert resolved[0].asset_id == stored.asset_id
        async with httpx.AsyncClient(timeout=30, trust_env=False) as client:
            response = await client.get(resolved[0].display_url)
            response.raise_for_status()
        assert response.content == payload

        rolled_back = await service.rollback_migration(
            owner_hasn_id=owner,
            job_id=str(job['job_id']),
            limit=10,
        )
        assert rolled_back == {'rolled_back': 1, 'remaining': 0}
        assert await service.process_jobs(job_type='orphan_cleanup', limit=10) == 1
        async with async_db_session() as db:
            current = (
                await db.execute(
                    text(
                        """
                        SELECT o.storage_id, o.object_key, i.verify_status
                        FROM hasn_storage_objects AS o
                        JOIN hasn_storage_migration_items AS i ON i.object_id = o.object_id
                        WHERE i.job_id = :job_id
                        """
                    ),
                    {'job_id': job['job_id']},
                )
            ).mappings().one()
        assert current['storage_id'] == source['storage_id']
        assert current['object_key'] == source['object_key']
        assert current['verify_status'] == 'rolled_back'
        with pytest.raises(errors.ServerError, match='S3 对象元数据读取失败'):
            await StorageService.stat_on_storage(
                target_storage,
                object_key=str(migrated['target_object_key']),
            )
        assert (
            await StorageService.stat_on_storage(
                source_storage,
                object_key=str(source['object_key']),
            )
        ).size == len(payload)
    finally:
        await _cleanup(owner, target_storage_id)


async def test_migration_routes_concurrent_writes_to_target_and_repeat_skips_converged_objects() -> None:
    """迁移窗口内的新写直接进入目标；重复迁移应跳过已收敛对象。"""
    owner, _user_id, target_storage_id = await _seed_owner_and_target()
    service = OwnerStorageService(async_db_session)
    initial_payload = f'migration-initial-{uuid.uuid4().hex}'.encode()
    concurrent_payload = f'migration-concurrent-{uuid.uuid4().hex}'.encode()
    try:
        await service.upload(
            owner_hasn_id=owner,
            chunks=_chunks(initial_payload),
            declared_size=len(initial_payload),
            filename='迁移前文件.txt',
            mime='text/plain',
            category='private_doc',
            source_app='migration_test',
            idempotency_key='migration-route-initial',
        )
        async with async_db_session.begin() as db:
            await db.execute(
                text("UPDATE s3_storage SET access = 'private' WHERE id = :storage_id"),
                {'storage_id': target_storage_id},
            )
        await service.create_migration(
            owner_hasn_id=owner,
            target_storage_by_access={'private': target_storage_id},
            observation_seconds=3600,
        )

        concurrent = await service.upload(
            owner_hasn_id=owner,
            chunks=_chunks(concurrent_payload),
            declared_size=len(concurrent_payload),
            filename='迁移中新增.txt',
            mime='text/plain',
            category='private_doc',
            source_app='migration_test',
            idempotency_key='migration-route-concurrent',
        )
        async with async_db_session() as db:
            concurrent_storage_id = (
                await db.execute(
                    text(
                        """
                        SELECT storage_id
                        FROM hasn_storage_objects
                        WHERE object_id = :object_id
                        """
                    ),
                    {'object_id': concurrent.object_id},
                )
            ).scalar_one()
        assert concurrent_storage_id == target_storage_id

        assert await service.process_jobs(
            job_type='storage_migration',
            limit=10,
            owner_hasn_id=owner,
        ) == 1
        repeated = await service.create_migration(
            owner_hasn_id=owner,
            target_storage_by_access={'private': target_storage_id},
            observation_seconds=3600,
        )
        assert repeated['status'] == 'succeeded'
        assert repeated['total_items'] == 0
    finally:
        await _cleanup(owner, target_storage_id)


async def test_migration_rejects_owner_with_inflight_upload_reservation() -> None:
    """迁移路由切换前必须等在途上传退出，避免快照遗漏仍写往源存储的对象。"""
    owner, _user_id, target_storage_id = await _seed_owner_and_target()
    service = OwnerStorageService(async_db_session)
    reservation = None
    try:
        async with async_db_session.begin() as db:
            await db.execute(
                text("UPDATE s3_storage SET access = 'private' WHERE id = :storage_id"),
                {'storage_id': target_storage_id},
            )
        reservation = await service.reserve(
            owner_hasn_id=owner,
            requested_bytes=128,
            idempotency_key='migration-inflight-reservation',
        )

        with pytest.raises(errors.ConflictError, match='STORAGE_MIGRATION_UPLOADS_IN_PROGRESS'):
            await service.create_migration(
                owner_hasn_id=owner,
                target_storage_by_access={'private': target_storage_id},
                observation_seconds=3600,
            )
    finally:
        if reservation is not None:
            await service.release_reservation(reservation.reservation_id)
        await _cleanup(owner, target_storage_id)


async def test_migration_source_is_deleted_only_after_observation_period() -> None:
    owner, _user_id, target_storage_id = await _seed_owner_and_target()
    service = OwnerStorageService(async_db_session)
    maintenance = OwnerStorageMaintenanceService(async_db_session)
    payload = f'real-storage-migration-cleanup-{uuid.uuid4().hex}'.encode()
    try:
        stored = await service.upload(
            owner_hasn_id=owner,
            chunks=_chunks(payload),
            declared_size=len(payload),
            filename='观察期文件.txt',
            mime='text/plain',
            category='private_doc',
            source_app='migration_test',
            idempotency_key='migration-cleanup-source',
        )
        async with async_db_session.begin() as db:
            await db.execute(
                text("UPDATE s3_storage SET access = 'private' WHERE id = :storage_id"),
                {'storage_id': target_storage_id},
            )
        job = await service.create_migration(
            owner_hasn_id=owner,
            target_storage_by_access={'private': target_storage_id},
            observation_seconds=3600,
        )
        assert await service.process_jobs(job_type='storage_migration', limit=10) == 1
        async with async_db_session.begin() as db:
            item = (
                await db.execute(
                    text(
                        """
                        SELECT source_storage_id, source_object_key,
                               target_storage_id, target_object_key
                        FROM hasn_storage_migration_items
                        WHERE job_id = :job_id
                        """
                    ),
                    {'job_id': job['job_id']},
                )
            ).mappings().one()
            source_storage = copy.copy(
                await StorageService.get_storage(db, int(item['source_storage_id']))
            )
            target_storage = copy.copy(
                await StorageService.get_storage(db, int(item['target_storage_id']))
            )
            await db.execute(
                text(
                    """
                    UPDATE hasn_storage_jobs
                    SET result = result || jsonb_build_object(
                        'observation_until',
                        now() - interval '1 minute'
                    )
                    WHERE job_id = :job_id
                    """
                ),
                {'job_id': job['job_id']},
            )

        report = await maintenance.sweep_migration_sources(
            owner_hasn_id=owner,
            limit=10,
        )

        assert report == {'checked': 1, 'deleted': 1, 'shared': 0}
        with pytest.raises(errors.ServerError, match='S3 对象元数据读取失败'):
            await StorageService.stat_on_storage(
                source_storage,
                object_key=str(item['source_object_key']),
            )
        assert (
            await StorageService.stat_on_storage(
                target_storage,
                object_key=str(item['target_object_key']),
            )
        ).size == len(payload)
        async with async_db_session() as db:
            row = (
                await db.execute(
                    text(
                        """
                        SELECT i.source_cleanup_status, i.source_deleted_time,
                               j.result
                        FROM hasn_storage_migration_items AS i
                        JOIN hasn_storage_jobs AS j ON j.job_id = i.job_id
                        WHERE i.job_id = :job_id
                          AND i.object_id = :object_id
                        """
                    ),
                    {'job_id': job['job_id'], 'object_id': stored.object_id},
                )
            ).mappings().one()
        assert row['source_cleanup_status'] == 'deleted'
        assert row['source_deleted_time'] is not None
        assert row['result']['source_cleanup_status'] == 'deleted'
        with pytest.raises(errors.ConflictError, match='STORAGE_MIGRATION_SOURCE_PURGED'):
            await service.rollback_migration(
                owner_hasn_id=owner,
                job_id=str(job['job_id']),
                limit=10,
            )
    finally:
        await _cleanup(owner, target_storage_id)


async def test_legacy_shared_source_is_retained_for_other_owner_after_migration() -> None:
    """迁移一个主人后，仍被另一主人引用的 legacy 源键不得被清理。"""
    owner_a, _user_a, target_a = await _seed_owner_and_target()
    owner_b, _user_b, target_b = await _seed_owner_and_target()
    service = OwnerStorageService(async_db_session)
    maintenance = OwnerStorageMaintenanceService(async_db_session)
    payload = f'legacy-shared-migration-{uuid.uuid4().hex}'.encode()
    try:
        asset_a = await service.upload(
            owner_hasn_id=owner_a,
            chunks=_chunks(payload),
            declared_size=len(payload),
            filename='共享旧对象-A.txt',
            mime='text/plain',
            category='private_doc',
            source_app='migration_test',
            idempotency_key='legacy-shared-a',
        )
        asset_b = await service.upload(
            owner_hasn_id=owner_b,
            chunks=_chunks(payload),
            declared_size=len(payload),
            filename='共享旧对象-B.txt',
            mime='text/plain',
            category='private_doc',
            source_app='migration_test',
            idempotency_key='legacy-shared-b',
        )
        async with async_db_session() as db:
            locations = (
                await db.execute(
                    text(
                        """
                        SELECT a.owner_hasn_id, o.object_id, o.storage_id, o.object_key
                        FROM hasn_assets AS a
                        JOIN hasn_storage_objects AS o ON o.object_id = a.object_id
                        WHERE a.asset_id IN (:asset_a, :asset_b)
                        """
                    ),
                    {'asset_a': asset_a.asset_id, 'asset_b': asset_b.asset_id},
                )
            ).mappings().all()
            by_owner = {str(row['owner_hasn_id']): row for row in locations}
            source = by_owner[owner_a]
            former_b = by_owner[owner_b]
            await StorageService.delete_object(
                db,
                storage_id=int(former_b['storage_id']),
                object_key=str(former_b['object_key']),
            )
        async with async_db_session.begin() as db:
            await db.execute(
                text(
                    """
                    UPDATE hasn_storage_objects
                    SET key_layout = 'legacy', updated_time = now()
                    WHERE object_id = :object_id
                    """
                ),
                {'object_id': str(source['object_id'])},
            )
            await db.execute(
                text(
                    """
                    UPDATE hasn_storage_objects
                    SET storage_id = :storage_id,
                        object_key = :object_key,
                        key_layout = 'legacy',
                        updated_time = now()
                    WHERE object_id = :object_id
                    """
                ),
                {
                    'storage_id': int(source['storage_id']),
                    'object_key': str(source['object_key']),
                    'object_id': str(former_b['object_id']),
                },
            )
            await db.execute(
                text(
                    """
                    UPDATE hasn_assets
                    SET storage_id = :storage_id,
                        object_key = :object_key,
                        updated_time = now()
                    WHERE owner_hasn_id = :owner AND asset_id = :asset_id
                    """
                ),
                {
                    'storage_id': int(source['storage_id']),
                    'object_key': str(source['object_key']),
                    'owner': owner_b,
                    'asset_id': asset_b.asset_id,
                },
            )
            await db.execute(
                text("UPDATE s3_storage SET access = 'private' WHERE id = :storage_id"),
                {'storage_id': target_a},
            )

        job = await service.create_migration(
            owner_hasn_id=owner_a,
            target_storage_by_access={'private': target_a},
            observation_seconds=3600,
        )
        assert await service.process_jobs(job_type='storage_migration', limit=10) == 1
        async with async_db_session.begin() as db:
            source_storage = copy.copy(
                await StorageService.get_storage(db, int(source['storage_id']))
            )
            await db.execute(
                text(
                    """
                    UPDATE hasn_storage_jobs
                    SET result = result || jsonb_build_object(
                        'observation_until',
                        now() - interval '1 minute'
                    )
                    WHERE job_id = :job_id
                    """
                ),
                {'job_id': job['job_id']},
            )

        report = await maintenance.sweep_migration_sources(
            owner_hasn_id=owner_a,
            limit=10,
        )

        assert report == {'checked': 1, 'deleted': 0, 'shared': 1}
        assert (
            await StorageService.stat_on_storage(
                source_storage,
                object_key=str(source['object_key']),
            )
        ).size == len(payload)
        async with async_db_session() as db:
            state = (
                await db.execute(
                    text(
                        """
                        SELECT i.source_cleanup_status, j.result
                        FROM hasn_storage_migration_items AS i
                        JOIN hasn_storage_jobs AS j ON j.job_id = i.job_id
                        WHERE i.job_id = :job_id
                        """
                    ),
                    {'job_id': job['job_id']},
                )
            ).mappings().one()
        assert state['source_cleanup_status'] == 'shared'
        assert state['result']['source_cleanup_status'] == 'shared_retained'
        assert state['result']['source_shared_items'] == 1
    finally:
        await _cleanup(owner_a, target_a)
        await _cleanup(owner_b, target_b)
