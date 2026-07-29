from __future__ import annotations

import hashlib
import tempfile
import uuid

import pytest

from sqlalchemy import text

from backend.app.hasn.service.owner_storage_maintenance_service import OwnerStorageMaintenanceService
from backend.app.hasn.service.owner_storage_service import OwnerStorageService
from backend.database.db import async_db_session
from backend.plugin.s3.service.storage_service import StorageService

pytestmark = pytest.mark.asyncio

_PART_SIZE = 5 * 1024 * 1024


def _identity(label: str) -> tuple[str, int]:
    suffix = int(uuid.uuid4().hex[:10], 16)
    return f'h_storage_multipart_{label}_{suffix:x}', 980_000_000 + suffix % 10_000_000


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
                'star': f'mp{user_id}',
                'user_id': user_id,
                'nickname': f'分片上传测试_{owner[-12:]}',
            },
        )
        await db.execute(
            text(
                """
                INSERT INTO hasn_storage_accounts
                    (owner_hasn_id, quota_bytes, used_bytes, reserved_bytes, quota_source,
                     quota_version, quota_valid_until, state, created_time)
                VALUES
                    (:owner, :quota, 0, 0, 'admin_override', 'multipart-real-s3',
                     now() + interval '1 hour', 'active', now())
                """
            ),
            {'owner': owner, 'quota': 32 * 1024 * 1024},
        )


async def _cleanup_owner(owner: str) -> None:
    async with async_db_session() as db:
        jobs = (
            await db.execute(
                text(
                    """
                    SELECT payload
                    FROM hasn_storage_jobs
                    WHERE owner_hasn_id = :owner AND job_type = 'multipart_abort_sweep'
                    """
                ),
                {'owner': owner},
            )
        ).mappings().all()
        for job in jobs:
            payload = dict(job['payload'])
            if payload.get('provider_upload_id') and payload.get('storage_id') and payload.get('object_key'):
                storage = await StorageService.get_storage(db, int(payload['storage_id']))
                try:
                    await StorageService.abort_multipart_on_storage(
                        storage,
                        object_key=str(payload['object_key']),
                        upload_id=str(payload['provider_upload_id']),
                    )
                except Exception:
                    pass
        objects = (
            await db.execute(
                text(
                    """
                    SELECT storage_id, object_key
                    FROM hasn_storage_objects
                    WHERE owner_hasn_id = :owner
                    """
                ),
                {'owner': owner},
            )
        ).mappings().all()
        for obj in objects:
            storage = await StorageService.get_storage(db, int(obj['storage_id']))
            await StorageService.delete_on_storage(storage, object_key=str(obj['object_key']))

    async with async_db_session.begin() as db:
        for table in (
            'hasn_storage_entries',
            'hasn_asset_bindings',
            'hasn_assets',
            'hasn_storage_objects',
            'hasn_storage_jobs',
            'hasn_storage_reservations',
            'hasn_storage_accounts',
        ):
            await db.execute(
                text(f'DELETE FROM {table} WHERE owner_hasn_id = :owner'),
                {'owner': owner},
            )
        await db.execute(text('DELETE FROM hasn_humans WHERE hasn_id = :owner'), {'owner': owner})


def _part_file(data: bytes):
    file = tempfile.SpooledTemporaryFile(max_size=_PART_SIZE + 1)
    file.write(data)
    file.seek(0)
    return file


async def test_real_multipart_upload_calibrates_and_commits() -> None:
    owner = _identity('complete')
    await _seed_owner(*owner)
    service = OwnerStorageService(async_db_session)
    part_one = b'a' * _PART_SIZE
    part_two = f'真实分片-{uuid.uuid4().hex}'.encode()
    payload = part_one + part_two
    try:
        session = await service.start_multipart(
            owner_hasn_id=owner[0],
            declared_size=len(payload),
            filename='大文件.bin',
            mime='application/octet-stream',
            category='user_upload',
            source_app='multipart_real_test',
            idempotency_key='multipart-complete',
        )
        with _part_file(part_one) as file:
            await service.upload_multipart_part(
                owner_hasn_id=owner[0],
                upload_id=session['upload_id'],
                part_number=1,
                file=file,
                size=len(part_one),
            )
        with _part_file(part_two) as file:
            await service.upload_multipart_part(
                owner_hasn_id=owner[0],
                upload_id=session['upload_id'],
                part_number=2,
                file=file,
                size=len(part_two),
            )

        stored = await service.complete_multipart(
            owner_hasn_id=owner[0],
            upload_id=session['upload_id'],
        )

        async with async_db_session() as db:
            obj = (
                await db.execute(
                    text(
                        """
                        SELECT o.storage_id, o.object_key, o.sha256, o.size_bytes, j.status
                        FROM hasn_storage_objects AS o
                        JOIN hasn_storage_jobs AS j
                          ON j.owner_hasn_id = o.owner_hasn_id
                         AND j.job_id = :job_id
                        WHERE o.object_id = :object_id
                        """
                    ),
                    {'job_id': session['upload_id'], 'object_id': stored.object_id},
                )
            ).mappings().one()
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
        assert obj['sha256'] == hashlib.sha256(payload).hexdigest()
        assert obj['size_bytes'] == len(payload)
        assert obj['status'] == 'succeeded'
        assert account['used_bytes'] == len(payload)
        assert account['reserved_bytes'] == 0
    finally:
        await _cleanup_owner(owner[0])


