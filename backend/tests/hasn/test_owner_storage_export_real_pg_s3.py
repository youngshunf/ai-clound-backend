from __future__ import annotations

import json
import uuid

import httpx
import pytest

from sqlalchemy import text

from backend.app.hasn.service.owner_storage_maintenance_service import (
    OwnerStorageMaintenanceService,
)
from backend.app.hasn.service.owner_storage_service import OwnerStorageService
from backend.common.exception import errors
from backend.database.db import async_db_session
from backend.plugin.s3.service.storage_service import StorageService

pytestmark = pytest.mark.asyncio


async def _chunks(payload: bytes):
    yield payload


async def _seed_owner() -> str:
    suffix = int(uuid.uuid4().hex[:10], 16)
    owner = f'h_export_{suffix:x}'
    user_id = 995_000_000 + suffix % 4_000_000
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
                'star': f'ex{user_id}',
                'user_id': user_id,
                'nickname': f'导出测试_{owner[-12:]}',
            },
        )
        await db.execute(
            text(
                """
                INSERT INTO hasn_storage_accounts
                    (owner_hasn_id, quota_bytes, used_bytes, reserved_bytes, quota_source,
                     quota_version, quota_valid_until, state, created_time)
                VALUES
                    (:owner, 10485760, 0, 0, 'admin_override', 'export-test',
                     now() + interval '1 hour', 'active', now())
                """
            ),
            {'owner': owner},
        )
    return owner


