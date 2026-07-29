from __future__ import annotations

import uuid

import pytest

from sqlalchemy import text

from backend.app.hasn.service.hasn_asset_service import hasn_asset_service
from backend.app.hasn.service.owner_storage_maintenance_service import OwnerStorageMaintenanceService
from backend.app.hasn.service.owner_storage_service import OwnerStorageService
from backend.database.db import async_db_session
from backend.plugin.s3.service.storage_service import ObjectRef, StorageService

pytestmark = pytest.mark.asyncio


def _identity(label: str) -> tuple[str, int]:
    suffix = int(uuid.uuid4().hex[:10], 16)
    return f'h_backfill_{label}_{suffix:x}', 979_000_000 + suffix % 10_000_000


async def _seed_owner(owner: str, user_id: int) -> None:
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
                'star': f'bf{user_id}',
                'user_id': user_id,
                'nickname': f'回填测试_{owner[-12:]}',
            },
        )


async def _cleanup(owners: list[str], *, storage_id: int, object_key: str) -> None:
    async with async_db_session() as db:
        try:
            await StorageService.delete_object(db, storage_id=storage_id, object_key=object_key)
        except Exception:
            pass
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
                {'owners': owners},
            )
        await db.execute(text('DELETE FROM hasn_humans WHERE hasn_id = ANY(:owners)'), {'owners': owners})