async def test_multipart_job_update_failure_cannot_leave_live_asset_targeted_as_orphan() -> None:
    """完成作业状态与资产提交必须原子，失败补偿不得指向仍活跃的对象。"""
    owner = _identity('jobfail')
    await _seed_owner(*owner)
    service = OwnerStorageService(async_db_session)
    payload = f'分片作业原子性-{uuid.uuid4().hex}'.encode()
    function_name = f'test_fail_multipart_job_{uuid.uuid4().hex}'
    trigger_name = f'trg_fail_multipart_job_{uuid.uuid4().hex}'
    session: dict[str, object] | None = None
    try:
        session = await service.start_multipart(
            owner_hasn_id=owner[0],
            declared_size=len(payload),
            filename='原子提交.txt',
            mime='text/plain',
            category='user_upload',
            source_app='multipart_real_test',
            idempotency_key='multipart-job-commit-failure',
        )
        with _part_file(payload) as file:
            await service.upload_multipart_part(
                owner_hasn_id=owner[0],
                upload_id=str(session['upload_id']),
                part_number=1,
                file=file,
                size=len(payload),
            )

        async with async_db_session.begin() as db:
            await db.execute(
                text(
                    f"""
                    CREATE FUNCTION {function_name}() RETURNS trigger
                    LANGUAGE plpgsql AS $$
                    BEGIN
                        IF NEW.job_id = '{session["upload_id"]}'
                           AND NEW.status = 'succeeded' THEN
                            RAISE EXCEPTION '测试注入 multipart 作业完成写失败';
                        END IF;
                        RETURN NEW;
                    END;
                    $$
                    """
                )
            )
            await db.execute(
                text(
                    f"""
                    CREATE TRIGGER {trigger_name}
                    BEFORE UPDATE ON hasn_storage_jobs
                    FOR EACH ROW EXECUTE FUNCTION {function_name}()
                    """
                )
            )

        with pytest.raises(Exception, match='测试注入 multipart 作业完成写失败'):
            await service.complete_multipart(
                owner_hasn_id=owner[0],
                upload_id=str(session['upload_id']),
            )

        async with async_db_session() as db:
            live_assets = (
                await db.execute(
                    text(
                        """
                        SELECT COUNT(*)
                        FROM hasn_assets
                        WHERE owner_hasn_id = :owner
                          AND upload_idempotency_key = 'multipart-job-commit-failure'
                          AND lifecycle_status = 'active'
                        """
                    ),
                    {'owner': owner[0]},
                )
            ).scalar_one()
            orphan_targeting_live = (
                await db.execute(
                    text(
                        """
                        SELECT COUNT(*)
                        FROM hasn_storage_jobs AS j
                        JOIN hasn_storage_objects AS o
                          ON o.owner_hasn_id = j.owner_hasn_id
                         AND o.storage_id = (j.payload ->> 'storage_id')::bigint
                         AND o.object_key = j.payload ->> 'object_key'
                        WHERE j.owner_hasn_id = :owner
                          AND j.job_type = 'orphan_cleanup'
                          AND o.state = 'active'
                        """
                    ),
                    {'owner': owner[0]},
                )
            ).scalar_one()
        assert live_assets == 0
        assert orphan_targeting_live == 0
    finally:
        async with async_db_session.begin() as db:
            await db.execute(text(f'DROP TRIGGER IF EXISTS {trigger_name} ON hasn_storage_jobs'))
            await db.execute(text(f'DROP FUNCTION IF EXISTS {function_name}()'))
        await _cleanup_owner(owner[0])


async def test_expired_multipart_is_aborted_and_releases_reservation() -> None:
    owner = _identity('abort')
    await _seed_owner(*owner)
    service = OwnerStorageService(async_db_session)
    maintenance = OwnerStorageMaintenanceService(async_db_session)
    part = b'b' * _PART_SIZE
    try:
        session = await service.start_multipart(
            owner_hasn_id=owner[0],
            declared_size=_PART_SIZE + 1024,
            filename='会超时.bin',
            mime='application/octet-stream',
            category='user_upload',
            source_app='multipart_real_test',
            idempotency_key='multipart-timeout',
        )
        with _part_file(part) as file:
            await service.upload_multipart_part(
                owner_hasn_id=owner[0],
                upload_id=session['upload_id'],
                part_number=1,
                file=file,
                size=len(part),
            )
        async with async_db_session.begin() as db:
            await db.execute(
                text(
                    """
                    UPDATE hasn_storage_jobs
                    SET expires_time = now() - interval '1 minute'
                    WHERE job_id = :job_id
                    """
                ),
                {'job_id': session['upload_id']},
            )

        report = await maintenance.sweep_expired_multipart(
            owner_hasn_id=owner[0],
            limit=10,
        )

        assert report == {'checked': 1, 'aborted': 1, 'failed': 0}
        async with async_db_session() as db:
            job_status = (
                await db.execute(
                    text('SELECT status FROM hasn_storage_jobs WHERE job_id = :job_id'),
                    {'job_id': session['upload_id']},
                )
            ).scalar_one()
            reservation_status = (
                await db.execute(
                    text(
                        """
                        SELECT status
                        FROM hasn_storage_reservations
                        WHERE reservation_id = :reservation_id
                        """
                    ),
                    {'reservation_id': session['reservation_id']},
                )
            ).scalar_one()
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
        assert job_status == 'cancelled'
        assert reservation_status == 'released'
        assert account == {'used_bytes': 0, 'reserved_bytes': 0}
    finally:
        await _cleanup_owner(owner[0])
