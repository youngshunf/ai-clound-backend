from __future__ import annotations

import uuid

import pytest

from sqlalchemy import text

from backend.app.hasn.service.owner_storage_service import OwnerStorageService
from backend.common.exception import errors
from backend.database.db import async_db_session
from backend.plugin.s3.service.storage_service import StorageService

pytestmark = pytest.mark.asyncio


async def _chunks(payload: bytes):
    yield payload


async def _seed_owner() -> str:
    suffix = int(uuid.uuid4().hex[:10], 16)
    owner = f'h_save_{suffix:x}'
    user_id = 985_000_000 + suffix % 10_000_000
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
                'star': f'sv{user_id}',
                'user_id': user_id,
                'nickname': f'转存测试_{owner[-12:]}',
            },
        )
        await db.execute(
            text(
                """
                INSERT INTO hasn_storage_accounts
                    (owner_hasn_id, quota_bytes, used_bytes, reserved_bytes, quota_source,
                     quota_version, quota_valid_until, state, created_time)
                VALUES
                    (:owner, 10485760, 0, 0, 'admin_override', 'save-test',
                     now() + interval '1 hour', 'active', now())
                """
            ),
            {'owner': owner},
        )
    return owner


async def _cleanup(owner: str) -> None:
    async with async_db_session() as db:
        objects = (
            await db.execute(
                text('SELECT storage_id, object_key FROM hasn_storage_objects WHERE owner_hasn_id = :owner'),
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
        for table in (
            'hasn_storage_entries',
            'hasn_assets',
            'hasn_storage_objects',
            'hasn_storage_reservations',
            'hasn_storage_jobs',
            'hasn_storage_accounts',
        ):
            await db.execute(text(f'DELETE FROM {table} WHERE owner_hasn_id = :owner'), {'owner': owner})
        await db.execute(text('DELETE FROM hasn_humans WHERE hasn_id = :owner'), {'owner': owner})


async def test_public_asset_save_is_an_independent_cross_owner_copy(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv('OWNER_SCOPE_SALT', f'save-test-salt-{uuid.uuid4().hex}')
    source_owner = await _seed_owner()
    target_owner = await _seed_owner()
    service = OwnerStorageService(async_db_session)
    payload = f'public-save-real-{uuid.uuid4().hex}'.encode()
    try:
        public_source = await service.upload(
            owner_hasn_id=source_owner,
            chunks=_chunks(payload),
            declared_size=len(payload),
            filename='公开源.png',
            mime='image/png',
            category='post_image',
            source_app='save_test',
            idempotency_key='save-public-source',
        )
        private_source = await service.upload(
            owner_hasn_id=source_owner,
            chunks=_chunks(b'private-source'),
            declared_size=14,
            filename='私有源.txt',
            mime='text/plain',
            category='private_doc',
            source_app='save_test',
            idempotency_key='save-private-source',
        )

        with pytest.raises(errors.NotFoundError, match='STORAGE_ASSET_NOT_FOUND'):
            await service.save_to_my_storage(
                owner_hasn_id=target_owner,
                source_asset_id=private_source.asset_id,
                idempotency_key='save-private-forbidden',
                parent_entry_id=None,
                display_name=None,
            )

        saved = await service.save_to_my_storage(
            owner_hasn_id=target_owner,
            source_asset_id=public_source.asset_id,
            idempotency_key='save-public-target',
            parent_entry_id=None,
            display_name='我的副本.png',
        )
        replay = await service.save_to_my_storage(
            owner_hasn_id=target_owner,
            source_asset_id=public_source.asset_id,
            idempotency_key='save-public-target',
            parent_entry_id=None,
            display_name='我的副本.png',
        )
        assert replay.asset_id == saved.asset_id
        assert saved.asset_id != public_source.asset_id
        assert saved.object_id != public_source.object_id
        assert saved.display_name == '我的副本.png'

        async with async_db_session() as db:
            rows = (
                await db.execute(
                    text(
                        """
                        SELECT owner_hasn_id, object_id, storage_id, object_key, access,
                               size_bytes, sha256, ref_count, state
                        FROM hasn_storage_objects
                        WHERE object_id IN (:source_object, :saved_object)
                        ORDER BY owner_hasn_id
                        """
                    ),
                    {
                        'source_object': public_source.object_id,
                        'saved_object': saved.object_id,
                    },
                )
            ).mappings().all()
        assert len(rows) == 2
        assert rows[0]['owner_hasn_id'] != rows[1]['owner_hasn_id']
        assert rows[0]['object_key'] != rows[1]['object_key']
        assert rows[0]['sha256'] == rows[1]['sha256']
        assert all(row['ref_count'] == 1 and row['state'] == 'active' for row in rows)
        assert (await service.usage(owner_hasn_id=target_owner)).used_bytes == len(payload)

        alternate_source = await service.upload(
            owner_hasn_id=source_owner,
            chunks=_chunks(payload[::-1]),
            declared_size=len(payload),
            filename='另一份公开源.png',
            mime='image/png',
            category='post_image',
            source_app='save_test',
            idempotency_key='save-public-source-alternate',
        )
        with pytest.raises(errors.ConflictError, match='STORAGE_IDEMPOTENCY_CONFLICT'):
            await service.save_to_my_storage(
                owner_hasn_id=target_owner,
                source_asset_id=alternate_source.asset_id,
                idempotency_key='save-public-target',
                parent_entry_id=None,
                display_name='我的副本.png',
            )

        await service.delete_asset(
            owner_hasn_id=source_owner,
            asset_id=public_source.asset_id,
            cascade=True,
        )
        assert await service.process_jobs(job_type='object_purge', limit=10) == 1
        async with async_db_session() as db:
            target_stat = await db.execute(
                text(
                    """
                    SELECT storage_id, object_key
                    FROM hasn_storage_objects
                    WHERE object_id = :object_id AND state = 'active'
                    """
                ),
                {'object_id': saved.object_id},
            )
            target = target_stat.mappings().one()
            stat = await StorageService.stat(
                db,
                storage_id=int(target['storage_id']),
                object_key=str(target['object_key']),
            )
        assert stat.size == len(payload)
    finally:
        await _cleanup(source_owner)
        await _cleanup(target_owner)