async def test_legacy_backfill_is_idempotent_and_preserves_cross_owner_object() -> None:
    owner_a = _identity('a')
    owner_b = _identity('b')
    owners = [owner_a[0], owner_b[0]]
    await _seed_owner(*owner_a)
    await _seed_owner(*owner_b)
    payload = f'legacy-shared-{uuid.uuid4().hex}'.encode()
    object_key = f'dm/legacy-tests/{uuid.uuid4().hex}.txt'
    async with async_db_session() as db:
        ref = await StorageService.upload(
            db,
            payload,
            category='dm_attachment',
            filename='历史资料.txt',
            content_type='text/plain',
            key=object_key,
        )

    asset_ids = [f'ast_{uuid.uuid4().hex}' for _ in range(3)]
    try:
        async with async_db_session.begin() as db:
            await db.execute(
                text(
                    """
                    INSERT INTO hasn_assets
                        (asset_id, owner_hasn_id, access, storage_id, object_key, kind, mime,
                         size_bytes, extract_status, created_time, updated_time)
                    VALUES
                        (:a1, :owner_a, 'private', :storage_id, :object_key, 'file', 'text/plain',
                         :size, 'done', now(), now()),
                        (:a2, :owner_a, 'private', :storage_id, :object_key, 'file', 'text/plain',
                         :size, 'done', now(), now()),
                        (:b1, :owner_b, 'private', :storage_id, :object_key, 'file', 'text/plain',
                         :size, 'done', now(), now())
                    """
                ),
                {
                    'a1': asset_ids[0],
                    'a2': asset_ids[1],
                    'b1': asset_ids[2],
                    'owner_a': owner_a[0],
                    'owner_b': owner_b[0],
                    'storage_id': ref.storage_id,
                    'object_key': ref.object_key,
                    'size': len(payload),
                },
            )

        async with async_db_session() as db:
            legacy_detail = await hasn_asset_service.get_by_asset_id(db, asset_ids[0])
            legacy_many = await hasn_asset_service.get_many(db, asset_ids)
            legacy_location = await hasn_asset_service.get_by_storage_location(
                db,
                storage_id=ref.storage_id,
                object_key=ref.object_key,
            )
        assert legacy_detail is not None
        assert legacy_detail.object_key == ref.object_key
        assert set(legacy_many) == set(asset_ids)
        assert {asset.asset_id for asset in legacy_location} == set(asset_ids)

        maintenance = OwnerStorageMaintenanceService(async_db_session)
        first = await maintenance.backfill_legacy_assets(
            owner_hasn_ids=owners,
            verify_objects=True,
        )
        second = await maintenance.backfill_legacy_assets(
            owner_hasn_ids=owners,
            verify_objects=True,
        )
        assert first['assets_backfilled'] == 3
        assert first['objects_created'] == 2
        assert first['shared_legacy_locations'] == 1
        assert second['assets_backfilled'] == 0
        assert second['objects_created'] == 0

        async with async_db_session() as db:
            objects = (
                await db.execute(
                    text(
                        """
                        SELECT owner_hasn_id, object_id, ref_count, size_bytes, sha256, key_layout, state
                        FROM hasn_storage_objects
                        WHERE owner_hasn_id = ANY(:owners)
                        ORDER BY owner_hasn_id
                        """
                    ),
                    {'owners': owners},
                )
            ).mappings().all()
            accounts = (
                await db.execute(
                    text(
                        """
                        SELECT owner_hasn_id, used_bytes, reserved_bytes
                        FROM hasn_storage_accounts
                        WHERE owner_hasn_id = ANY(:owners)
                        ORDER BY owner_hasn_id
                        """
                    ),
                    {'owners': owners},
                )
            ).mappings().all()
            entries = (
                await db.execute(
                    text(
                        """
                        SELECT owner_hasn_id, asset_id, display_name
                        FROM hasn_storage_entries
                        WHERE owner_hasn_id = ANY(:owners)
                        ORDER BY owner_hasn_id, display_name
                        """
                    ),
                    {'owners': owners},
                )
            ).mappings().all()
        assert len(objects) == 2
        assert {str(row['key_layout']) for row in objects} == {'legacy'}
        assert {str(row['state']) for row in objects} == {'active'}
        assert all(row['sha256'] for row in objects)
        refs = {str(row['owner_hasn_id']): int(row['ref_count']) for row in objects}
        assert refs == {owner_a[0]: 2, owner_b[0]: 1}
        assert {str(row['owner_hasn_id']): int(row['used_bytes']) for row in accounts} == {
            owner_a[0]: len(payload),
            owner_b[0]: len(payload),
        }
        assert all(int(row['reserved_bytes']) == 0 for row in accounts)
        assert len(entries) == 3
        assert len({(str(row['owner_hasn_id']), str(row['display_name'])) for row in entries}) == 3

        lifecycle = OwnerStorageService(async_db_session)
        await lifecycle.delete_asset(owner_hasn_id=owner_a[0], asset_id=asset_ids[0], cascade=True)
        await lifecycle.delete_asset(owner_hasn_id=owner_a[0], asset_id=asset_ids[1], cascade=True)
        await lifecycle.process_jobs(limit=10, job_type='object_purge')
        async with async_db_session() as db:
            assert (await StorageService.stat(db, storage_id=ref.storage_id, object_key=ref.object_key)).size == len(
                payload
            )
        await lifecycle.delete_asset(owner_hasn_id=owner_b[0], asset_id=asset_ids[2], cascade=True)
        await lifecycle.process_jobs(limit=10, job_type='object_purge')
        async with async_db_session() as db:
            with pytest.raises(Exception):
                await StorageService.stat(db, storage_id=ref.storage_id, object_key=ref.object_key)
    finally:
        await _cleanup(owners, storage_id=ref.storage_id, object_key=ref.object_key)


async def test_legacy_backfill_reports_owner_without_identity_without_forging_quota() -> None:
    owner = f'h_backfill_missing_{uuid.uuid4().hex[:16]}'
    asset_id = f'ast_{uuid.uuid4().hex}'
    object_key = f'dm/missing-owner/{uuid.uuid4().hex}.bin'
    async with async_db_session.begin() as db:
        await db.execute(
            text(
                """
                INSERT INTO hasn_assets
                    (asset_id, owner_hasn_id, access, storage_id, object_key, kind, mime,
                     size_bytes, extract_status, created_time, updated_time)
                VALUES
                    (:asset_id, :owner, 'private', 1, :object_key, 'file',
                     'application/octet-stream', 17, 'done', now(), now())
                """
            ),
            {'asset_id': asset_id, 'owner': owner, 'object_key': object_key},
        )
    try:
        report = await OwnerStorageMaintenanceService(async_db_session).backfill_legacy_assets(
            owner_hasn_ids=[owner],
            verify_objects=False,
        )
        assert report['assets_backfilled'] == 1
        assert report['owners_without_identity'] == 1
        assert report['unresolved_owner_hasn_ids'] == [owner]
        async with async_db_session() as db:
            account = (
                await db.execute(
                    text('SELECT 1 FROM hasn_storage_accounts WHERE owner_hasn_id = :owner'),
                    {'owner': owner},
                )
            ).scalar_one_or_none()
        assert account is None
    finally:
        async with async_db_session.begin() as db:
            for table in (
                'hasn_storage_entries',
                'hasn_assets',
                'hasn_storage_objects',
            ):
                await db.execute(
                    text(f'DELETE FROM {table} WHERE owner_hasn_id = :owner'),
                    {'owner': owner},
                )


