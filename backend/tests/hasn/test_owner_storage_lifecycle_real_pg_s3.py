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


async def _seed_owner() -> tuple[str, int]:
    suffix = int(uuid.uuid4().hex[:10], 16)
    owner = f'h_lifecycle_{suffix:x}'
    user_id = 965_000_000 + suffix % 10_000_000
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
                'star': f'lc{user_id}',
                'user_id': user_id,
                'nickname': f'生命周期测试_{owner[-12:]}',
            },
        )
        await db.execute(
            text(
                """
                INSERT INTO hasn_storage_accounts
                    (owner_hasn_id, quota_bytes, used_bytes, reserved_bytes, quota_source,
                     quota_version, quota_valid_until, state, created_time)
                VALUES
                    (:owner, 10485760, 0, 0, 'admin_override', 'lifecycle-test',
                     now() + interval '1 hour', 'active', now())
                """
            ),
            {'owner': owner},
        )
    return owner, user_id


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
        await db.execute(
            text('DELETE FROM hasn_artifacts WHERE owner_hasn_id = :owner'),
            {'owner': owner},
        )
        for table in (
            'hasn_storage_entries',
            'hasn_asset_bindings',
            'hasn_assets',
            'hasn_storage_objects',
            'hasn_storage_reservations',
            'hasn_storage_jobs',
            'hasn_storage_accounts',
        ):
            await db.execute(text(f'DELETE FROM {table} WHERE owner_hasn_id = :owner'), {'owner': owner})  # noqa: S608
        await db.execute(text('DELETE FROM hasn_humans WHERE hasn_id = :owner'), {'owner': owner})


async def test_cascade_delete_is_an_over_quota_self_rescue_path() -> None:
    owner, _ = await _seed_owner()
    service = OwnerStorageService(async_db_session)
    payload = f'lifecycle-real-{uuid.uuid4().hex}'.encode()
    try:
        first = await service.upload(
            owner_hasn_id=owner,
            chunks=_chunks(payload),
            declared_size=len(payload),
            filename='被引用一.txt',
            mime='text/plain',
            category='user_upload',
            source_app='lifecycle_test',
            idempotency_key='lifecycle-first',
        )
        second = await service.upload(
            owner_hasn_id=owner,
            chunks=_chunks(payload),
            declared_size=len(payload),
            filename='被引用二.txt',
            mime='text/plain',
            category='user_upload',
            source_app='lifecycle_test',
            idempotency_key='lifecycle-second',
        )
        first_artifact_id = f'art_{uuid.uuid4().hex[:24]}'
        second_artifact_id = f'art_{uuid.uuid4().hex[:24]}'
        async with async_db_session.begin() as db:
            await db.execute(
                text(
                    """
                    INSERT INTO hasn_artifacts
                        (artifact_id, agent_hasn_id, owner_hasn_id, artifact_key,
                         artifact_kind, kind, asset_id, source_kind, action, metadata,
                         status, created_time)
                    VALUES
                        (:first_artifact_id, :first_agent, :owner, :first_key,
                         'file', 'file', :first_asset_id, 'platform_tool', 'create',
                         '{}'::jsonb, 'active', now()),
                        (:second_artifact_id, :second_agent, :owner, :second_key,
                         'file', 'file', :second_asset_id, 'platform_tool', 'create',
                         '{}'::jsonb, 'active', now())
                    """
                ),
                {
                    'first_artifact_id': first_artifact_id,
                    'first_agent': f'a_{uuid.uuid4().hex[:20]}',
                    'owner': owner,
                    'first_key': f'asset:{first.asset_id}',
                    'first_asset_id': first.asset_id,
                    'second_artifact_id': second_artifact_id,
                    'second_agent': f'a_{uuid.uuid4().hex[:20]}',
                    'second_key': f'asset:{second.asset_id}',
                    'second_asset_id': second.asset_id,
                },
            )
        await service.bind_asset(
            owner_hasn_id=owner,
            asset_id=first.asset_id,
            resource_uri=f'hasn://artifact/{first_artifact_id}',
            role='source',
        )
        await service.bind_asset(
            owner_hasn_id=owner,
            asset_id=second.asset_id,
            resource_uri=f'hasn://artifact/{second_artifact_id}',
            role='source',
        )

        await service.trash_asset(owner_hasn_id=owner, asset_id=first.asset_id)
        assert (await service.usage(owner_hasn_id=owner)).used_bytes == len(payload)
        await service.restore_asset(owner_hasn_id=owner, asset_id=first.asset_id)

        refs = await service.asset_references(owner_hasn_id=owner, asset_id=first.asset_id)
        assert refs == [
            {
                'binding_id': refs[0]['binding_id'],
                'resource_uri': f'hasn://artifact/{first_artifact_id}',
                'role': 'source',
                'status': 'active',
            }
        ]
        with pytest.raises(errors.ConflictError, match='STORAGE_ASSET_IN_USE') as conflict:
            await service.delete_asset(owner_hasn_id=owner, asset_id=first.asset_id, cascade=False)
        assert conflict.value.data['references'][0]['resource_uri'] == (
            f'hasn://artifact/{first_artifact_id}'
        )

        async with async_db_session.begin() as db:
            await db.execute(
                text(
                    """
                    UPDATE hasn_storage_accounts
                    SET quota_bytes = :quota, state = 'over_quota'
                    WHERE owner_hasn_id = :owner
                    """
                ),
                {'owner': owner, 'quota': len(payload) - 1},
            )
        with pytest.raises(errors.RequestError, match='STORAGE_QUOTA_EXCEEDED'):
            await service.upload(
                owner_hasn_id=owner,
                chunks=_chunks(b'x'),
                declared_size=1,
                filename='超限.txt',
                mime='text/plain',
                category='user_upload',
                source_app='lifecycle_test',
                idempotency_key='over-quota-before-delete',
            )

        first_deleted = await service.delete_asset(owner_hasn_id=owner, asset_id=first.asset_id, cascade=True)
        assert first_deleted == {'asset_id': first.asset_id, 'state': 'deleted', 'purge_job_id': None}
        assert (await service.usage(owner_hasn_id=owner)).used_bytes == len(payload)

        second_deleted = await service.delete_asset(owner_hasn_id=owner, asset_id=second.asset_id, cascade=True)
        assert second_deleted['state'] == 'deleting'
        assert second_deleted['purge_job_id']
        assert (await service.usage(owner_hasn_id=owner)).used_bytes == len(payload)

        processed = await service.process_jobs(
            job_type='object_purge',
            limit=10,
            owner_hasn_id=owner,
        )
        assert processed == 1
        usage = await service.usage(owner_hasn_id=owner)
        assert usage.used_bytes == 0
        assert usage.state == 'active'

        async with async_db_session() as db:
            row = (
                await db.execute(
                    text(
                        """
                        SELECT o.state,
                               COUNT(*) FILTER (WHERE a.lifecycle_status = 'deleted') AS deleted_assets,
                               COUNT(*) FILTER (WHERE b.status = 'deleted') AS deleted_bindings
                        FROM hasn_storage_objects AS o
                        JOIN hasn_assets AS a ON a.object_id = o.object_id
                        LEFT JOIN hasn_asset_bindings AS b ON b.asset_id = a.asset_id
                        WHERE o.object_id = :object_id
                        GROUP BY o.state
                        """
                    ),
                    {'object_id': first.object_id},
                )
            ).mappings().one()
        assert row['state'] == 'deleted'
        assert row['deleted_assets'] == 2
        assert row['deleted_bindings'] == 2
    finally:
        await _cleanup(owner)