async def _cleanup(owner: str) -> None:
    async with async_db_session() as db:
        locations = (
            await db.execute(
                text(
                    """
                    SELECT storage_id, object_key
                    FROM hasn_storage_objects
                    WHERE owner_hasn_id = :owner
                    UNION
                    SELECT (result ->> 'storage_id')::bigint,
                           result ->> 'object_key'
                    FROM hasn_storage_jobs
                    WHERE owner_hasn_id = :owner
                      AND job_type = 'storage_export'
                      AND result ? 'storage_id'
                    """
                ),
                {'owner': owner},
            )
        ).all()
        for storage_id, object_key in locations:
            await StorageService.delete_object(
                db,
                storage_id=int(storage_id),
                object_key=str(object_key),
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
            await db.execute(text(f'DELETE FROM {table} WHERE owner_hasn_id = :owner'), {'owner': owner})  # noqa: S608
        await db.execute(text('DELETE FROM hasn_humans WHERE hasn_id = :owner'), {'owner': owner})


async def test_manifest_export_is_snapshot_throttled_and_downloadable() -> None:
    owner = await _seed_owner()
    service = OwnerStorageService(async_db_session)
    payload = b'export-real-content'
    try:
        asset = await service.upload(
            owner_hasn_id=owner,
            chunks=_chunks(payload),
            declared_size=len(payload),
            filename='导出文档.txt',
            mime='text/plain',
            category='private_doc',
            source_app='export_test',
            idempotency_key='export-source',
        )
        await service.bind_asset(
            owner_hasn_id=owner,
            asset_id=asset.asset_id,
            resource_uri='hasn://knowledge/documents/export_doc',
            role='source',
        )
        job = await service.create_export(
            owner_hasn_id=owner,
            mode='manifest',
            include_trashed=False,
        )
        assert job['status'] == 'pending'
        with pytest.raises(errors.RequestError, match='STORAGE_EXPORT_THROTTLED') as throttled:
            await service.create_export(
                owner_hasn_id=owner,
                mode='manifest',
                include_trashed=False,
            )
        assert throttled.value.code == 429

        assert await service.process_jobs(job_type='storage_export', limit=10) == 1
        status = await service.export_status(owner_hasn_id=owner, job_id=job['job_id'])
        assert status['status'] == 'succeeded'
        assert status['total_items'] == 1
        download = await service.export_download(owner_hasn_id=owner, job_id=job['job_id'])
        assert download['expires_at']
        async with httpx.AsyncClient(timeout=30, trust_env=False) as client:
            response = await client.get(download['url'])
            response.raise_for_status()
        lines = response.text.splitlines()
        assert len(lines) == 1
        manifest = json.loads(lines[0])
        assert manifest['asset_id'] == asset.asset_id
        assert manifest['logical_path'] == '/导出文档.txt'
        assert manifest['size_bytes'] == len(payload)
        assert manifest['source_app'] == 'export_test'
        assert manifest['bindings'] == [
            {
                'resource_uri': 'hasn://knowledge/documents/export_doc',
                'role': 'source',
            }
        ]
        assert manifest['download_url'].startswith('http')
    finally:
        await _cleanup(owner)


async def test_expired_export_staging_object_is_physically_deleted() -> None:
    owner = await _seed_owner()
    service = OwnerStorageService(async_db_session)
    maintenance = OwnerStorageMaintenanceService(async_db_session)
    try:
        await service.upload(
            owner_hasn_id=owner,
            chunks=_chunks(b'export-expiry-real-content'),
            declared_size=len(b'export-expiry-real-content'),
            filename='到期导出.txt',
            mime='text/plain',
            category='private_doc',
            source_app='export_test',
            idempotency_key='export-expiry-source',
        )
        job = await service.create_export(
            owner_hasn_id=owner,
            mode='manifest',
            include_trashed=False,
        )
        assert await service.process_jobs(job_type='storage_export', limit=1) == 1
        async with async_db_session.begin() as db:
            result = (
                await db.execute(
                    text(
                        """
                        UPDATE hasn_storage_jobs
                        SET expires_time = now() - interval '1 minute'
                        WHERE job_id = :job_id
                        RETURNING result
                        """
                    ),
                    {'job_id': job['job_id']},
                )
            ).scalar_one()
            storage = await StorageService.get_storage(db, int(result['storage_id']))

        report = await maintenance.sweep_expired_exports(
            owner_hasn_id=owner,
            limit=10,
        )

        assert report == {'checked': 1, 'purged': 1}
        with pytest.raises(errors.ServerError, match='S3 对象元数据读取失败'):
            await StorageService.stat_on_storage(
                storage,
                object_key=str(result['object_key']),
            )
        async with async_db_session() as db:
            current = (
                await db.execute(
                    text('SELECT result FROM hasn_storage_jobs WHERE job_id = :job_id'),
                    {'job_id': job['job_id']},
                )
            ).scalar_one()
        assert current['expired'] is True
        assert 'storage_id' not in current
        assert 'object_key' not in current
        with pytest.raises(errors.ConflictError, match='STORAGE_EXPORT_EXPIRED'):
            await service.export_download(owner_hasn_id=owner, job_id=job['job_id'])
    finally:
        await _cleanup(owner)


async def test_export_reports_missing_source_instead_of_publishing_false_success() -> None:
    """源对象缺失时导出必须留下逐项错误，不能发布只有下载链接的伪成功清单。"""
    owner = await _seed_owner()
    service = OwnerStorageService(async_db_session)
    payload = b'export-missing-source'
    try:
        asset = await service.upload(
            owner_hasn_id=owner,
            chunks=_chunks(payload),
            declared_size=len(payload),
            filename='已缺失原件.txt',
            mime='text/plain',
            category='private_doc',
            source_app='export_test',
            idempotency_key='export-missing-source',
        )
        async with async_db_session() as db:
            location = (
                await db.execute(
                    text(
                        """
                        SELECT o.storage_id, o.object_key
                        FROM hasn_assets AS a
                        JOIN hasn_storage_objects AS o ON o.object_id = a.object_id
                        WHERE a.owner_hasn_id = :owner AND a.asset_id = :asset_id
                        """
                    ),
                    {'owner': owner, 'asset_id': asset.asset_id},
                )
            ).mappings().one()
            await StorageService.delete_object(
                db,
                storage_id=int(location['storage_id']),
                object_key=str(location['object_key']),
            )

        job = await service.create_export(
            owner_hasn_id=owner,
            mode='manifest',
            include_trashed=False,
        )
        assert await service.process_jobs(job_type='storage_export', limit=1) == 0
        status = await service.export_status(owner_hasn_id=owner, job_id=job['job_id'])
        assert status['status'] == 'failed'
        assert status['error_code'] == 'STORAGE_EXPORT_FAILED'
        assert status['processed_items'] == 0
        assert status['failed_items'] == 1
        assert status['failures'] == [
            {
                'asset_id': asset.asset_id,
                'logical_path': '/已缺失原件.txt',
                'error_code': 'STORAGE_OBJECT_MISSING',
            }
        ]
        with pytest.raises(errors.ConflictError, match='STORAGE_EXPORT_NOT_READY'):
            await service.export_download(owner_hasn_id=owner, job_id=job['job_id'])
    finally:
        await _cleanup(owner)


async def test_export_uses_creation_snapshot_after_rename_and_delete() -> None:
    """导出创建后的目录改名与逻辑删除不得改写已经冻结的清单。"""
    owner = await _seed_owner()
    service = OwnerStorageService(async_db_session)
    payload = b'export-immutable-snapshot'
    try:
        asset = await service.upload(
            owner_hasn_id=owner,
            chunks=_chunks(payload),
            declared_size=len(payload),
            filename='创建时名称.txt',
            mime='text/plain',
            category='private_doc',
            source_app='export_test',
            idempotency_key='export-immutable-snapshot',
        )
        job = await service.create_export(
            owner_hasn_id=owner,
            mode='manifest',
            include_trashed=False,
        )
        renamed = await service.update_entry(
            owner_hasn_id=owner,
            entry_id=asset.entry_id,
            version=1,
            name='后来改名.txt',
            parent_entry_id=None,
        )
        assert renamed['display_name'] == '后来改名.txt'
        deleted = await service.delete_asset(
            owner_hasn_id=owner,
            asset_id=asset.asset_id,
        )
        assert deleted['state'] == 'deleting'

        assert await service.process_jobs(job_type='storage_export', limit=1) == 1
        download = await service.export_download(owner_hasn_id=owner, job_id=job['job_id'])
        async with httpx.AsyncClient(timeout=30, trust_env=False) as client:
            response = await client.get(download['url'])
            response.raise_for_status()
        manifest = json.loads(response.text)
        assert manifest['asset_id'] == asset.asset_id
        assert manifest['logical_path'] == '/创建时名称.txt'
        assert manifest['original_name'] == '创建时名称.txt'
    finally:
        await _cleanup(owner)


async def test_export_list_recovers_job_and_emits_completion_notification() -> None:
    """作业跑完后：① 列表能凭权威数据找回它（客户端换页后恢复状态卡）；② 主人收到完成通知。

    没有这两条，导出就断在半截——`job_id` 只活在页面内存里，切个菜单就再也取不到产物，
    而产物 24 小时后过期即被清理。
    """
    owner = await _seed_owner()
    service = OwnerStorageService(async_db_session)
    payload = b'export-list-and-notify'
    try:
        await service.upload(
            owner_hasn_id=owner,
            chunks=_chunks(payload),
            declared_size=len(payload),
            filename='待导出.txt',
            mime='text/plain',
            category='private_doc',
            source_app='export_test',
            idempotency_key='export-list-source',
        )
        job = await service.create_export(
            owner_hasn_id=owner,
            mode='manifest',
            include_trashed=False,
        )

        # 未完成时列表也要能找回作业（客户端凭它显示「进行中」）。
        listed = await service.list_exports(owner_hasn_id=owner)
        assert [item['job_id'] for item in listed['items']] == [job['job_id']]
        assert listed['items'][0]['status'] == 'pending'

        assert await service.process_jobs(job_type='storage_export', limit=1) == 1

        listed = await service.list_exports(owner_hasn_id=owner)
        assert listed['items'][0]['status'] == 'succeeded'
        # 列表与单条查询共用视图构造，形状必须一致（否则客户端两条路径渲染会打架）。
        status = await service.export_status(owner_hasn_id=owner, job_id=job['job_id'])
        assert listed['items'][0] == status

        async with async_db_session() as db:
            notification = (
                await db.execute(
                    text(
                        """
                        SELECT title, body, data, source, delivery
                        FROM hasn_notifications
                        WHERE target_id = :owner
                          AND type = 'storage_export_ready'
                        """
                    ),
                    {'owner': owner},
                )
            ).mappings().one()
        assert notification['title'] == '云存储导出已完成'
        assert notification['data']['job_id'] == job['job_id']
        assert notification['data']['link'] == 'hasn://storage/usage'
        assert notification['source']['display_name'] == '云存储'
        # 导出是主人在等的结果 → 允许弹到桌面；但不另开服务号会话。
        channels = notification['delivery']['channels']
        assert channels['center'] is True
        assert channels['card_message'] is False
    finally:
        await _cleanup(owner)


async def test_export_list_limit_is_validated() -> None:
    """越界 limit 如实拒绝，不静默截断成别的条数。"""
    owner = await _seed_owner()
    service = OwnerStorageService(async_db_session)
    try:
        with pytest.raises(errors.RequestError, match='STORAGE_EXPORT_LIST_LIMIT_INVALID'):
            await service.list_exports(owner_hasn_id=owner, limit=0)
        with pytest.raises(errors.RequestError, match='STORAGE_EXPORT_LIST_LIMIT_INVALID'):
            await service.list_exports(owner_hasn_id=owner, limit=51)
    finally:
        await _cleanup(owner)