async def test_legacy_hash_verification_merges_same_owner_duplicate_locations() -> None:
    owner = _identity('duplicate')
    await _seed_owner(*owner)
    payload = f'legacy-duplicate-{uuid.uuid4().hex}'.encode()
    keys = [
        f'published/legacy-tests/{uuid.uuid4().hex}.bin',
        f'published/legacy-tests/{uuid.uuid4().hex}.bin',
    ]
    refs: list[ObjectRef] = []
    async with async_db_session() as db:
        for key in keys:
            refs.append(
                await StorageService.upload(
                    db,
                    payload,
                    category='published_artifact',
                    filename='重复制品.bin',
                    content_type='application/octet-stream',
                    key=key,
                )
            )
    asset_ids = [f'ast_{uuid.uuid4().hex}' for _ in keys]
    try:
        async with async_db_session.begin() as db:
            for asset_id, ref in zip(asset_ids, refs, strict=True):
                await db.execute(
                    text(
                        """
                        INSERT INTO hasn_assets
                            (asset_id, owner_hasn_id, access, storage_id, object_key, kind, mime,
                             size_bytes, extract_status, created_time, updated_time)
                        VALUES
                            (:asset_id, :owner, 'private', :storage_id, :object_key, 'file',
                             'application/octet-stream', :size, 'done', now(), now())
                        """
                    ),
                    {
                        'asset_id': asset_id,
                        'owner': owner[0],
                        'storage_id': ref.storage_id,
                        'object_key': ref.object_key,
                        'size': len(payload),
                    },
                )

        report = await OwnerStorageMaintenanceService(async_db_session).backfill_legacy_assets(
            owner_hasn_ids=[owner[0]],
            verify_objects=True,
        )
        assert report['objects_created'] == 2
        assert report['objects_verified'] == 1
        assert report['objects_merged'] == 1
        async with async_db_session() as db:
            before_purge = (
                await db.execute(
                    text(
                        """
                        SELECT state, COUNT(*) AS count
                        FROM hasn_storage_objects
                        WHERE owner_hasn_id = :owner
                        GROUP BY state
                        """
                    ),
                    {'owner': owner[0]},
                )
            ).mappings().all()
            object_ids = set(
                (
                    await db.execute(
                        text('SELECT object_id FROM hasn_assets WHERE owner_hasn_id = :owner'),
                        {'owner': owner[0]},
                    )
                )
                .scalars()
                .all()
            )
        assert {str(row['state']): int(row['count']) for row in before_purge} == {
            'active': 1,
            'deleting': 1,
        }
        assert len(object_ids) == 1

        await OwnerStorageService(async_db_session).process_jobs(limit=10, job_type='object_purge')
        async with async_db_session() as db:
            account_used = (
                await db.execute(
                    text('SELECT used_bytes FROM hasn_storage_accounts WHERE owner_hasn_id = :owner'),
                    {'owner': owner[0]},
                )
            ).scalar_one()
            states = (
                await db.execute(
                    text(
                        """
                        SELECT state, COUNT(*) AS count
                        FROM hasn_storage_objects
                        WHERE owner_hasn_id = :owner
                        GROUP BY state
                        """
                    ),
                    {'owner': owner[0]},
                )
            ).mappings().all()
        assert int(account_used) == len(payload)
        assert {str(row['state']): int(row['count']) for row in states} == {
            'active': 1,
            'deleted': 1,
        }
    finally:
        await _cleanup([owner[0]], storage_id=refs[0].storage_id, object_key=refs[0].object_key)
        async with async_db_session() as db:
            try:
                await StorageService.delete_object(
                    db,
                    storage_id=refs[1].storage_id,
                    object_key=refs[1].object_key,
                )
            except Exception:
                pass
