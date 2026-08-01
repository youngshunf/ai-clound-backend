"""用户云存储领域服务。

当前文件先承载配额投影与预占事务。对象上传、资产登记和生命周期操作在同一服务
中逐步收口，供应商网络调用不得进入这里的预占事务。
"""

from __future__ import annotations

import asyncio
import copy
import hashlib
import json
import os
import tempfile
import zipfile

from collections.abc import AsyncIterable, AsyncIterator, Sequence
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, BinaryIO, Protocol
from uuid import uuid4

from sqlalchemy import text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from backend.app.hasn.service.owner_storage_names import (
    display_name_for_upload,
    normalize_storage_name,
    suffixed_name,
)
from backend.app.hasn.service.owner_storage_policy import (
    CategoryPolicy,
    build_owner_object_key,
    resolve_owner_category,
)
from backend.common.exception import errors
from backend.common.log import log
from backend.common.response.response_code import StandardResponseCode
from backend.database.schema_names import SCHEMA_NAMES
from backend.plugin.s3.service.storage_service import StorageService
from backend.utils.timezone import timezone

RESERVATION_TTL = timedelta(minutes=30)
_UPLOAD_CHUNK_SIZE = 1024 * 1024
_TEMP_CAPACITY_DEFAULT = 32 * 1024**3
_EXPORT_COOLDOWN_DEFAULT = 3600
_EXPORT_DAILY_LIMIT_DEFAULT = 3
_EXPORT_ARCHIVE_MAX_BYTES_DEFAULT = 2 * 1024**3
_EXPORT_TTL_DEFAULT = 24 * 3600
_temp_capacity_lock = asyncio.Lock()
_temp_bytes_in_use = 0


class _ExportValidationFailed(Exception):
    """导出源对象已确认永久不一致，作业状态已经落库。"""


class SessionFactory(Protocol):
    """可建立异步数据库会话的最小协议。"""

    def __call__(self) -> AsyncSession: ...

    def begin(self) -> Any: ...


@dataclass(frozen=True, slots=True)
class StorageUsage:
    """Owner 当前有效配额投影。"""

    owner_hasn_id: str
    quota_bytes: int
    used_bytes: int
    reserved_bytes: int
    quota_source: str
    quota_version: str
    quota_valid_until: datetime | None
    state: str


@dataclass(frozen=True, slots=True)
class StorageReservation:
    """一次可幂等重放的物理对象预占。"""

    reservation_id: str
    owner_hasn_id: str
    object_id: str
    result_asset_id: str | None
    idempotency_key: str
    request_fingerprint: str | None
    reserved_bytes: int
    status: str
    expires_time: datetime


@dataclass(frozen=True, slots=True)
class StoredAsset:
    """统一上传成功后的稳定结果。"""

    asset_id: str
    object_id: str
    entry_id: str
    kind: str
    mime: str
    size_bytes: int
    display_name: str
    deduplicated: bool

    @property
    def uri(self) -> str:
        return f'hasn://asset/{self.asset_id}'


@dataclass(frozen=True, slots=True)
class _StagedUpload:
    path: Path
    size_bytes: int
    sha256: str


@dataclass(frozen=True, slots=True)
class _DerivedQuota:
    quota_bytes: int
    source: str
    version: str
    subscription_id: int | None
    valid_until: datetime | None


def _usage_of(row: Any) -> StorageUsage:
    return StorageUsage(
        owner_hasn_id=str(row['owner_hasn_id']),
        quota_bytes=int(row['quota_bytes']),
        used_bytes=int(row['used_bytes']),
        reserved_bytes=int(row['reserved_bytes']),
        quota_source=str(row['quota_source']),
        quota_version=str(row['quota_version']),
        quota_valid_until=row['quota_valid_until'],
        state=str(row['state']),
    )


def _reservation_of(row: Any) -> StorageReservation:
    return StorageReservation(
        reservation_id=str(row['reservation_id']),
        owner_hasn_id=str(row['owner_hasn_id']),
        object_id=str(row['object_id']),
        result_asset_id=row['result_asset_id'],
        idempotency_key=str(row['idempotency_key']),
        request_fingerprint=(
            str(row['request_fingerprint']) if row['request_fingerprint'] is not None else None
        ),
        reserved_bytes=int(row['reserved_bytes']),
        status=str(row['status']),
        expires_time=row['expires_time'],
    )


def _kind_for_mime(mime: str) -> str:
    if mime.startswith('image/'):
        return 'image'
    if mime.startswith('audio/'):
        return 'voice'
    return 'file'


def _system_category(category: str) -> str:
    return {
        'dm_attachment': 'conversation_attachments',
        'private_doc': 'knowledge',
        'published_artifact': 'agent_artifacts',
        'user_upload': 'my_uploads',
        'user_avatar': 'app_files',
        'post_image': 'app_files',
    }.get(category, 'app_files')


def _request_fingerprint(
    *,
    content_sha256: str | None,
    filename: str,
    mime: str,
    category: str,
    source_app: str,
    parent_entry_id: str | None = None,
    width: int | None = None,
    height: int | None = None,
    duration_ms: int | None = None,
) -> str:
    """按请求语义计算稳定指纹；同幂等键的任一字段变化都必须冲突。"""
    canonical = json.dumps(
        {
            'content_sha256': content_sha256,
            'filename': filename,
            'mime': mime,
            'category': category,
            'source_app': source_app,
            'parent_entry_id': parent_entry_id,
            'width': width,
            'height': height,
            'duration_ms': duration_ms,
        },
        ensure_ascii=False,
        sort_keys=True,
        separators=(',', ':'),
    )
    return hashlib.sha256(canonical.encode('utf-8')).hexdigest()


def _temp_capacity_bytes() -> int:
    raw = os.getenv('OWNER_STORAGE_TEMP_CAPACITY_BYTES')
    if raw is None:
        return _TEMP_CAPACITY_DEFAULT
    try:
        value = int(raw)
    except ValueError as exc:
        raise errors.ServerError(msg='STORAGE_TEMP_CAPACITY_INVALID') from exc
    if value <= 0:
        raise errors.ServerError(msg='STORAGE_TEMP_CAPACITY_INVALID')
    return value


def _positive_env_int(name: str, default: int) -> int:
    raw = os.getenv(name)
    if raw is None:
        return default
    try:
        value = int(raw)
    except ValueError as exc:
        raise errors.ServerError(msg=f'{name}_INVALID') from exc
    if value <= 0:
        raise errors.ServerError(msg=f'{name}_INVALID')
    return value


def _stored_asset_of(row: Any, *, deduplicated: bool) -> StoredAsset:
    return StoredAsset(
        asset_id=str(row['asset_id']),
        object_id=str(row['object_id']),
        entry_id=str(row['entry_id']),
        kind=str(row['kind']),
        mime=str(row['mime']),
        size_bytes=int(row['size_bytes']),
        display_name=str(row['display_name']),
        deduplicated=deduplicated,
    )


def _export_job_view(row: Any) -> dict[str, Any]:
    """导出作业行 → 对外视图。单条查询与列表共用，避免两处形状漂移。"""
    payload = dict(row['payload'])
    result = dict(row['result'])
    return {
        'job_id': str(row['job_id']),
        'status': str(row['status']),
        'mode': payload.get('mode'),
        'total_items': int(row['total_items']),
        'processed_items': int(row['processed_items']),
        'failed_items': int(row['failed_items']),
        'total_bytes': int(payload.get('total_bytes', 0)),
        'error_code': row['error_code'],
        'attempt_count': int(row['attempt_count']),
        'size_bytes': result.get('size_bytes'),
        'sha256': result.get('sha256'),
        'failures': list(result.get('failures') or []),
        'expires_time': row['expires_time'].isoformat() if row['expires_time'] else None,
        'created_time': row['created_time'].isoformat(),
        'updated_time': row['updated_time'].isoformat() if row['updated_time'] else None,
    }


class OwnerStorageService:
    """按 Owner 隔离的配额与对象生命周期权威。"""

    def __init__(self, session_factory: SessionFactory) -> None:
        self._sessions = session_factory

    @staticmethod
    async def _write_storage_for_owner(
        db: AsyncSession,
        *,
        owner_hasn_id: str,
        access: str,
    ) -> Any:
        """迁移窗口内按 Owner 固定写入目标，避免快照后的新对象遗留在源存储。"""
        migration = (
            await db.execute(
                text(
                    """
                    SELECT status, payload, result
                    FROM hasn_storage_jobs
                    WHERE owner_hasn_id = :owner
                      AND job_type = 'storage_migration'
                    ORDER BY id DESC
                    LIMIT 1
                    """
                ),
                {'owner': owner_hasn_id},
            )
        ).mappings().one_or_none()
        if migration is not None:
            status = str(migration['status'])
            result = dict(migration['result'])
            target_map = dict(migration['payload']).get('target_storage_by_access', {})
            target_storage_id = target_map.get(access)
            if (
                status in {'pending', 'running', 'retrying', 'paused', 'succeeded'}
                and result.get('rolled_back') is not True
                and target_storage_id is not None
            ):
                storage = await StorageService.get_storage(db, int(target_storage_id))
                if str(storage.access) != access:
                    raise errors.ServerError(msg='STORAGE_MIGRATION_TARGET_ACCESS_MISMATCH')
                return storage
        return await StorageService.get_write_storage(db, access=access)

    @staticmethod
    async def _derive_quota(db: AsyncSession, *, owner_hasn_id: str, now: datetime) -> _DerivedQuota:
        human = (
            await db.execute(
                text(
                    """
                    SELECT user_id
                    FROM hasn_humans
                    WHERE hasn_id = :owner AND status = 'active'
                    """
                ),
                {'owner': owner_hasn_id},
            )
        ).mappings().one_or_none()
        if human is None:
            raise errors.ServerError(
                msg='STORAGE_OWNER_IDENTITY_NOT_READY',
                data={'owner_hasn_id': owner_hasn_id},
            )

        contract = (
            await db.execute(
                text(
                    """
                    SELECT id, contract_no, contract_end_at, plan_snapshot
                    FROM hasn_billing.user_subscription
                    WHERE app_code = 'huanxing'
                      AND user_id = :user_id
                      AND status IN ('active', 'cancel_at_period_end', 'scheduled')
                      AND contract_start_at IS NOT NULL
                      AND contract_start_at <= :now
                      AND (contract_end_at IS NULL OR contract_end_at > :now)
                    ORDER BY contract_start_at DESC, id DESC
                    LIMIT 1
                    """
                ),
                {'user_id': int(human['user_id']), 'now': now},
            )
        ).mappings().one_or_none()
        if contract is not None:
            snapshot = contract['plan_snapshot']
            raw_quota = snapshot.get('storage_bytes') if isinstance(snapshot, dict) else None
            if isinstance(raw_quota, bool) or not isinstance(raw_quota, int) or raw_quota <= 0:
                raise errors.ServerError(
                    msg='STORAGE_CONTRACT_QUOTA_INVALID',
                    data={'subscription_id': int(contract['id'])},
                )
            return _DerivedQuota(
                quota_bytes=raw_quota,
                source='subscription',
                version=str(contract['contract_no'] or f"subscription:{contract['id']}"),
                subscription_id=int(contract['id']),
                valid_until=contract['contract_end_at'],
            )

        free_plan = (
            await db.execute(
                text(
                    """
                    SELECT id, quota_json, created_time, updated_time
                    FROM hasn_billing.billing_plan
                    WHERE offering_key = 'llm:tier'
                      AND status = 'active'
                      AND (plan_key = 'free' OR quota_json ->> 'tier' = 'free')
                    ORDER BY id DESC
                    LIMIT 1
                    """
                )
            )
        ).mappings().one_or_none()
        snapshot = free_plan['quota_json'] if free_plan is not None else None
        raw_quota = snapshot.get('storage_bytes') if isinstance(snapshot, dict) else None
        if (
            free_plan is None
            or isinstance(raw_quota, bool)
            or not isinstance(raw_quota, int)
            or raw_quota <= 0
        ):
            raise errors.ServerError(msg='STORAGE_FREE_POLICY_NOT_READY')
        version_time = free_plan['updated_time'] or free_plan['created_time']
        return _DerivedQuota(
            quota_bytes=raw_quota,
            source='free_policy',
            version=f"billing_plan:{free_plan['id']}:{version_time.isoformat()}",
            subscription_id=None,
            valid_until=None,
        )

    async def _refresh_account_in_transaction(
        self,
        db: AsyncSession,
        *,
        owner_hasn_id: str,
        now: datetime,
    ) -> StorageUsage:
        account = (
            await db.execute(
                text(
                    """
                    SELECT owner_hasn_id, quota_bytes, used_bytes, reserved_bytes, quota_source,
                           quota_version, quota_valid_until, state
                    FROM hasn_storage_accounts
                    WHERE owner_hasn_id = :owner
                    FOR UPDATE
                    """
                ),
                {'owner': owner_hasn_id},
            )
        ).mappings().one_or_none()

        if account is not None:
            valid_until = account['quota_valid_until']
            if account['quota_source'] == 'admin_override' and (valid_until is None or valid_until > now):
                return _usage_of(account)
            if valid_until is None:
                return _usage_of(account)
            if valid_until > now:
                return _usage_of(account)

        derived = await self._derive_quota(db, owner_hasn_id=owner_hasn_id, now=now)
        if account is None:
            await db.execute(
                text(
                    """
                    INSERT INTO hasn_storage_accounts
                        (owner_hasn_id, quota_bytes, used_bytes, reserved_bytes, quota_source,
                         quota_version, source_subscription_id, quota_valid_until, state,
                         created_time, updated_time)
                    VALUES
                        (:owner, :quota, 0, 0, :source, :version, :subscription_id,
                         :valid_until, 'active', :now, :now)
                    ON CONFLICT (owner_hasn_id) DO NOTHING
                    """
                ),
                {
                    'owner': owner_hasn_id,
                    'quota': derived.quota_bytes,
                    'source': derived.source,
                    'version': derived.version,
                    'subscription_id': derived.subscription_id,
                    'valid_until': derived.valid_until,
                    'now': now,
                },
            )
        else:
            state = (
                'active'
                if int(account['used_bytes']) + int(account['reserved_bytes']) <= derived.quota_bytes
                else 'over_quota'
            )
            await db.execute(
                text(
                    """
                    UPDATE hasn_storage_accounts
                    SET quota_bytes = :quota,
                        quota_source = :source,
                        quota_version = :version,
                        source_subscription_id = :subscription_id,
                        quota_valid_until = :valid_until,
                        state = :state,
                        updated_time = :now
                    WHERE owner_hasn_id = :owner
                    """
                ),
                {
                    'owner': owner_hasn_id,
                    'quota': derived.quota_bytes,
                    'source': derived.source,
                    'version': derived.version,
                    'subscription_id': derived.subscription_id,
                    'valid_until': derived.valid_until,
                    'state': state,
                    'now': now,
                },
            )

        refreshed = (
            await db.execute(
                text(
                    """
                    SELECT owner_hasn_id, quota_bytes, used_bytes, reserved_bytes, quota_source,
                           quota_version, quota_valid_until, state
                    FROM hasn_storage_accounts
                    WHERE owner_hasn_id = :owner
                    FOR UPDATE
                    """
                ),
                {'owner': owner_hasn_id},
            )
        ).mappings().one_or_none()
        if refreshed is None:
            raise errors.ServerError(
                msg='STORAGE_ACCOUNT_NOT_READY',
                data={'owner_hasn_id': owner_hasn_id},
            )
        return _usage_of(refreshed)

    async def usage(self, *, owner_hasn_id: str, now: datetime | None = None) -> StorageUsage:
        """确保账户存在，并在投影过期时从不可变合同快照 lazy 派生。"""
        effective_now = now or timezone.now()
        async with self._sessions.begin() as db:
            return await self._refresh_account_in_transaction(
                db,
                owner_hasn_id=owner_hasn_id,
                now=effective_now,
            )

    async def usage_details(self, *, owner_hasn_id: str) -> dict[str, Any]:
        """返回容量投影和按物理对象唯一计量的类别占用。"""
        usage = await self.usage(owner_hasn_id=owner_hasn_id)
        async with self._sessions() as db:
            rows = (
                await db.execute(
                    text(
                        """
                        WITH object_categories AS (
                            SELECT DISTINCT ON (o.object_id)
                                   o.object_id,
                                   COALESCE(a.category, 'uncategorized') AS category,
                                   o.size_bytes
                            FROM hasn_storage_objects AS o
                            LEFT JOIN hasn_assets AS a
                              ON a.object_id = o.object_id
                             AND a.owner_hasn_id = o.owner_hasn_id
                             AND a.lifecycle_status <> 'deleted'
                            WHERE o.owner_hasn_id = :owner
                              AND o.billable_to_owner
                              AND o.state IN ('pending', 'active', 'deleting')
                            ORDER BY o.object_id, a.created_time, a.id
                        )
                        SELECT category, SUM(size_bytes)::bigint AS bytes
                        FROM object_categories
                        GROUP BY category
                        ORDER BY category
                        """
                    ),
                    {'owner': owner_hasn_id},
                )
            ).mappings().all()
        return {
            'owner_hasn_id': owner_hasn_id,
            'quota_bytes': usage.quota_bytes,
            'used_bytes': usage.used_bytes,
            'reserved_bytes': usage.reserved_bytes,
            'remaining_bytes': max(0, usage.quota_bytes - usage.used_bytes - usage.reserved_bytes),
            'quota_source': usage.quota_source,
            'quota_version': usage.quota_version,
            'quota_valid_until': usage.quota_valid_until.isoformat() if usage.quota_valid_until else None,
            'state': usage.state,
            'category_bytes': {str(row['category']): int(row['bytes']) for row in rows},
        }

    async def create_folder(
        self,
        *,
        owner_hasn_id: str,
        name: str,
        parent_entry_id: str | None = None,
    ) -> dict[str, Any]:
        """创建 Owner 私有逻辑文件夹。"""
        display_name = display_name_for_upload(name)
        normalized_name = normalize_storage_name(display_name)
        async with self._sessions.begin() as db:
            await self._lock_directory_names(db, owner_hasn_id, parent_entry_id)
            await self._assert_folder_parent(
                db,
                owner_hasn_id=owner_hasn_id,
                parent_entry_id=parent_entry_id,
            )
            await self._assert_entry_name_available(
                db,
                owner_hasn_id=owner_hasn_id,
                parent_entry_id=parent_entry_id,
                normalized_name=normalized_name,
            )
            now = timezone.now()
            row = (
                await db.execute(
                    text(
                        """
                        INSERT INTO hasn_storage_entries
                            (entry_id, owner_hasn_id, asset_id, parent_entry_id, entry_type,
                             display_name, normalized_name, system_category, version,
                             created_time, updated_time)
                        VALUES
                            (:entry_id, :owner, NULL, :parent, 'folder',
                             :display_name, :normalized_name, NULL, 1, :now, :now)
                        RETURNING entry_id, parent_entry_id, entry_type, display_name, version
                        """
                    ),
                    {
                        'entry_id': f'ent_{uuid4().hex}',
                        'owner': owner_hasn_id,
                        'parent': parent_entry_id,
                        'display_name': display_name,
                        'normalized_name': normalized_name,
                        'now': now,
                    },
                )
            ).mappings().one()
            return {
                'entry_id': str(row['entry_id']),
                'parent_entry_id': row['parent_entry_id'],
                'entry_type': str(row['entry_type']),
                'display_name': str(row['display_name']),
                'version': int(row['version']),
            }

    async def entry_details(
        self,
        *,
        owner_hasn_id: str,
        entry_id: str,
    ) -> dict[str, Any]:
        """读取单个目录项；不存在与越权统一返回相同 404。"""
        async with self._sessions() as db:
            row = await self._entry_row(
                db,
                owner_hasn_id=owner_hasn_id,
                entry_id=entry_id,
            )
            if row is None:
                raise errors.NotFoundError(msg='STORAGE_ENTRY_NOT_FOUND')
            return self._entry_dict(row)

    async def list_entries(
        self,
        *,
        owner_hasn_id: str,
        parent_entry_id: str | None = None,
        query: str | None = None,
        entry_type: str | None = None,
        category: str | None = None,
        source_app: str | None = None,
        lifecycle_status: str = 'active',
        page: int = 1,
        page_size: int = 50,
    ) -> dict[str, Any]:
        """分页列出目录项；搜索时覆盖全部目录，否则列指定目录。"""
        if page <= 0 or page_size <= 0 or page_size > 200:
            raise errors.RequestError(
                code=StandardResponseCode.HTTP_422,
                msg='STORAGE_PAGINATION_INVALID',
            )
        if entry_type not in {None, 'file', 'folder'}:
            raise errors.RequestError(
                code=StandardResponseCode.HTTP_422,
                msg='STORAGE_ENTRY_TYPE_INVALID',
            )
        if lifecycle_status not in {'active', 'trashed', 'deleting', 'deleted'}:
            raise errors.RequestError(
                code=StandardResponseCode.HTTP_422,
                msg='STORAGE_LIFECYCLE_STATUS_INVALID',
            )
        normalized_query = query.strip() if query else None
        values = {
            'owner': owner_hasn_id,
            'parent': parent_entry_id,
            'query': f'%{normalized_query}%' if normalized_query else None,
            'entry_type': entry_type,
            'category': category,
            'source_app': source_app,
            'lifecycle_status': lifecycle_status,
            'limit': page_size,
            'offset': (page - 1) * page_size,
        }
        predicate = """
            e.owner_hasn_id = :owner
            AND (
                CAST(:entry_type AS varchar) IS NULL
                OR e.entry_type = CAST(:entry_type AS varchar)
            )
            AND (
                CAST(:category AS varchar) IS NULL
                OR a.category = CAST(:category AS varchar)
            )
            AND (
                CAST(:source_app AS varchar) IS NULL
                OR a.source_app = CAST(:source_app AS varchar)
            )
            AND (
                (CAST(:lifecycle_status AS varchar) = 'active' AND (
                    e.entry_type = 'folder' OR a.lifecycle_status = 'active'
                ))
                OR (
                    CAST(:lifecycle_status AS varchar) <> 'active'
                    AND e.entry_type = 'file'
                    AND a.lifecycle_status = CAST(:lifecycle_status AS varchar)
                )
            )
            AND (
                CAST(:query AS varchar) IS NOT NULL
                OR (
                    (CAST(:parent AS varchar) IS NULL AND e.parent_entry_id IS NULL)
                    OR e.parent_entry_id = CAST(:parent AS varchar)
                )
            )
            AND (
                CAST(:query AS varchar) IS NULL
                OR e.display_name ILIKE CAST(:query AS varchar)
            )
        """
        async with self._sessions() as db:
            if parent_entry_id is not None and normalized_query is None:
                await self._assert_folder_parent(
                    db,
                    owner_hasn_id=owner_hasn_id,
                    parent_entry_id=parent_entry_id,
                )
            total = (
                await db.execute(
                    text(
                        f"""
                        SELECT COUNT(*)
                        FROM hasn_storage_entries AS e
                        LEFT JOIN hasn_assets AS a ON a.asset_id = e.asset_id
                        WHERE {predicate}
                        """
                    ),
                    values,
                )
            ).scalar_one()
            rows = (
                await db.execute(
                    text(
                        f"""
                        SELECT e.entry_id, e.asset_id, e.parent_entry_id, e.entry_type,
                               e.display_name, e.system_category, e.version,
                               e.created_time, e.updated_time,
                               a.kind, a.mime, a.category, a.source_app,
                               a.lifecycle_status, a.original_name,
                               o.object_id, o.size_bytes, o.sha256, o.state AS object_state
                        FROM hasn_storage_entries AS e
                        LEFT JOIN hasn_assets AS a ON a.asset_id = e.asset_id
                        LEFT JOIN hasn_storage_objects AS o ON o.object_id = a.object_id
                        WHERE {predicate}
                        ORDER BY
                            CASE WHEN e.entry_type = 'folder' THEN 0 ELSE 1 END,
                            e.normalized_name,
                            e.entry_id
                        LIMIT :limit OFFSET :offset
                        """
                    ),
                    values,
                )
            ).mappings().all()
            return {
                'page': page,
                'page_size': page_size,
                'total': int(total),
                'items': [self._entry_dict(row) for row in rows],
            }

    async def update_entry(
        self,
        *,
        owner_hasn_id: str,
        entry_id: str,
        version: int,
        name: str | None,
        parent_entry_id: str | None,
    ) -> dict[str, Any]:
        """以乐观锁原子重命名或移动目录项。"""
        if version <= 0:
            raise errors.RequestError(
                code=StandardResponseCode.HTTP_422,
                msg='STORAGE_ENTRY_VERSION_INVALID',
            )
        async with self._sessions.begin() as db:
            current = (
                await db.execute(
                    text(
                        """
                        SELECT entry_id, owner_hasn_id, parent_entry_id, entry_type,
                               display_name, normalized_name, version
                        FROM hasn_storage_entries
                        WHERE owner_hasn_id = :owner AND entry_id = :entry_id
                        FOR UPDATE
                        """
                    ),
                    {'owner': owner_hasn_id, 'entry_id': entry_id},
                )
            ).mappings().one_or_none()
            if current is None:
                raise errors.NotFoundError(msg='STORAGE_ENTRY_NOT_FOUND')
            if int(current['version']) != version:
                raise errors.ConflictError(
                    msg='STORAGE_ENTRY_VERSION_CONFLICT',
                    data={'current_version': int(current['version'])},
                )
            await self._assert_folder_parent(
                db,
                owner_hasn_id=owner_hasn_id,
                parent_entry_id=parent_entry_id,
            )
            if str(current['entry_type']) == 'folder':
                await self._assert_folder_move_acyclic(
                    db,
                    owner_hasn_id=owner_hasn_id,
                    entry_id=entry_id,
                    parent_entry_id=parent_entry_id,
                )

            display_name = display_name_for_upload(name) if name is not None else str(current['display_name'])
            normalized_name = normalize_storage_name(display_name)
            await self._lock_directory_names(db, owner_hasn_id, parent_entry_id)
            await self._assert_entry_name_available(
                db,
                owner_hasn_id=owner_hasn_id,
                parent_entry_id=parent_entry_id,
                normalized_name=normalized_name,
                exclude_entry_id=entry_id,
            )
            now = timezone.now()
            row = (
                await db.execute(
                    text(
                        """
                        UPDATE hasn_storage_entries
                        SET parent_entry_id = :parent,
                            display_name = :display_name,
                            normalized_name = :normalized_name,
                            version = version + 1,
                            updated_time = :now
                        WHERE owner_hasn_id = :owner
                          AND entry_id = :entry_id
                          AND version = :version
                        RETURNING entry_id, asset_id, parent_entry_id, entry_type,
                                  display_name, system_category, version,
                                  created_time, updated_time
                        """
                    ),
                    {
                        'owner': owner_hasn_id,
                        'entry_id': entry_id,
                        'version': version,
                        'parent': parent_entry_id,
                        'display_name': display_name,
                        'normalized_name': normalized_name,
                        'now': now,
                    },
                )
            ).mappings().one_or_none()
            if row is None:
                raise errors.ConflictError(msg='STORAGE_ENTRY_VERSION_CONFLICT')
            return self._entry_dict(row)

    @staticmethod
    async def _entry_row(
        db: AsyncSession,
        *,
        owner_hasn_id: str,
        entry_id: str,
    ) -> Any:
        return (
            await db.execute(
                text(
                    """
                    SELECT e.entry_id, e.asset_id, e.parent_entry_id, e.entry_type,
                           e.display_name, e.system_category, e.version,
                           e.created_time, e.updated_time,
                           a.kind, a.mime, a.category, a.source_app,
                           a.lifecycle_status, a.original_name,
                           o.object_id, o.size_bytes, o.sha256, o.state AS object_state
                    FROM hasn_storage_entries AS e
                    LEFT JOIN hasn_assets AS a ON a.asset_id = e.asset_id
                    LEFT JOIN hasn_storage_objects AS o ON o.object_id = a.object_id
                    WHERE e.owner_hasn_id = :owner AND e.entry_id = :entry_id
                    """
                ),
                {'owner': owner_hasn_id, 'entry_id': entry_id},
            )
        ).mappings().one_or_none()

    @staticmethod
    def _entry_dict(row: Any) -> dict[str, Any]:
        keys = set(row.keys())

        def value(key: str) -> Any:
            return row[key] if key in keys else None

        created_time = value('created_time')
        updated_time = value('updated_time')
        return {
            'entry_id': str(row['entry_id']),
            'asset_id': value('asset_id'),
            'parent_entry_id': value('parent_entry_id'),
            'entry_type': str(row['entry_type']),
            'display_name': str(row['display_name']),
            'system_category': value('system_category'),
            'version': int(row['version']),
            'kind': value('kind'),
            'mime': value('mime'),
            'category': value('category'),
            'source_app': value('source_app'),
            'lifecycle_status': value('lifecycle_status'),
            'original_name': value('original_name'),
            'object_id': value('object_id'),
            'size_bytes': int(value('size_bytes')) if value('size_bytes') is not None else None,
            'sha256': value('sha256'),
            'object_state': value('object_state'),
            'created_time': created_time.isoformat() if created_time is not None else None,
            'updated_time': updated_time.isoformat() if updated_time is not None else None,
        }

    @staticmethod
    async def _lock_directory_names(
        db: AsyncSession,
        owner_hasn_id: str,
        parent_entry_id: str | None,
    ) -> None:
        await db.execute(
            text('SELECT pg_advisory_xact_lock(hashtextextended(:lock_key, 0))'),
            {'lock_key': f"owner-storage-dir-name:{owner_hasn_id}:{parent_entry_id or 'root'}"},
        )

    @staticmethod
    async def _assert_folder_parent(
        db: AsyncSession,
        *,
        owner_hasn_id: str,
        parent_entry_id: str | None,
    ) -> None:
        if parent_entry_id is None:
            return
        parent = (
            await db.execute(
                text(
                    """
                    SELECT 1
                    FROM hasn_storage_entries
                    WHERE owner_hasn_id = :owner
                      AND entry_id = :parent
                      AND entry_type = 'folder'
                    """
                ),
                {'owner': owner_hasn_id, 'parent': parent_entry_id},
            )
        ).scalar_one_or_none()
        if parent is None:
            raise errors.NotFoundError(msg='STORAGE_ENTRY_NOT_FOUND')

    @staticmethod
    async def _assert_entry_name_available(
        db: AsyncSession,
        *,
        owner_hasn_id: str,
        parent_entry_id: str | None,
        normalized_name: str,
        exclude_entry_id: str | None = None,
    ) -> None:
        exists = (
            await db.execute(
                text(
                    """
                    SELECT 1
                    FROM hasn_storage_entries
                    WHERE owner_hasn_id = :owner
                      AND (
                          (CAST(:parent AS varchar) IS NULL AND parent_entry_id IS NULL)
                          OR parent_entry_id = CAST(:parent AS varchar)
                      )
                      AND normalized_name = :normalized_name
                      AND (
                          CAST(:exclude_entry_id AS varchar) IS NULL
                          OR entry_id <> CAST(:exclude_entry_id AS varchar)
                      )
                    LIMIT 1
                    """
                ),
                {
                    'owner': owner_hasn_id,
                    'parent': parent_entry_id,
                    'normalized_name': normalized_name,
                    'exclude_entry_id': exclude_entry_id,
                },
            )
        ).scalar_one_or_none()
        if exists is not None:
            raise errors.ConflictError(msg='STORAGE_NAME_CONFLICT')

    @staticmethod
    async def _assert_folder_move_acyclic(
        db: AsyncSession,
        *,
        owner_hasn_id: str,
        entry_id: str,
        parent_entry_id: str | None,
    ) -> None:
        if parent_entry_id is None:
            return
        if parent_entry_id == entry_id:
            raise errors.ConflictError(msg='STORAGE_FOLDER_CYCLE')
        descendant = (
            await db.execute(
                text(
                    """
                    WITH RECURSIVE descendants AS (
                        SELECT entry_id
                        FROM hasn_storage_entries
                        WHERE owner_hasn_id = :owner AND parent_entry_id = :entry_id
                        UNION ALL
                        SELECT child.entry_id
                        FROM hasn_storage_entries AS child
                        JOIN descendants AS parent
                          ON child.parent_entry_id = parent.entry_id
                        WHERE child.owner_hasn_id = :owner
                    )
                    SELECT 1
                    FROM descendants
                    WHERE entry_id = :parent_entry_id
                    LIMIT 1
                    """
                ),
                {
                    'owner': owner_hasn_id,
                    'entry_id': entry_id,
                    'parent_entry_id': parent_entry_id,
                },
            )
        ).scalar_one_or_none()
        if descendant is not None:
            raise errors.ConflictError(msg='STORAGE_FOLDER_CYCLE')

    async def save_to_my_storage(
        self,
        *,
        owner_hasn_id: str,
        source_asset_id: str,
        idempotency_key: str,
        parent_entry_id: str | None,
        display_name: str | None,
    ) -> StoredAsset:
        """把本人或公开源资产保存为当前 Owner 的独立逻辑资产。"""
        if not idempotency_key or len(idempotency_key) > 128:
            raise errors.RequestError(
                code=StandardResponseCode.HTTP_422,
                msg='STORAGE_IDEMPOTENCY_KEY_INVALID',
            )
        if display_name is not None:
            display_name_for_upload(display_name)

        async with self._sessions() as db:
            await self._assert_folder_parent(
                db,
                owner_hasn_id=owner_hasn_id,
                parent_entry_id=parent_entry_id,
            )
            source = (
                await db.execute(
                    text(
                        """
                        SELECT a.asset_id, a.owner_hasn_id, a.access, a.kind, a.mime,
                               a.width, a.height, a.duration_ms, a.original_name,
                               o.object_id, o.storage_id, o.object_key, o.size_bytes, o.sha256
                        FROM hasn_assets AS a
                        JOIN hasn_storage_objects AS o ON o.object_id = a.object_id
                        WHERE a.asset_id = :asset_id
                          AND a.lifecycle_status = 'active'
                          AND o.state = 'active'
                          AND (
                              a.owner_hasn_id = :owner
                              OR a.access = 'public'
                          )
                        """
                    ),
                    {'asset_id': source_asset_id, 'owner': owner_hasn_id},
                )
            ).mappings().one_or_none()
            if source is None:
                raise errors.NotFoundError(msg='STORAGE_ASSET_NOT_FOUND')
            source_storage = copy.copy(await StorageService.get_storage(db, int(source['storage_id'])))

        source_size = int(source['size_bytes'])
        source_sha = str(source['sha256']).strip() if source['sha256'] else None
        if source_sha is None:
            source_sha, actual_size = await StorageService.sha256_on_storage(
                source_storage,
                object_key=str(source['object_key']),
            )
            if actual_size != source_size:
                raise errors.ConflictError(msg='STORAGE_OBJECT_MISSING')

        filename = display_name or str(source['original_name'] or f'{source_asset_id}.bin')
        request_fingerprint = _request_fingerprint(
            content_sha256=source_sha,
            filename=filename,
            mime=str(source['mime']),
            category='user_upload',
            source_app='owner_storage_save',
            parent_entry_id=parent_entry_id,
            width=source['width'],
            height=source['height'],
            duration_ms=source['duration_ms'],
        )
        existing_reservation = await self._reservation_by_idempotency(
            owner_hasn_id,
            idempotency_key,
        )
        if existing_reservation is not None:
            self._validate_replay(
                existing_reservation,
                source_size,
                request_fingerprint=request_fingerprint,
            )
            replay = await self._uploaded_asset_by_idempotency(owner_hasn_id, idempotency_key)
            if replay is not None:
                return replay
        duplicate = await self._create_saved_asset_from_existing_object(
            owner_hasn_id=owner_hasn_id,
            sha256=source_sha,
            filename=filename,
            mime=str(source['mime']),
            source_asset_id=source_asset_id,
            idempotency_key=idempotency_key,
            parent_entry_id=parent_entry_id,
            width=source['width'],
            height=source['height'],
            duration_ms=source['duration_ms'],
            request_fingerprint=request_fingerprint,
        )
        if duplicate is not None:
            return duplicate

        reservation = await self.reserve(
            owner_hasn_id=owner_hasn_id,
            requested_bytes=source_size,
            idempotency_key=idempotency_key,
            request_fingerprint=request_fingerprint,
        )
        if reservation.status == 'committed' and reservation.result_asset_id:
            replay = await self._uploaded_asset_by_idempotency(owner_hasn_id, idempotency_key)
            if replay is None:
                raise errors.ConflictError(
                    msg='STORAGE_IDEMPOTENCY_RESULT_UNAVAILABLE',
                    data={'asset_id': reservation.result_asset_id},
                )
            return replay
        if reservation.status != 'reserved':
            raise errors.ConflictError(
                msg='STORAGE_RESERVATION_NOT_ACTIVE',
                data={'status': reservation.status},
            )

        target_storage: Any = None
        target_key: str | None = None
        copied = False
        policy = resolve_owner_category('user_upload')
        try:
            async with self._sessions() as db:
                target_storage = await self._write_storage_for_owner(
                    db,
                    owner_hasn_id=owner_hasn_id,
                    access=policy.access,
                )
            target_key = build_owner_object_key(
                owner_hasn_id=owner_hasn_id,
                access=policy.access,
                object_id=reservation.object_id,
            )
            await StorageService.copy_between_storages(
                source_storage,
                source_key=str(source['object_key']),
                target=target_storage,
                target_key=target_key,
                size=source_size,
                content_type=str(source['mime']),
            )
            copied = True
            stat = await StorageService.stat_on_storage(target_storage, object_key=target_key)
            if stat.size != source_size:
                raise errors.GatewayError(msg='STORAGE_UPLOAD_SIZE_MISMATCH')
            copied_sha, copied_size = await StorageService.sha256_on_storage(
                target_storage,
                object_key=target_key,
            )
            if copied_size != source_size or copied_sha != source_sha:
                raise errors.GatewayError(msg='STORAGE_UPLOAD_HASH_MISMATCH')
            return await self._finalize_uploaded_asset(
                reservation=reservation,
                storage_id=int(target_storage.id),
                object_key=target_key,
                policy=policy,
                sha256=source_sha,
                size_bytes=source_size,
                filename=filename,
                mime=str(source['mime']),
                category='user_upload',
                source_app='owner_storage_save',
                idempotency_key=idempotency_key,
                width=source['width'],
                height=source['height'],
                duration_ms=source['duration_ms'],
                extract_status='done',
                parent_entry_id=parent_entry_id,
                display_name_override=display_name,
                derived_from_asset_id=source_asset_id,
            )
        except Exception as exc:
            if copied and target_storage is not None and target_key is not None:
                await self._record_orphan_cleanup(
                    owner_hasn_id=owner_hasn_id,
                    storage_id=int(target_storage.id),
                    object_key=target_key,
                    reservation_id=reservation.reservation_id,
                    reason=(exc.msg or type(exc).__name__)
                    if isinstance(exc, errors.BaseExceptionError)
                    else type(exc).__name__,
                )
            await self.release_reservation(reservation.reservation_id)
            if isinstance(exc, errors.BaseExceptionError):
                raise
            log.exception(f'用户云存储转存失败: {type(exc).__name__}: {exc!r}')
            raise errors.GatewayError(msg='STORAGE_UPLOAD_FAILED') from exc

    async def _create_saved_asset_from_existing_object(
        self,
        *,
        owner_hasn_id: str,
        sha256: str,
        filename: str,
        mime: str,
        source_asset_id: str,
        idempotency_key: str,
        parent_entry_id: str | None,
        width: int | None,
        height: int | None,
        duration_ms: int | None,
        request_fingerprint: str,
    ) -> StoredAsset | None:
        async with self._sessions.begin() as db:
            await self._lock_upload_idempotency(db, owner_hasn_id, idempotency_key)
            existing_reservation = await self._reservation_by_idempotency_in_transaction(
                db,
                owner_hasn_id,
                idempotency_key,
            )
            if existing_reservation is not None:
                self._validate_replay(
                    existing_reservation,
                    existing_reservation.reserved_bytes,
                    request_fingerprint=request_fingerprint,
                )
            replay = await self._uploaded_asset_by_idempotency_in_transaction(
                db,
                owner_hasn_id,
                idempotency_key,
            )
            if replay is not None:
                return replay
            await self._lock_content_hash(db, owner_hasn_id, sha256)
            obj = (
                await db.execute(
                    text(
                        """
                        SELECT object_id, storage_id, object_key, access, size_bytes
                        FROM hasn_storage_objects
                        WHERE owner_hasn_id = :owner
                          AND sha256 = :sha256
                          AND access = 'private'
                          AND billable_to_owner
                          AND state = 'active'
                        FOR UPDATE
                        """
                    ),
                    {'owner': owner_hasn_id, 'sha256': sha256},
                )
            ).mappings().one_or_none()
            if obj is None:
                return None
            result = await self._insert_logical_asset(
                db,
                owner_hasn_id=owner_hasn_id,
                object_row=obj,
                filename=filename,
                mime=mime,
                category='user_upload',
                source_app='owner_storage_save',
                idempotency_key=idempotency_key,
                width=width,
                height=height,
                duration_ms=duration_ms,
                extract_status='done',
                deduplicated=True,
                parent_entry_id=parent_entry_id,
                display_name_override=filename,
                derived_from_asset_id=source_asset_id,
            )
            updated = await db.execute(
                text(
                    """
                    UPDATE hasn_storage_objects
                    SET ref_count = ref_count + 1, updated_time = :now
                    WHERE object_id = :object_id AND state = 'active'
                    """
                ),
                {'object_id': str(obj['object_id']), 'now': timezone.now()},
            )
            if updated.rowcount != 1:
                raise errors.ServerError(msg='STORAGE_OBJECT_REFERENCE_INVALID')
            now = timezone.now()
            await db.execute(
                text(
                    """
                    INSERT INTO hasn_storage_reservations
                        (reservation_id, owner_hasn_id, object_id, result_asset_id,
                         idempotency_key, request_fingerprint, reserved_bytes, status,
                         expires_time, created_time, updated_time)
                    VALUES
                        (:reservation_id, :owner, :reservation_object_id, :asset_id,
                         :idempotency_key, :request_fingerprint, :reserved_bytes, 'committed',
                         :now, :now, :now)
                    """
                ),
                {
                    'reservation_id': f'res_{uuid4().hex}',
                    'owner': owner_hasn_id,
                    'reservation_object_id': f'obj_{uuid4().hex}',
                    'asset_id': result.asset_id,
                    'idempotency_key': idempotency_key,
                    'request_fingerprint': request_fingerprint,
                    'reserved_bytes': int(obj['size_bytes']),
                    'now': now,
                },
            )
            return result

    async def start_multipart(
        self,
        *,
        owner_hasn_id: str,
        declared_size: int,
        filename: str,
        mime: str,
        category: str,
        source_app: str,
        idempotency_key: str,
        parent_entry_id: str | None = None,
    ) -> dict[str, Any]:
        """预占配额并在供应商侧创建受控 multipart 会话。"""
        policy = resolve_owner_category(category)
        policy.assert_upload_allowed(mime=mime, size_bytes=declared_size)
        display_name_for_upload(filename)
        async with self._sessions() as db:
            await self._assert_folder_parent(
                db,
                owner_hasn_id=owner_hasn_id,
                parent_entry_id=parent_entry_id,
            )
        if not idempotency_key or len(idempotency_key) > 128:
            raise errors.RequestError(
                code=StandardResponseCode.HTTP_422,
                msg='STORAGE_IDEMPOTENCY_KEY_INVALID',
            )
        request_fingerprint = _request_fingerprint(
            content_sha256=None,
            filename=filename,
            mime=mime,
            category=category,
            source_app=source_app,
            parent_entry_id=parent_entry_id,
        )
        reservation = await self.reserve(
            owner_hasn_id=owner_hasn_id,
            requested_bytes=declared_size,
            idempotency_key=idempotency_key,
            request_fingerprint=request_fingerprint,
        )
        if reservation.status == 'committed' and reservation.result_asset_id:
            return {
                'status': 'succeeded',
                'asset_id': reservation.result_asset_id,
                'upload_id': None,
                'reservation_id': reservation.reservation_id,
            }
        existing_job = await self._multipart_job_by_idempotency(
            owner_hasn_id=owner_hasn_id,
            idempotency_key=idempotency_key,
        )
        if existing_job is not None:
            return self._multipart_session_response(existing_job)
        if reservation.status != 'reserved':
            raise errors.ConflictError(
                msg='STORAGE_RESERVATION_NOT_ACTIVE',
                data={'status': reservation.status},
            )

        async with self._sessions() as db:
            storage = await self._write_storage_for_owner(
                db,
                owner_hasn_id=owner_hasn_id,
                access=policy.access,
            )
        object_key = build_owner_object_key(
            owner_hasn_id=owner_hasn_id,
            access=policy.access,
            object_id=reservation.object_id,
        )
        provider_upload_id: str | None = None
        job_id = f'mpu_{uuid4().hex}'
        try:
            provider_upload_id = await StorageService.create_multipart_on_storage(
                storage,
                object_key=object_key,
                content_type=mime,
            )
            payload = {
                'reservation_id': reservation.reservation_id,
                'provider_upload_id': provider_upload_id,
                'storage_id': int(storage.id),
                'object_key': object_key,
                'declared_size': declared_size,
                'filename': filename,
                'mime': mime,
                'category': category,
                'source_app': source_app,
                'idempotency_key': idempotency_key,
                'parent_entry_id': parent_entry_id,
                'parts': [],
            }
            now = timezone.now()
            async with self._sessions.begin() as db:
                await db.execute(
                    text(
                        """
                        INSERT INTO hasn_storage_jobs
                            (job_id, owner_hasn_id, job_type, status, cursor, total_items,
                             processed_items, failed_items, payload, result, attempt_count,
                             expires_time, created_time, updated_time)
                        VALUES
                            (:job_id, :owner, 'multipart_abort_sweep', 'running', '{}'::jsonb, 0,
                             0, 0, CAST(:payload AS jsonb), '{}'::jsonb, 0,
                             :expires_time, :now, :now)
                        """
                    ),
                    {
                        'job_id': job_id,
                        'owner': owner_hasn_id,
                        'payload': json.dumps(payload),
                        'expires_time': reservation.expires_time,
                        'now': now,
                    },
                )
            return {
                'status': 'running',
                'upload_id': job_id,
                'reservation_id': reservation.reservation_id,
                'declared_size': declared_size,
                'expires_time': reservation.expires_time,
                'parts': [],
            }
        except Exception:
            if provider_upload_id is not None:
                try:
                    await StorageService.abort_multipart_on_storage(
                        storage,
                        object_key=object_key,
                        upload_id=provider_upload_id,
                    )
                except Exception as abort_exc:
                    log.error(
                        f'multipart 初始化补偿终止失败: {type(abort_exc).__name__}: {abort_exc!r}'
                    )
            await self.release_reservation(reservation.reservation_id)
            raise

    async def upload_multipart_part(
        self,
        *,
        owner_hasn_id: str,
        upload_id: str,
        part_number: int,
        file: BinaryIO,
        size: int,
    ) -> dict[str, Any]:
        """上传一个分片；网络 I/O 全程不持有数据库事务。"""
        job = await self._multipart_job(owner_hasn_id=owner_hasn_id, upload_id=upload_id)
        if job['status'] != 'running' or job['expires_time'] <= timezone.now():
            raise errors.ConflictError(msg='STORAGE_MULTIPART_NOT_ACTIVE')
        actual_size, part_sha256 = await self._measure_multipart_part(file)
        if actual_size != size:
            raise errors.RequestError(
                code=StandardResponseCode.HTTP_422,
                msg='STORAGE_MULTIPART_PART_SIZE_MISMATCH',
                data={'declared_bytes': size, 'actual_bytes': actual_size},
            )
        payload = job['payload']
        existing_parts = {
            int(part['part_number']): dict(part) for part in payload.get('parts', [])
        }
        projected_size = (
            sum(int(part['size_bytes']) for number, part in existing_parts.items() if number != part_number)
            + actual_size
        )
        policy = resolve_owner_category(str(payload['category']))
        declared_size = int(payload['declared_size'])
        allowed_deviation = max(int(declared_size * 0.1), 64 * 1024**2)
        if projected_size > policy.max_size_bytes:
            await self.abort_multipart(owner_hasn_id=owner_hasn_id, upload_id=upload_id)
            raise errors.RequestError(
                code=StandardResponseCode.HTTP_413,
                msg='STORAGE_FILE_TOO_LARGE',
                data={'max_size_bytes': policy.max_size_bytes, 'requested_bytes': projected_size},
            )
        if projected_size - declared_size > allowed_deviation:
            await self.abort_multipart(owner_hasn_id=owner_hasn_id, upload_id=upload_id)
            raise errors.RequestError(
                code=StandardResponseCode.HTTP_422,
                msg='STORAGE_DECLARED_SIZE_MISMATCH',
                data={'declared_bytes': declared_size, 'actual_bytes': projected_size},
            )

        async with self._sessions() as db:
            storage = copy.copy(await StorageService.get_storage(db, int(payload['storage_id'])))
        etag = await StorageService.upload_multipart_part_on_storage(
            storage,
            object_key=str(payload['object_key']),
            upload_id=str(payload['provider_upload_id']),
            part_number=part_number,
            file=file,
            size=actual_size,
        )
        part = {
            'part_number': part_number,
            'etag': etag,
            'size_bytes': actual_size,
            'sha256': part_sha256,
        }
        async with self._sessions.begin() as db:
            locked = (
                await db.execute(
                    text(
                        """
                        SELECT status, payload
                        FROM hasn_storage_jobs
                        WHERE job_id = :job_id AND owner_hasn_id = :owner
                        FOR UPDATE
                        """
                    ),
                    {'job_id': upload_id, 'owner': owner_hasn_id},
                )
            ).mappings().one_or_none()
            if locked is None:
                raise errors.NotFoundError(msg='STORAGE_MULTIPART_NOT_FOUND')
            if str(locked['status']) != 'running':
                raise errors.ConflictError(msg='STORAGE_MULTIPART_NOT_ACTIVE')
            locked_payload = dict(locked['payload'])
            parts = {
                int(item['part_number']): dict(item) for item in locked_payload.get('parts', [])
            }
            parts[part_number] = part
            locked_payload['parts'] = [parts[number] for number in sorted(parts)]
            await db.execute(
                text(
                    """
                    UPDATE hasn_storage_jobs
                    SET payload = CAST(:payload AS jsonb),
                        processed_items = :processed_items,
                        updated_time = :now
                    WHERE job_id = :job_id
                    """
                ),
                {
                    'payload': json.dumps(locked_payload),
                    'processed_items': len(parts),
                    'now': timezone.now(),
                    'job_id': upload_id,
                },
            )
        return part

    async def complete_multipart(
        self,
        *,
        owner_hasn_id: str,
        upload_id: str,
    ) -> StoredAsset:
        """校准分片总量、完成供应商会话并原子登记资产。"""
        job = await self._multipart_job(owner_hasn_id=owner_hasn_id, upload_id=upload_id)
        if job['status'] == 'succeeded':
            asset_id = job['result'].get('asset_id')
            if asset_id:
                async with self._sessions() as db:
                    replay = await self._asset_by_id_in_transaction(db, owner_hasn_id, str(asset_id))
                if replay is not None:
                    return replay
        if job['status'] != 'running' or job['expires_time'] <= timezone.now():
            raise errors.ConflictError(msg='STORAGE_MULTIPART_NOT_ACTIVE')
        payload = job['payload']
        parts = sorted(
            (dict(item) for item in payload.get('parts', [])),
            key=lambda item: int(item['part_number']),
        )
        if not parts or [int(part['part_number']) for part in parts] != list(range(1, len(parts) + 1)):
            raise errors.RequestError(
                code=StandardResponseCode.HTTP_422,
                msg='STORAGE_MULTIPART_PARTS_INCOMPLETE',
            )
        actual_size = sum(int(part['size_bytes']) for part in parts)
        declared_size = int(payload['declared_size'])
        allowed_deviation = max(int(declared_size * 0.1), 64 * 1024**2)
        if abs(actual_size - declared_size) > allowed_deviation:
            await self.abort_multipart(owner_hasn_id=owner_hasn_id, upload_id=upload_id)
            raise errors.RequestError(
                code=StandardResponseCode.HTTP_422,
                msg='STORAGE_DECLARED_SIZE_MISMATCH',
                data={'declared_bytes': declared_size, 'actual_bytes': actual_size},
            )
        try:
            await self._resize_reservation(
                reservation_id=str(payload['reservation_id']),
                actual_bytes=actual_size,
            )
        except Exception:
            try:
                await self.abort_multipart(owner_hasn_id=owner_hasn_id, upload_id=upload_id)
            except Exception as abort_exc:
                log.error(
                    f'multipart 配额校准失败后的补偿终止失败: '
                    f'{type(abort_exc).__name__}: {abort_exc!r}'
                )
            raise
        reservation = await self._reservation_by_idempotency(
            owner_hasn_id,
            str(payload['idempotency_key']),
        )
        if reservation is None:
            raise errors.ServerError(msg='STORAGE_RESERVATION_NOT_FOUND')
        async with self._sessions() as db:
            storage = copy.copy(await StorageService.get_storage(db, int(payload['storage_id'])))

        provider_completed = False
        try:
            await StorageService.complete_multipart_on_storage(
                storage,
                object_key=str(payload['object_key']),
                upload_id=str(payload['provider_upload_id']),
                parts=[(int(part['part_number']), str(part['etag'])) for part in parts],
            )
            provider_completed = True
            stat = await StorageService.stat_on_storage(
                storage,
                object_key=str(payload['object_key']),
            )
            if stat.size != actual_size:
                raise errors.GatewayError(
                    msg='STORAGE_UPLOAD_SIZE_MISMATCH',
                    data={'expected_bytes': actual_size, 'actual_bytes': stat.size},
                )
            sha256, hashed_size = await StorageService.sha256_on_storage(
                storage,
                object_key=str(payload['object_key']),
            )
            if hashed_size != actual_size:
                raise errors.GatewayError(msg='STORAGE_UPLOAD_HASH_MISMATCH')
            stored = await self._finalize_uploaded_asset(
                reservation=reservation,
                storage_id=int(payload['storage_id']),
                object_key=str(payload['object_key']),
                policy=resolve_owner_category(str(payload['category'])),
                sha256=sha256,
                size_bytes=actual_size,
                filename=str(payload['filename']),
                mime=str(payload['mime']),
                category=str(payload['category']),
                source_app=str(payload['source_app']),
                idempotency_key=str(payload['idempotency_key']),
                width=None,
                height=None,
                duration_ms=None,
                extract_status='done' if payload['category'] == 'published_artifact' else None,
                parent_entry_id=(
                    str(payload['parent_entry_id'])
                    if payload.get('parent_entry_id') is not None
                    else None
                ),
                multipart_job_id=upload_id,
                multipart_part_count=len(parts),
            )
            return stored
        except Exception as exc:
            if provider_completed:
                await self._record_orphan_cleanup(
                    owner_hasn_id=owner_hasn_id,
                    storage_id=int(payload['storage_id']),
                    object_key=str(payload['object_key']),
                    reservation_id=str(payload['reservation_id']),
                    reason=(exc.msg or type(exc).__name__)
                    if isinstance(exc, errors.BaseExceptionError)
                    else type(exc).__name__,
                )
                await self.release_reservation(str(payload['reservation_id']))
                await self._mark_multipart_failed(upload_id=upload_id, owner_hasn_id=owner_hasn_id)
            raise

    async def abort_multipart(
        self,
        *,
        owner_hasn_id: str,
        upload_id: str,
    ) -> dict[str, str]:
        """终止供应商 multipart 会话；成功后才释放预占。"""
        job = await self._multipart_job(owner_hasn_id=owner_hasn_id, upload_id=upload_id)
        if job['status'] in {'cancelled', 'failed'}:
            return {'upload_id': upload_id, 'status': str(job['status'])}
        if job['status'] == 'succeeded':
            raise errors.ConflictError(msg='STORAGE_MULTIPART_ALREADY_COMPLETED')
        payload = job['payload']
        async with self._sessions() as db:
            storage = copy.copy(await StorageService.get_storage(db, int(payload['storage_id'])))
        try:
            await StorageService.abort_multipart_on_storage(
                storage,
                object_key=str(payload['object_key']),
                upload_id=str(payload['provider_upload_id']),
            )
        except Exception:
            async with self._sessions.begin() as db:
                await db.execute(
                    text(
                        """
                        UPDATE hasn_storage_jobs
                        SET status = 'retrying',
                            attempt_count = attempt_count + 1,
                            next_attempt_time = :retry_time,
                            error_code = 'STORAGE_MULTIPART_ABORT_FAILED',
                            updated_time = :now
                        WHERE job_id = :job_id AND owner_hasn_id = :owner
                        """
                    ),
                    {
                        'retry_time': timezone.now() + timedelta(minutes=5),
                        'now': timezone.now(),
                        'job_id': upload_id,
                        'owner': owner_hasn_id,
                    },
                )
            raise
        await self.release_reservation(str(payload['reservation_id']))
        async with self._sessions.begin() as db:
            await db.execute(
                text(
                    """
                    UPDATE hasn_storage_jobs
                    SET status = 'cancelled',
                        error_code = NULL,
                        next_attempt_time = NULL,
                        updated_time = :now
                    WHERE job_id = :job_id AND owner_hasn_id = :owner
                    """
                ),
                {'now': timezone.now(), 'job_id': upload_id, 'owner': owner_hasn_id},
            )
        return {'upload_id': upload_id, 'status': 'cancelled'}

    async def _multipart_job(
        self,
        *,
        owner_hasn_id: str,
        upload_id: str,
    ) -> dict[str, Any]:
        async with self._sessions() as db:
            row = (
                await db.execute(
                    text(
                        """
                        SELECT job_id, owner_hasn_id, status, payload, result, expires_time
                        FROM hasn_storage_jobs
                        WHERE job_id = :job_id
                          AND owner_hasn_id = :owner
                          AND job_type = 'multipart_abort_sweep'
                        """
                    ),
                    {'job_id': upload_id, 'owner': owner_hasn_id},
                )
            ).mappings().one_or_none()
        if row is None:
            raise errors.NotFoundError(msg='STORAGE_MULTIPART_NOT_FOUND')
        return {
            'job_id': str(row['job_id']),
            'owner_hasn_id': str(row['owner_hasn_id']),
            'status': str(row['status']),
            'payload': dict(row['payload']),
            'result': dict(row['result']),
            'expires_time': row['expires_time'],
        }

    async def _multipart_job_by_idempotency(
        self,
        *,
        owner_hasn_id: str,
        idempotency_key: str,
    ) -> dict[str, Any] | None:
        async with self._sessions() as db:
            row = (
                await db.execute(
                    text(
                        """
                        SELECT job_id, owner_hasn_id, status, payload, result, expires_time
                        FROM hasn_storage_jobs
                        WHERE owner_hasn_id = :owner
                          AND job_type = 'multipart_abort_sweep'
                          AND payload ->> 'idempotency_key' = :idempotency_key
                        ORDER BY id DESC
                        LIMIT 1
                        """
                    ),
                    {'owner': owner_hasn_id, 'idempotency_key': idempotency_key},
                )
            ).mappings().one_or_none()
        if row is None:
            return None
        return {
            'job_id': str(row['job_id']),
            'owner_hasn_id': str(row['owner_hasn_id']),
            'status': str(row['status']),
            'payload': dict(row['payload']),
            'result': dict(row['result']),
            'expires_time': row['expires_time'],
        }

    @staticmethod
    def _multipart_session_response(job: dict[str, Any]) -> dict[str, Any]:
        payload = job['payload']
        return {
            'status': job['status'],
            'asset_id': job['result'].get('asset_id'),
            'upload_id': job['job_id'],
            'reservation_id': payload.get('reservation_id'),
            'declared_size': payload.get('declared_size'),
            'expires_time': job['expires_time'],
            'parts': payload.get('parts', []),
        }

    @staticmethod
    async def _measure_multipart_part(file: BinaryIO) -> tuple[int, str]:
        def measure() -> tuple[int, str]:
            file.seek(0)
            digest = hashlib.sha256()
            size = 0
            while chunk := file.read(_UPLOAD_CHUNK_SIZE):
                digest.update(chunk)
                size += len(chunk)
            file.seek(0)
            return size, digest.hexdigest()

        return await asyncio.to_thread(measure)

    async def _resize_reservation(self, *, reservation_id: str, actual_bytes: int) -> None:
        """按完成前真实分片总量调整预占，增量仍受配额原子门禁。"""
        if actual_bytes <= 0:
            raise errors.RequestError(msg='STORAGE_RESERVATION_SIZE_INVALID')
        async with self._sessions.begin() as db:
            reservation = await self._locked_reservation(db, reservation_id)
            if reservation.status != 'reserved':
                raise errors.ConflictError(msg='STORAGE_RESERVATION_NOT_ACTIVE')
            delta = actual_bytes - reservation.reserved_bytes
            if delta == 0:
                return
            updated = await db.execute(
                text(
                    """
                    UPDATE hasn_storage_accounts
                    SET reserved_bytes = reserved_bytes + :delta,
                        state = CASE
                            WHEN used_bytes + reserved_bytes + :delta <= quota_bytes
                            THEN 'active' ELSE 'over_quota'
                        END,
                        updated_time = :now
                    WHERE owner_hasn_id = :owner
                      AND reserved_bytes + :delta >= 0
                      AND used_bytes + reserved_bytes + :delta <= quota_bytes
                    """
                ),
                {
                    'delta': delta,
                    'now': timezone.now(),
                    'owner': reservation.owner_hasn_id,
                },
            )
            if updated.rowcount != 1:
                current = (
                    await db.execute(
                        text(
                            """
                            SELECT quota_bytes, used_bytes, reserved_bytes
                            FROM hasn_storage_accounts
                            WHERE owner_hasn_id = :owner
                            """
                        ),
                        {'owner': reservation.owner_hasn_id},
                    )
                ).mappings().one_or_none()
                if current is None:
                    raise errors.ServerError(msg='STORAGE_ACCOUNT_NOT_READY')
                raise errors.RequestError(
                    code=StandardResponseCode.HTTP_507,
                    msg='STORAGE_QUOTA_EXCEEDED',
                    data={
                        'quota_bytes': int(current['quota_bytes']),
                        'used_bytes': int(current['used_bytes']),
                        'reserved_bytes': int(current['reserved_bytes']),
                        'requested_bytes': actual_bytes,
                    },
                )
            await db.execute(
                text(
                    """
                    UPDATE hasn_storage_reservations
                    SET reserved_bytes = :actual_bytes, updated_time = :now
                    WHERE reservation_id = :reservation_id
                    """
                ),
                {
                    'actual_bytes': actual_bytes,
                    'now': timezone.now(),
                    'reservation_id': reservation_id,
                },
            )

    async def _mark_multipart_failed(self, *, upload_id: str, owner_hasn_id: str) -> None:
        async with self._sessions.begin() as db:
            await db.execute(
                text(
                    """
                    UPDATE hasn_storage_jobs
                    SET status = 'failed',
                        failed_items = failed_items + 1,
                        error_code = 'STORAGE_MULTIPART_FINALIZE_FAILED',
                        updated_time = :now
                    WHERE job_id = :job_id AND owner_hasn_id = :owner
                    """
                ),
                {'now': timezone.now(), 'job_id': upload_id, 'owner': owner_hasn_id},
            )

    async def upload(
        self,
        *,
        owner_hasn_id: str,
        chunks: AsyncIterable[bytes],
        declared_size: int | None,
        filename: str,
        mime: str,
        category: str,
        source_app: str,
        idempotency_key: str,
        width: int | None = None,
        height: int | None = None,
        duration_ms: int | None = None,
        extract_status: str | None = None,
    ) -> StoredAsset:
        """流式落临时区、同 Owner 去重、预占、真实写对象并原子登记两层模型。"""
        policy = resolve_owner_category(category)
        if not idempotency_key or len(idempotency_key) > 128:
            raise errors.RequestError(
                code=StandardResponseCode.HTTP_422,
                msg='STORAGE_IDEMPOTENCY_KEY_INVALID',
            )

        usage = await self.usage(owner_hasn_id=owner_hasn_id)
        if declared_size is not None and (declared_size <= 0 or declared_size > policy.max_size_bytes):
            policy.assert_upload_allowed(mime=mime, size_bytes=declared_size)
        existing_before_staging = await self._reservation_by_idempotency(
            owner_hasn_id,
            idempotency_key,
        )
        remaining_bytes = max(
            0,
            usage.quota_bytes - usage.used_bytes - usage.reserved_bytes,
        )
        if (
            existing_before_staging is None
            and declared_size is not None
            and declared_size > remaining_bytes
        ):
            self._raise_quota_exceeded(usage, declared_size)

        staged = await self._stage_upload(
            chunks,
            hard_limit=(
                policy.max_size_bytes
                if existing_before_staging is not None
                else min(policy.max_size_bytes, remaining_bytes)
            ),
            policy=policy,
            mime=mime,
            declared_size=declared_size,
            usage=usage,
        )
        reservation: StorageReservation | None = None
        storage: Any = None
        object_key: str | None = None
        uploaded = False
        try:
            request_fingerprint = _request_fingerprint(
                content_sha256=staged.sha256,
                filename=filename,
                mime=mime,
                category=category,
                source_app=source_app,
                width=width,
                height=height,
                duration_ms=duration_ms,
            )
            existing_reservation = await self._reservation_by_idempotency(
                owner_hasn_id,
                idempotency_key,
            )
            if existing_reservation is not None:
                self._validate_replay(
                    existing_reservation,
                    staged.size_bytes,
                    request_fingerprint=request_fingerprint,
                )
                replay = await self._uploaded_asset_by_idempotency(
                    owner_hasn_id,
                    idempotency_key,
                )
                if replay is not None:
                    return replay

            duplicate = await self._create_asset_from_existing_object(
                owner_hasn_id=owner_hasn_id,
                sha256=staged.sha256,
                access=policy.access,
                filename=filename,
                mime=mime,
                category=category,
                source_app=source_app,
                idempotency_key=idempotency_key,
                request_fingerprint=request_fingerprint,
                width=width,
                height=height,
                duration_ms=duration_ms,
                extract_status=extract_status,
            )
            if duplicate is not None:
                return duplicate

            reservation = await self.reserve(
                owner_hasn_id=owner_hasn_id,
                requested_bytes=staged.size_bytes,
                idempotency_key=idempotency_key,
                request_fingerprint=request_fingerprint,
            )
            if reservation.status == 'committed' and reservation.result_asset_id:
                replay = await self._uploaded_asset_by_idempotency(owner_hasn_id, idempotency_key)
                if replay is None:
                    raise errors.ConflictError(
                        msg='STORAGE_IDEMPOTENCY_RESULT_UNAVAILABLE',
                        data={'asset_id': reservation.result_asset_id},
                    )
                return replay
            if reservation.status != 'reserved':
                raise errors.ConflictError(
                    msg='STORAGE_RESERVATION_NOT_ACTIVE',
                    data={'status': reservation.status},
                )

            async with self._sessions() as db:
                storage = await self._write_storage_for_owner(
                    db,
                    owner_hasn_id=owner_hasn_id,
                    access=policy.access,
                )
            object_key = build_owner_object_key(
                owner_hasn_id=owner_hasn_id,
                access=policy.access,
                object_id=reservation.object_id,
            )
            await StorageService.upload_stream_to_storage(
                storage,
                self._read_staged_file(staged.path),
                size=staged.size_bytes,
                key=object_key,
                content_type=mime,
            )
            uploaded = True
            stat = await StorageService.stat_on_storage(storage, object_key=object_key)
            if stat.size != staged.size_bytes:
                raise errors.GatewayError(
                    msg='STORAGE_UPLOAD_SIZE_MISMATCH',
                    data={'expected_bytes': staged.size_bytes, 'actual_bytes': stat.size},
                )
            stored_sha, stored_size = await StorageService.sha256_on_storage(storage, object_key=object_key)
            if stored_size != staged.size_bytes or stored_sha != staged.sha256:
                raise errors.GatewayError(msg='STORAGE_UPLOAD_HASH_MISMATCH')

            return await self._finalize_uploaded_asset(
                reservation=reservation,
                storage_id=int(storage.id),
                object_key=object_key,
                policy=policy,
                sha256=staged.sha256,
                size_bytes=staged.size_bytes,
                filename=filename,
                mime=mime,
                category=category,
                source_app=source_app,
                idempotency_key=idempotency_key,
                width=width,
                height=height,
                duration_ms=duration_ms,
                extract_status=extract_status,
            )
        except Exception as exc:
            if reservation is not None and reservation.status == 'reserved':
                if uploaded and storage is not None and object_key is not None:
                    await self._record_orphan_cleanup(
                        owner_hasn_id=owner_hasn_id,
                        storage_id=int(storage.id),
                        object_key=object_key,
                        reservation_id=reservation.reservation_id,
                        reason=(exc.msg or type(exc).__name__)
                        if isinstance(exc, errors.BaseExceptionError)
                        else type(exc).__name__,
                    )
                await self.release_reservation(reservation.reservation_id)
            if isinstance(exc, errors.BaseExceptionError):
                raise
            log.exception(f'用户云存储上传失败: {type(exc).__name__}: {exc!r}')
            raise errors.GatewayError(msg='STORAGE_UPLOAD_FAILED') from exc
        finally:
            await self._discard_staged_upload(staged)

    async def upload_bytes(
        self,
        *,
        owner_hasn_id: str,
        data: bytes,
        filename: str,
        mime: str,
        category: str,
        source_app: str,
        idempotency_key: str,
        width: int | None = None,
        height: int | None = None,
        duration_ms: int | None = None,
        extract_status: str | None = None,
    ) -> StoredAsset:
        """把既有内存字节接入统一编排；不会绕过配额、哈希或对象校验。"""

        async def chunks() -> AsyncIterator[bytes]:
            yield data

        return await self.upload(
            owner_hasn_id=owner_hasn_id,
            chunks=chunks(),
            declared_size=len(data),
            filename=filename,
            mime=mime,
            category=category,
            source_app=source_app,
            idempotency_key=idempotency_key,
            width=width,
            height=height,
            duration_ms=duration_ms,
            extract_status=extract_status,
        )

    async def asset_content_sha256(self, *, owner_hasn_id: str, asset_id: str) -> str:
        """读取本人逻辑资产对应的服务端权威内容哈希。"""
        async with self._sessions() as db:
            sha256 = (
                await db.execute(
                    text(
                        """
                        SELECT o.sha256
                        FROM hasn_assets AS a
                        JOIN hasn_storage_objects AS o ON o.object_id = a.object_id
                        WHERE a.owner_hasn_id = :owner
                          AND a.asset_id = :asset_id
                          AND a.lifecycle_status = 'active'
                          AND o.state = 'active'
                        """
                    ),
                    {'owner': owner_hasn_id, 'asset_id': asset_id},
                )
            ).scalar_one_or_none()
        if sha256 is None or not str(sha256).strip():
            raise errors.ServerError(msg='STORAGE_OBJECT_HASH_MISSING')
        return str(sha256).strip()

    @staticmethod
    def _raise_quota_exceeded(usage: StorageUsage, requested_bytes: int) -> None:
        raise errors.RequestError(
            code=StandardResponseCode.HTTP_507,
            msg='STORAGE_QUOTA_EXCEEDED',
            data={
                'quota_bytes': usage.quota_bytes,
                'used_bytes': usage.used_bytes,
                'reserved_bytes': usage.reserved_bytes,
                'requested_bytes': requested_bytes,
            },
        )

    @staticmethod
    async def _stage_upload(
        chunks: AsyncIterable[bytes],
        *,
        hard_limit: int,
        policy: CategoryPolicy,
        mime: str,
        declared_size: int | None,
        usage: StorageUsage,
    ) -> _StagedUpload:
        """在预占前有界落临时区，同时计算服务端权威大小与 SHA-256。"""
        global _temp_bytes_in_use

        file_descriptor, raw_path = tempfile.mkstemp(prefix='hasn-owner-storage-', suffix='.upload')
        path = Path(raw_path)
        digest = hashlib.sha256()
        size_bytes = 0
        reserved_temp_bytes = 0
        file_obj = os.fdopen(file_descriptor, 'wb')
        try:
            async for chunk in chunks:
                if not isinstance(chunk, bytes):
                    raise errors.RequestError(
                        code=StandardResponseCode.HTTP_422,
                        msg='STORAGE_UPLOAD_CHUNK_INVALID',
                    )
                if not chunk:
                    continue
                next_size = size_bytes + len(chunk)
                if next_size > hard_limit:
                    if hard_limit < policy.max_size_bytes:
                        OwnerStorageService._raise_quota_exceeded(usage, next_size)
                    raise errors.RequestError(
                        code=StandardResponseCode.HTTP_413,
                        msg='STORAGE_FILE_TOO_LARGE',
                        data={'max_size_bytes': policy.max_size_bytes, 'requested_bytes': next_size},
                    )
                async with _temp_capacity_lock:
                    if _temp_bytes_in_use + len(chunk) > _temp_capacity_bytes():
                        raise errors.RequestError(
                            code=StandardResponseCode.HTTP_503,
                            msg='STORAGE_TEMP_CAPACITY_EXHAUSTED',
                        )
                    _temp_bytes_in_use += len(chunk)
                    reserved_temp_bytes += len(chunk)
                await asyncio.to_thread(file_obj.write, chunk)
                digest.update(chunk)
                size_bytes = next_size
            await asyncio.to_thread(file_obj.flush)
            await asyncio.to_thread(os.fsync, file_obj.fileno())
            if size_bytes <= 0:
                raise errors.RequestError(
                    code=StandardResponseCode.HTTP_422,
                    msg='STORAGE_FILE_EMPTY',
                )
            policy.assert_upload_allowed(mime=mime, size_bytes=size_bytes)
            if declared_size is not None and declared_size != size_bytes:
                allowed_deviation = max(int(declared_size * 0.1), 64 * 1024**2)
                if abs(declared_size - size_bytes) > allowed_deviation:
                    raise errors.RequestError(
                        code=StandardResponseCode.HTTP_422,
                        msg='STORAGE_DECLARED_SIZE_MISMATCH',
                        data={'declared_bytes': declared_size, 'actual_bytes': size_bytes},
                    )
            return _StagedUpload(path=path, size_bytes=size_bytes, sha256=digest.hexdigest())
        except Exception:
            file_obj.close()
            path.unlink(missing_ok=True)
            async with _temp_capacity_lock:
                _temp_bytes_in_use = max(0, _temp_bytes_in_use - reserved_temp_bytes)
            raise
        finally:
            if not file_obj.closed:
                file_obj.close()

    @staticmethod
    async def _read_staged_file(path: Path) -> AsyncIterator[bytes]:
        file_obj = await asyncio.to_thread(path.open, 'rb')
        try:
            while True:
                chunk = await asyncio.to_thread(file_obj.read, _UPLOAD_CHUNK_SIZE)
                if not chunk:
                    break
                yield chunk
        finally:
            await asyncio.to_thread(file_obj.close)

    @staticmethod
    async def _discard_staged_upload(staged: _StagedUpload) -> None:
        global _temp_bytes_in_use

        await asyncio.to_thread(staged.path.unlink, missing_ok=True)
        async with _temp_capacity_lock:
            _temp_bytes_in_use = max(0, _temp_bytes_in_use - staged.size_bytes)

    async def _uploaded_asset_by_idempotency(
        self,
        owner_hasn_id: str,
        idempotency_key: str,
    ) -> StoredAsset | None:
        async with self._sessions() as db:
            row = (
                await db.execute(
                    text(
                        """
                        SELECT a.asset_id, a.object_id, e.entry_id, a.kind, a.mime,
                               o.size_bytes, e.display_name
                        FROM hasn_assets AS a
                        JOIN hasn_storage_objects AS o ON o.object_id = a.object_id
                        JOIN hasn_storage_entries AS e ON e.asset_id = a.asset_id
                        WHERE a.owner_hasn_id = :owner
                          AND a.upload_idempotency_key = :idempotency_key
                          AND a.lifecycle_status <> 'deleted'
                        """
                    ),
                    {'owner': owner_hasn_id, 'idempotency_key': idempotency_key},
                )
            ).mappings().one_or_none()
            return _stored_asset_of(row, deduplicated=False) if row is not None else None

    async def _create_asset_from_existing_object(
        self,
        *,
        owner_hasn_id: str,
        sha256: str,
        access: str,
        filename: str,
        mime: str,
        category: str,
        source_app: str,
        idempotency_key: str,
        request_fingerprint: str,
        width: int | None,
        height: int | None,
        duration_ms: int | None,
        extract_status: str | None,
    ) -> StoredAsset | None:
        """命中同 Owner 哈希时只增逻辑资产与引用计数，不预占、不写对象存储。"""
        async with self._sessions.begin() as db:
            await self._lock_upload_idempotency(db, owner_hasn_id, idempotency_key)
            existing_reservation = await self._reservation_by_idempotency_in_transaction(
                db,
                owner_hasn_id,
                idempotency_key,
            )
            if existing_reservation is not None:
                self._validate_replay(
                    existing_reservation,
                    existing_reservation.reserved_bytes,
                    request_fingerprint=request_fingerprint,
                )
            replay = await self._uploaded_asset_by_idempotency_in_transaction(
                db,
                owner_hasn_id,
                idempotency_key,
            )
            if replay is not None:
                return replay
            await self._lock_content_hash(db, owner_hasn_id, sha256)
            obj = (
                await db.execute(
                    text(
                        """
                        SELECT object_id, storage_id, object_key, access, size_bytes
                        FROM hasn_storage_objects
                        WHERE owner_hasn_id = :owner
                          AND sha256 = :sha256
                          AND access = :access
                          AND billable_to_owner
                          AND state = 'active'
                        FOR UPDATE
                        """
                    ),
                    {'owner': owner_hasn_id, 'sha256': sha256, 'access': access},
                )
            ).mappings().one_or_none()
            if obj is None:
                return None
            result = await self._insert_logical_asset(
                db,
                owner_hasn_id=owner_hasn_id,
                object_row=obj,
                filename=filename,
                mime=mime,
                category=category,
                source_app=source_app,
                idempotency_key=idempotency_key,
                width=width,
                height=height,
                duration_ms=duration_ms,
                extract_status=extract_status,
                deduplicated=True,
            )
            updated = await db.execute(
                text(
                    """
                    UPDATE hasn_storage_objects
                    SET ref_count = ref_count + 1, updated_time = :now
                    WHERE object_id = :object_id AND state = 'active'
                    """
                ),
                {'object_id': str(obj['object_id']), 'now': timezone.now()},
            )
            if updated.rowcount != 1:
                raise errors.ServerError(msg='STORAGE_OBJECT_REFERENCE_INVALID')
            now = timezone.now()
            await db.execute(
                text(
                    """
                    INSERT INTO hasn_storage_reservations
                        (reservation_id, owner_hasn_id, object_id, result_asset_id,
                         idempotency_key, request_fingerprint, reserved_bytes, status,
                         expires_time, created_time, updated_time)
                    VALUES
                        (:reservation_id, :owner, :reservation_object_id, :asset_id,
                         :idempotency_key, :request_fingerprint, :reserved_bytes, 'committed',
                         :now, :now, :now)
                    """
                ),
                {
                    'reservation_id': f'res_{uuid4().hex}',
                    'owner': owner_hasn_id,
                    'reservation_object_id': f'obj_{uuid4().hex}',
                    'asset_id': result.asset_id,
                    'idempotency_key': idempotency_key,
                    'request_fingerprint': request_fingerprint,
                    'reserved_bytes': int(obj['size_bytes']),
                    'now': now,
                },
            )
            return result

    async def _finalize_uploaded_asset(
        self,
        *,
        reservation: StorageReservation,
        storage_id: int,
        object_key: str,
        policy: CategoryPolicy,
        sha256: str,
        size_bytes: int,
        filename: str,
        mime: str,
        category: str,
        source_app: str,
        idempotency_key: str,
        width: int | None,
        height: int | None,
        duration_ms: int | None,
        extract_status: str | None,
        parent_entry_id: str | None = None,
        display_name_override: str | None = None,
        derived_from_asset_id: str | None = None,
        multipart_job_id: str | None = None,
        multipart_part_count: int | None = None,
    ) -> StoredAsset:
        """事务 B：对象、资产、目录、预占和账户五项原子提交。"""
        async with self._sessions.begin() as db:
            await self._lock_upload_idempotency(db, reservation.owner_hasn_id, idempotency_key)
            replay = await self._uploaded_asset_by_idempotency_in_transaction(
                db,
                reservation.owner_hasn_id,
                idempotency_key,
            )
            if replay is not None:
                return replay
            locked_reservation = await self._locked_reservation(db, reservation.reservation_id)
            if locked_reservation.status != 'reserved':
                if locked_reservation.status == 'committed' and locked_reservation.result_asset_id:
                    replay = await self._asset_by_id_in_transaction(
                        db,
                        reservation.owner_hasn_id,
                        locked_reservation.result_asset_id,
                    )
                    if replay is not None:
                        return replay
                raise errors.ConflictError(
                    msg='STORAGE_RESERVATION_NOT_ACTIVE',
                    data={'status': locked_reservation.status},
                )
            if locked_reservation.reserved_bytes != size_bytes:
                raise errors.ConflictError(msg='STORAGE_RESERVATION_SIZE_MISMATCH')

            await self._lock_content_hash(db, reservation.owner_hasn_id, sha256)
            existing = (
                await db.execute(
                    text(
                        """
                        SELECT object_id, storage_id, object_key, access, size_bytes
                        FROM hasn_storage_objects
                        WHERE owner_hasn_id = :owner
                          AND sha256 = :sha256
                          AND access = :access
                          AND billable_to_owner
                          AND state = 'active'
                        FOR UPDATE
                        """
                    ),
                    {
                        'owner': reservation.owner_hasn_id,
                        'sha256': sha256,
                        'access': policy.access,
                    },
                )
            ).mappings().one_or_none()
            now = timezone.now()
            if existing is not None:
                result = await self._insert_logical_asset(
                    db,
                    owner_hasn_id=reservation.owner_hasn_id,
                    object_row=existing,
                    filename=filename,
                    mime=mime,
                    category=category,
                    source_app=source_app,
                    idempotency_key=idempotency_key,
                    width=width,
                    height=height,
                    duration_ms=duration_ms,
                    extract_status=extract_status,
                    deduplicated=True,
                    parent_entry_id=parent_entry_id,
                    display_name_override=display_name_override,
                    derived_from_asset_id=derived_from_asset_id,
                )
                await db.execute(
                    text(
                        """
                        UPDATE hasn_storage_objects
                        SET ref_count = ref_count + 1, updated_time = :now
                        WHERE object_id = :object_id
                        """
                    ),
                    {'object_id': str(existing['object_id']), 'now': now},
                )
                await self._finish_reservation_account(
                    db,
                    locked_reservation,
                    used_increment=0,
                    result_asset_id=result.asset_id,
                    now=now,
                )
                await self._insert_orphan_cleanup_job(
                    db,
                    owner_hasn_id=reservation.owner_hasn_id,
                    storage_id=storage_id,
                    object_key=object_key,
                    reservation_id=reservation.reservation_id,
                    reason='dedup_race',
                )
                await self._finish_multipart_job_in_transaction(
                    db,
                    owner_hasn_id=reservation.owner_hasn_id,
                    job_id=multipart_job_id,
                    asset_id=result.asset_id,
                    actual_bytes=size_bytes,
                    part_count=multipart_part_count,
                )
                return result

            object_row = {
                'object_id': reservation.object_id,
                'storage_id': storage_id,
                'object_key': object_key,
                'access': policy.access,
                'size_bytes': size_bytes,
            }
            await db.execute(
                text(
                    """
                    INSERT INTO hasn_storage_objects
                        (object_id, owner_hasn_id, storage_id, object_key, key_layout, access,
                         size_bytes, sha256, billable_to_owner, ref_count, state,
                         created_time, updated_time)
                    VALUES
                        (:object_id, :owner, :storage_id, :object_key, 'owner_scoped', :access,
                         :size_bytes, :sha256, TRUE, 1, 'active', :now, :now)
                    """
                ),
                {
                    **object_row,
                    'owner': reservation.owner_hasn_id,
                    'sha256': sha256,
                    'now': now,
                },
            )
            result = await self._insert_logical_asset(
                db,
                owner_hasn_id=reservation.owner_hasn_id,
                object_row=object_row,
                filename=filename,
                mime=mime,
                category=category,
                source_app=source_app,
                idempotency_key=idempotency_key,
                width=width,
                height=height,
                duration_ms=duration_ms,
                extract_status=extract_status,
                deduplicated=False,
                parent_entry_id=parent_entry_id,
                display_name_override=display_name_override,
                derived_from_asset_id=derived_from_asset_id,
            )
            await self._finish_reservation_account(
                db,
                locked_reservation,
                used_increment=size_bytes,
                result_asset_id=result.asset_id,
                now=now,
            )
            await self._finish_multipart_job_in_transaction(
                db,
                owner_hasn_id=reservation.owner_hasn_id,
                job_id=multipart_job_id,
                asset_id=result.asset_id,
                actual_bytes=size_bytes,
                part_count=multipart_part_count,
            )
            return result

    @staticmethod
    async def _finish_multipart_job_in_transaction(
        db: AsyncSession,
        *,
        owner_hasn_id: str,
        job_id: str | None,
        asset_id: str,
        actual_bytes: int,
        part_count: int | None,
    ) -> None:
        """把 multipart 作业完成状态与资产、配额提交放在同一事务。"""
        if job_id is None:
            return
        if part_count is None:
            raise errors.ServerError(msg='STORAGE_MULTIPART_RESULT_INVALID')
        updated = await db.execute(
            text(
                """
                UPDATE hasn_storage_jobs
                SET status = 'succeeded',
                    total_items = :total_items,
                    processed_items = :total_items,
                    result = jsonb_build_object(
                        'asset_id', CAST(:asset_id AS text),
                        'actual_bytes', CAST(:actual_bytes AS bigint)
                    ),
                    updated_time = :now
                WHERE job_id = :job_id
                  AND owner_hasn_id = :owner
                  AND status = 'running'
                """
            ),
            {
                'total_items': part_count,
                'asset_id': asset_id,
                'actual_bytes': actual_bytes,
                'now': timezone.now(),
                'job_id': job_id,
                'owner': owner_hasn_id,
            },
        )
        if getattr(updated, 'rowcount', 0) != 1:
            raise errors.ConflictError(msg='STORAGE_MULTIPART_NOT_ACTIVE')

    async def _insert_logical_asset(
        self,
        db: AsyncSession,
        *,
        owner_hasn_id: str,
        object_row: Any,
        filename: str,
        mime: str,
        category: str,
        source_app: str,
        idempotency_key: str,
        width: int | None,
        height: int | None,
        duration_ms: int | None,
        extract_status: str | None,
        deduplicated: bool,
        parent_entry_id: str | None = None,
        display_name_override: str | None = None,
        derived_from_asset_id: str | None = None,
    ) -> StoredAsset:
        await self._assert_folder_parent(
            db,
            owner_hasn_id=owner_hasn_id,
            parent_entry_id=parent_entry_id,
        )
        await self._lock_directory_names(db, owner_hasn_id, parent_entry_id)
        display_name, normalized_name = await self._available_entry_name(
            db,
            owner_hasn_id,
            parent_entry_id,
            display_name_override or filename,
        )
        asset_id = f'ast_{uuid4().hex}'
        entry_id = f'ent_{uuid4().hex}'
        kind = _kind_for_mime(mime)
        effective_extract_status = extract_status or ('pending' if category in {'dm_attachment', 'private_doc'} else 'done')
        await db.execute(
            text(
                """
                INSERT INTO hasn_assets
                    (asset_id, owner_hasn_id, access, storage_id, object_key, kind, mime,
                     size_bytes, width, height, duration_ms, extract_status, object_id,
                     category, original_name, source_app, upload_idempotency_key,
                     derived_from_asset_id, lifecycle_status, version, created_time, updated_time)
                VALUES
                    (:asset_id, :owner, :access, :storage_id, :object_key, :kind, :mime,
                     :size_bytes, :width, :height, :duration_ms, :extract_status, :object_id,
                     :category, :original_name, :source_app, :idempotency_key,
                     :derived_from_asset_id, 'active', 1, :now, :now)
                """
            ),
            {
                'asset_id': asset_id,
                'owner': owner_hasn_id,
                'access': str(object_row['access']),
                'storage_id': int(object_row['storage_id']),
                'object_key': str(object_row['object_key']),
                'kind': kind,
                'mime': mime,
                'size_bytes': int(object_row['size_bytes']),
                'width': width,
                'height': height,
                'duration_ms': duration_ms,
                'extract_status': effective_extract_status,
                'object_id': str(object_row['object_id']),
                'category': category,
                'original_name': filename[:512],
                'source_app': source_app[:64],
                'idempotency_key': idempotency_key,
                'derived_from_asset_id': derived_from_asset_id,
                'now': timezone.now(),
            },
        )
        await db.execute(
            text(
                """
                INSERT INTO hasn_storage_entries
                    (entry_id, owner_hasn_id, asset_id, parent_entry_id, entry_type,
                     display_name, normalized_name, system_category, version,
                     created_time, updated_time)
                VALUES
                    (:entry_id, :owner, :asset_id, :parent_entry_id, 'file', :display_name,
                     :normalized_name, :system_category, 1, :now, :now)
                """
            ),
            {
                'entry_id': entry_id,
                'owner': owner_hasn_id,
                'asset_id': asset_id,
                'parent_entry_id': parent_entry_id,
                'display_name': display_name,
                'normalized_name': normalized_name,
                'system_category': _system_category(category),
                'now': timezone.now(),
            },
        )
        return StoredAsset(
            asset_id=asset_id,
            object_id=str(object_row['object_id']),
            entry_id=entry_id,
            kind=kind,
            mime=mime,
            size_bytes=int(object_row['size_bytes']),
            display_name=display_name,
            deduplicated=deduplicated,
        )

    @staticmethod
    async def _finish_reservation_account(
        db: AsyncSession,
        reservation: StorageReservation,
        *,
        used_increment: int,
        result_asset_id: str,
        now: datetime,
    ) -> None:
        account_result = (
            await db.execute(
            text(
                """
                UPDATE hasn_storage_accounts
                SET reserved_bytes = reserved_bytes - :reserved,
                    used_bytes = used_bytes + :used_increment,
                    state = CASE
                        WHEN used_bytes + :used_increment + reserved_bytes - :reserved <= quota_bytes
                        THEN 'active' ELSE 'over_quota'
                    END,
                    updated_time = :now
                WHERE owner_hasn_id = :owner AND reserved_bytes >= :reserved
                RETURNING owner_hasn_id
                """
            ),
            {
                'owner': reservation.owner_hasn_id,
                'reserved': reservation.reserved_bytes,
                'used_increment': used_increment,
                'now': now,
            },
            )
        ).scalar_one_or_none()
        if account_result is None:
            raise errors.ServerError(msg='STORAGE_RESERVATION_COUNTER_INVALID')
        await db.execute(
            text(
                """
                UPDATE hasn_storage_reservations
                SET status = 'committed', result_asset_id = :asset_id, updated_time = :now
                WHERE reservation_id = :reservation_id AND status = 'reserved'
                """
            ),
            {
                'reservation_id': reservation.reservation_id,
                'asset_id': result_asset_id,
                'now': now,
            },
        )

    @staticmethod
    async def _lock_upload_idempotency(db: AsyncSession, owner_hasn_id: str, idempotency_key: str) -> None:
        await db.execute(
            text('SELECT pg_advisory_xact_lock(hashtextextended(:lock_key, 0))'),
            {'lock_key': f'owner-storage-idem:{owner_hasn_id}:{idempotency_key}'},
        )

    @staticmethod
    async def _lock_content_hash(db: AsyncSession, owner_hasn_id: str, sha256: str) -> None:
        await db.execute(
            text('SELECT pg_advisory_xact_lock(hashtextextended(:lock_key, 0))'),
            {'lock_key': f'owner-storage-sha:{owner_hasn_id}:{sha256}'},
        )

    @staticmethod
    async def _available_entry_name(
        db: AsyncSession,
        owner_hasn_id: str,
        parent_entry_id: str | None,
        filename: str,
    ) -> tuple[str, str]:
        base = display_name_for_upload(filename)
        for sequence in range(1, 10_001):
            candidate = base if sequence == 1 else suffixed_name(base, sequence)
            normalized = normalize_storage_name(candidate)
            exists = (
                await db.execute(
                    text(
                        """
                        SELECT 1
                        FROM hasn_storage_entries
                        WHERE owner_hasn_id = :owner
                          AND (
                              (
                                  CAST(:parent AS varchar) IS NULL
                                  AND parent_entry_id IS NULL
                              )
                              OR parent_entry_id = CAST(:parent AS varchar)
                          )
                          AND normalized_name = :normalized
                        LIMIT 1
                        """
                    ),
                    {
                        'owner': owner_hasn_id,
                        'parent': parent_entry_id,
                        'normalized': normalized,
                    },
                )
            ).scalar_one_or_none()
            if exists is None:
                return candidate, normalized
        raise errors.ConflictError(msg='STORAGE_NAME_CONFLICT')

    @staticmethod
    async def _uploaded_asset_by_idempotency_in_transaction(
        db: AsyncSession,
        owner_hasn_id: str,
        idempotency_key: str,
    ) -> StoredAsset | None:
        row = (
            await db.execute(
                text(
                    """
                    SELECT a.asset_id, a.object_id, e.entry_id, a.kind, a.mime,
                           o.size_bytes, e.display_name
                    FROM hasn_assets AS a
                    JOIN hasn_storage_objects AS o ON o.object_id = a.object_id
                    JOIN hasn_storage_entries AS e ON e.asset_id = a.asset_id
                    WHERE a.owner_hasn_id = :owner
                      AND a.upload_idempotency_key = :idempotency_key
                      AND a.lifecycle_status <> 'deleted'
                    """
                ),
                {'owner': owner_hasn_id, 'idempotency_key': idempotency_key},
            )
        ).mappings().one_or_none()
        return _stored_asset_of(row, deduplicated=False) if row is not None else None

    @staticmethod
    async def _asset_by_id_in_transaction(
        db: AsyncSession,
        owner_hasn_id: str,
        asset_id: str,
    ) -> StoredAsset | None:
        row = (
            await db.execute(
                text(
                    """
                    SELECT a.asset_id, a.object_id, e.entry_id, a.kind, a.mime,
                           o.size_bytes, e.display_name
                    FROM hasn_assets AS a
                    JOIN hasn_storage_objects AS o ON o.object_id = a.object_id
                    JOIN hasn_storage_entries AS e ON e.asset_id = a.asset_id
                    WHERE a.owner_hasn_id = :owner AND a.asset_id = :asset_id
                    """
                ),
                {'owner': owner_hasn_id, 'asset_id': asset_id},
            )
        ).mappings().one_or_none()
        return _stored_asset_of(row, deduplicated=False) if row is not None else None

    async def _record_orphan_cleanup(
        self,
        *,
        owner_hasn_id: str,
        storage_id: int,
        object_key: str,
        reservation_id: str,
        reason: str,
    ) -> None:
        try:
            async with self._sessions.begin() as db:
                await self._insert_orphan_cleanup_job(
                    db,
                    owner_hasn_id=owner_hasn_id,
                    storage_id=storage_id,
                    object_key=object_key,
                    reservation_id=reservation_id,
                    reason=reason,
                )
        except Exception as exc:
            log.error(f'孤儿对象清理作业登记失败: {type(exc).__name__}: {exc!r}')
            raise errors.ServerError(msg='STORAGE_ORPHAN_CLEANUP_NOT_RECORDED') from exc

    @staticmethod
    async def _insert_orphan_cleanup_job(
        db: AsyncSession,
        *,
        owner_hasn_id: str,
        storage_id: int,
        object_key: str,
        reservation_id: str,
        reason: str,
    ) -> None:
        await db.execute(
            text(
                """
                INSERT INTO hasn_storage_jobs
                    (job_id, owner_hasn_id, job_type, status, cursor, total_items,
                     processed_items, failed_items, payload, result, attempt_count,
                     next_attempt_time, created_time, updated_time)
                VALUES
                    (:job_id, :owner, 'orphan_cleanup', 'pending', '{}'::jsonb, 1,
                     0, 0,
                     jsonb_build_object(
                         'storage_id', CAST(:storage_id AS bigint),
                         'object_key', CAST(:object_key AS text),
                         'reservation_id', CAST(:reservation_id AS text),
                         'reason', CAST(:reason AS text)
                     ),
                     '{}'::jsonb, 0, :now, :now, :now)
                """
            ),
            {
                'job_id': f'job_{uuid4().hex}',
                'owner': owner_hasn_id,
                'storage_id': storage_id,
                'object_key': object_key,
                'reservation_id': reservation_id,
                'reason': reason[:200],
                'now': timezone.now(),
            },
        )

    async def bind_asset(
        self,
        *,
        owner_hasn_id: str,
        asset_id: str,
        resource_uri: str,
        role: str,
    ) -> dict[str, str]:
        """登记业务资源对资产的权威反向引用。"""
        async with self._sessions.begin() as db:
            return await self.bind_asset_in_transaction(
                db,
                owner_hasn_id=owner_hasn_id,
                asset_id=asset_id,
                resource_uri=resource_uri,
                role=role,
            )

    @staticmethod
    async def bind_asset_in_transaction(
        db: AsyncSession,
        *,
        owner_hasn_id: str,
        asset_id: str,
        resource_uri: str,
        role: str,
    ) -> dict[str, str]:
        """在业务事务中原子登记反向引用。"""
        if not resource_uri.startswith('hasn://') or len(resource_uri) > 1024:
            raise errors.RequestError(
                code=StandardResponseCode.HTTP_422,
                msg='STORAGE_RESOURCE_URI_INVALID',
            )
        if not role or len(role) > 32:
            raise errors.RequestError(
                code=StandardResponseCode.HTTP_422,
                msg='STORAGE_BINDING_ROLE_INVALID',
            )

        visible = (
            await db.execute(
                text(
                    """
                    SELECT 1
                    FROM hasn_assets
                    WHERE owner_hasn_id = :owner
                      AND asset_id = :asset_id
                      AND lifecycle_status = 'active'
                    FOR UPDATE
                    """
                ),
                {'owner': owner_hasn_id, 'asset_id': asset_id},
            )
        ).scalar_one_or_none()
        if visible is None:
            raise errors.NotFoundError(msg='STORAGE_ASSET_NOT_FOUND')

        row = (
            await db.execute(
                text(
                    """
                    INSERT INTO hasn_asset_bindings
                        (binding_id, owner_hasn_id, asset_id, resource_uri, role,
                         status, created_time, updated_time)
                    VALUES
                        (:binding_id, :owner, :asset_id, :resource_uri, :role,
                         'active', :now, :now)
                    ON CONFLICT (asset_id, resource_uri, role)
                    DO UPDATE SET
                        status = 'active',
                        updated_time = EXCLUDED.updated_time
                    RETURNING binding_id, resource_uri, role, status
                    """
                ),
                {
                    'binding_id': f'bnd_{uuid4().hex}',
                    'owner': owner_hasn_id,
                    'asset_id': asset_id,
                    'resource_uri': resource_uri,
                    'role': role,
                    'now': timezone.now(),
                },
            )
        ).mappings().one()
        return {
            'binding_id': str(row['binding_id']),
            'resource_uri': str(row['resource_uri']),
            'role': str(row['role']),
            'status': str(row['status']),
        }

    @staticmethod
    async def bind_private_attachment_in_transaction(
        db: AsyncSession,
        *,
        asset_id: str,
        conversation_id: str,
        message_id: int,
    ) -> None:
        """经窄化数据库接缝原子写入私有附件授权与删除保护。"""
        resource_uri = f'hasn://messages/c/{conversation_id}#{message_id}'
        await db.execute(
            text(
                """
                SELECT public.hasn_bind_private_attachment(
                    CAST(:asset_id AS varchar),
                    CAST(:conversation_id AS uuid),
                    CAST(:resource_uri AS varchar),
                    CAST(:binding_id AS varchar)
                )
                """
            ),
            {
                'asset_id': asset_id,
                'conversation_id': conversation_id,
                'resource_uri': resource_uri,
                'binding_id': f'bnd_{uuid4().hex}',
            },
        )

    async def asset_references(
        self,
        *,
        owner_hasn_id: str,
        asset_id: str,
    ) -> list[dict[str, str]]:
        """返回资产的活动引用；不存在与越权统一返回相同 404。"""
        async with self._sessions() as db:
            visible = (
                await db.execute(
                    text(
                        """
                        SELECT 1
                        FROM hasn_assets
                        WHERE owner_hasn_id = :owner
                          AND asset_id = :asset_id
                          AND lifecycle_status NOT IN ('deleting', 'deleted')
                        """
                    ),
                    {'owner': owner_hasn_id, 'asset_id': asset_id},
                )
            ).scalar_one_or_none()
            if visible is None:
                raise errors.NotFoundError(msg='STORAGE_ASSET_NOT_FOUND')
            rows = (
                await db.execute(
                    text(
                        """
                        SELECT binding_id, resource_uri, role, status
                        FROM hasn_asset_bindings
                        WHERE owner_hasn_id = :owner
                          AND asset_id = :asset_id
                          AND status = 'active'
                        ORDER BY created_time, id
                        """
                    ),
                    {'owner': owner_hasn_id, 'asset_id': asset_id},
                )
            ).mappings().all()
            return [
                {
                    'binding_id': str(row['binding_id']),
                    'resource_uri': str(row['resource_uri']),
                    'role': str(row['role']),
                    'status': str(row['status']),
                }
                for row in rows
            ]

    async def trash_asset(
        self,
        *,
        owner_hasn_id: str,
        asset_id: str,
    ) -> dict[str, str]:
        """把资产移入垃圾箱；对象与用量保持不变。"""
        async with self._sessions.begin() as db:
            row = await self._lock_owned_asset(db, owner_hasn_id=owner_hasn_id, asset_id=asset_id)
            status = str(row['lifecycle_status'])
            if status == 'trashed':
                return {'asset_id': asset_id, 'state': 'trashed'}
            if status != 'active':
                raise errors.NotFoundError(msg='STORAGE_ASSET_NOT_FOUND')
            await db.execute(
                text(
                    """
                    UPDATE hasn_assets
                    SET lifecycle_status = 'trashed',
                        trashed_time = :now,
                        version = version + 1,
                        updated_time = :now
                    WHERE owner_hasn_id = :owner AND asset_id = :asset_id
                    """
                ),
                {'owner': owner_hasn_id, 'asset_id': asset_id, 'now': timezone.now()},
            )
            return {'asset_id': asset_id, 'state': 'trashed'}

    async def restore_asset(
        self,
        *,
        owner_hasn_id: str,
        asset_id: str,
    ) -> dict[str, str]:
        """恢复垃圾箱资产；不复制对象，也不改变用量。"""
        async with self._sessions.begin() as db:
            row = await self._lock_owned_asset(db, owner_hasn_id=owner_hasn_id, asset_id=asset_id)
            status = str(row['lifecycle_status'])
            if status == 'active':
                return {'asset_id': asset_id, 'state': 'active'}
            if status != 'trashed':
                raise errors.NotFoundError(msg='STORAGE_ASSET_NOT_FOUND')
            await db.execute(
                text(
                    """
                    UPDATE hasn_assets
                    SET lifecycle_status = 'active',
                        trashed_time = NULL,
                        version = version + 1,
                        updated_time = :now
                    WHERE owner_hasn_id = :owner AND asset_id = :asset_id
                    """
                ),
                {'owner': owner_hasn_id, 'asset_id': asset_id, 'now': timezone.now()},
            )
            return {'asset_id': asset_id, 'state': 'active'}

    async def delete_asset(
        self,
        *,
        owner_hasn_id: str,
        asset_id: str,
        cascade: bool = False,
    ) -> dict[str, str | None]:
        """彻底删除逻辑资产；物理删除确认后才释放配额。"""
        async with self._sessions.begin() as db:
            asset = await self._lock_owned_asset(db, owner_hasn_id=owner_hasn_id, asset_id=asset_id)
            lifecycle_status = str(asset['lifecycle_status'])
            if lifecycle_status == 'deleted':
                return {'asset_id': asset_id, 'state': 'deleted', 'purge_job_id': None}
            if lifecycle_status == 'deleting':
                job_id = (
                    await db.execute(
                        text(
                            """
                            SELECT job_id
                            FROM hasn_storage_jobs
                            WHERE owner_hasn_id = :owner
                              AND job_type = 'object_purge'
                              AND payload ->> 'object_id' = :object_id
                              AND status IN ('pending', 'running', 'retrying')
                            ORDER BY id DESC
                            LIMIT 1
                            """
                        ),
                        {'owner': owner_hasn_id, 'object_id': str(asset['object_id'])},
                    )
                ).scalar_one_or_none()
                return {
                    'asset_id': asset_id,
                    'state': 'deleting',
                    'purge_job_id': str(job_id) if job_id is not None else None,
                }

            references = await self._active_references_in_transaction(
                db,
                owner_hasn_id=owner_hasn_id,
                asset_id=asset_id,
            )
            if references and not cascade:
                raise errors.ConflictError(
                    msg='STORAGE_ASSET_IN_USE',
                    data={'references': references},
                )

            now = timezone.now()
            if cascade:
                await self._tombstone_business_references(
                    db,
                    owner_hasn_id=owner_hasn_id,
                    asset_id=asset_id,
                    references=references,
                    now=now,
                )
                await db.execute(
                    text(
                        """
                        UPDATE hasn_asset_bindings
                        SET status = 'deleted', updated_time = :now
                        WHERE owner_hasn_id = :owner
                          AND asset_id = :asset_id
                          AND status = 'active'
                        """
                    ),
                    {'owner': owner_hasn_id, 'asset_id': asset_id, 'now': now},
                )

            object_id = asset['object_id']
            if object_id is None:
                raise errors.ServerError(msg='STORAGE_ASSET_OBJECT_NOT_READY')
            obj = (
                await db.execute(
                    text(
                        """
                        SELECT object_id, owner_hasn_id, storage_id, object_key, key_layout,
                               size_bytes, billable_to_owner, ref_count, state
                        FROM hasn_storage_objects
                        WHERE object_id = :object_id AND owner_hasn_id = :owner
                        FOR UPDATE
                        """
                    ),
                    {'object_id': str(object_id), 'owner': owner_hasn_id},
                )
            ).mappings().one_or_none()
            if obj is None:
                raise errors.ServerError(msg='STORAGE_ASSET_OBJECT_NOT_READY')
            ref_count = int(obj['ref_count'])
            if ref_count <= 0:
                raise errors.ServerError(msg='STORAGE_OBJECT_REFERENCE_INVALID')

            remaining_refs = ref_count - 1
            await db.execute(
                text(
                    """
                    DELETE FROM hasn_storage_entries
                    WHERE owner_hasn_id = :owner AND asset_id = :asset_id
                    """
                ),
                {'owner': owner_hasn_id, 'asset_id': asset_id},
            )
            if remaining_refs > 0:
                await db.execute(
                    text(
                        """
                        UPDATE hasn_storage_objects
                        SET ref_count = :ref_count, updated_time = :now
                        WHERE object_id = :object_id
                        """
                    ),
                    {'object_id': str(object_id), 'ref_count': remaining_refs, 'now': now},
                )
                await self._mark_asset_deleted(
                    db,
                    owner_hasn_id=owner_hasn_id,
                    asset_id=asset_id,
                    now=now,
                )
                return {'asset_id': asset_id, 'state': 'deleted', 'purge_job_id': None}

            if str(obj['key_layout']) == 'legacy':
                location_refs = (
                    await db.execute(
                        text(
                            """
                            SELECT COUNT(*)
                            FROM hasn_storage_objects
                            WHERE storage_id = :storage_id
                              AND object_key = :object_key
                              AND state IN ('pending', 'active', 'deleting')
                            """
                        ),
                        {
                            'storage_id': int(obj['storage_id']),
                            'object_key': str(obj['object_key']),
                        },
                    )
                ).scalar_one()
                if int(location_refs) > 1:
                    await db.execute(
                        text(
                            """
                            UPDATE hasn_storage_objects
                            SET ref_count = 0, state = 'deleted', updated_time = :now
                            WHERE object_id = :object_id
                            """
                        ),
                        {'object_id': str(object_id), 'now': now},
                    )
                    await self._mark_asset_deleted(
                        db,
                        owner_hasn_id=owner_hasn_id,
                        asset_id=asset_id,
                        now=now,
                    )
                    if bool(obj['billable_to_owner']):
                        await self._decrement_account_usage_or_skip_orphan_identity(
                            db,
                            owner_hasn_id=owner_hasn_id,
                            size_bytes=int(obj['size_bytes']),
                            now=now,
                        )
                    return {'asset_id': asset_id, 'state': 'deleted', 'purge_job_id': None}

            job_id = f'job_{uuid4().hex}'
            await db.execute(
                text(
                    """
                    UPDATE hasn_storage_objects
                    SET ref_count = 0, state = 'deleting', updated_time = :now
                    WHERE object_id = :object_id
                    """
                ),
                {'object_id': str(object_id), 'now': now},
            )
            await db.execute(
                text(
                    """
                    UPDATE hasn_assets
                    SET lifecycle_status = 'deleting',
                        version = version + 1,
                        updated_time = :now
                    WHERE owner_hasn_id = :owner AND asset_id = :asset_id
                    """
                ),
                {'owner': owner_hasn_id, 'asset_id': asset_id, 'now': now},
            )
            await db.execute(
                text(
                    """
                    INSERT INTO hasn_storage_jobs
                        (job_id, owner_hasn_id, job_type, status, cursor, total_items,
                         processed_items, failed_items, payload, result, attempt_count,
                         next_attempt_time, created_time, updated_time)
                    VALUES
                        (:job_id, :owner, 'object_purge', 'pending', '{}'::jsonb, 1,
                         0, 0,
                         jsonb_build_object(
                             'object_id', CAST(:object_id AS text),
                             'storage_id', CAST(:storage_id AS bigint),
                             'object_key', CAST(:object_key AS text),
                             'size_bytes', CAST(:size_bytes AS bigint)
                         ),
                         '{}'::jsonb, 0, :now, :now, :now)
                    """
                ),
                {
                    'job_id': job_id,
                    'owner': owner_hasn_id,
                    'object_id': str(object_id),
                    'storage_id': int(obj['storage_id']),
                    'object_key': str(obj['object_key']),
                    'size_bytes': int(obj['size_bytes']),
                    'now': now,
                },
            )
            return {'asset_id': asset_id, 'state': 'deleting', 'purge_job_id': job_id}

    async def create_export(
        self,
        *,
        owner_hasn_id: str,
        mode: str,
        include_trashed: bool,
    ) -> dict[str, Any]:
        """创建带并发、冷却与每日次数护栏的异步导出快照。"""
        if mode not in {'manifest', 'archive'}:
            raise errors.RequestError(
                code=StandardResponseCode.HTTP_422,
                msg='STORAGE_EXPORT_MODE_INVALID',
            )
        usage = await self.usage(owner_hasn_id=owner_hasn_id)
        now = timezone.now()
        cooldown = _positive_env_int('STORAGE_EXPORT_COOLDOWN_SECONDS', _EXPORT_COOLDOWN_DEFAULT)
        daily_limit = _positive_env_int('STORAGE_EXPORT_DAILY_LIMIT', _EXPORT_DAILY_LIMIT_DEFAULT)
        archive_max = _positive_env_int(
            'STORAGE_EXPORT_ARCHIVE_MAX_BYTES',
            _EXPORT_ARCHIVE_MAX_BYTES_DEFAULT,
        )
        ttl_seconds = _positive_env_int('STORAGE_EXPORT_TTL_SECONDS', _EXPORT_TTL_DEFAULT)
        async with self._sessions.begin() as db:
            await db.execute(
                text('SELECT pg_advisory_xact_lock(hashtextextended(:lock_key, 0))'),
                {'lock_key': f'owner-storage-export:{owner_hasn_id}'},
            )
            active = (
                await db.execute(
                    text(
                        """
                        SELECT job_id
                        FROM hasn_storage_jobs
                        WHERE owner_hasn_id = :owner
                          AND job_type = 'storage_export'
                          AND status IN ('pending', 'running', 'retrying')
                        LIMIT 1
                        """
                    ),
                    {'owner': owner_hasn_id},
                )
            ).scalar_one_or_none()
            if active is not None:
                raise errors.RequestError(
                    code=StandardResponseCode.HTTP_429,
                    msg='STORAGE_EXPORT_THROTTLED',
                    data={'reason': 'concurrent_export', 'active_job_id': str(active)},
                )
            latest = (
                await db.execute(
                    text(
                        """
                        SELECT created_time
                        FROM hasn_storage_jobs
                        WHERE owner_hasn_id = :owner AND job_type = 'storage_export'
                        ORDER BY created_time DESC
                        LIMIT 1
                        """
                    ),
                    {'owner': owner_hasn_id},
                )
            ).scalar_one_or_none()
            if latest is not None and latest + timedelta(seconds=cooldown) > now:
                retry_after = int((latest + timedelta(seconds=cooldown) - now).total_seconds())
                raise errors.RequestError(
                    code=StandardResponseCode.HTTP_429,
                    msg='STORAGE_EXPORT_THROTTLED',
                    data={'reason': 'cooldown', 'retry_after_seconds': max(1, retry_after)},
                )
            today_count = (
                await db.execute(
                    text(
                        """
                        SELECT COUNT(*)
                        FROM hasn_storage_jobs
                        WHERE owner_hasn_id = :owner
                          AND job_type = 'storage_export'
                          AND created_time >= date_trunc(
                              'day',
                              CAST(:now AS timestamptz)
                          )
                        """
                    ),
                    {'owner': owner_hasn_id, 'now': now},
                )
            ).scalar_one()
            if int(today_count) >= daily_limit:
                raise errors.RequestError(
                    code=StandardResponseCode.HTTP_429,
                    msg='STORAGE_EXPORT_THROTTLED',
                    data={'reason': 'daily_limit', 'daily_limit': daily_limit},
                )

            job_id = f'job_{uuid4().hex}'
            await db.execute(
                text(
                    """
                    INSERT INTO hasn_storage_jobs
                        (job_id, owner_hasn_id, job_type, status, cursor, total_items,
                         processed_items, failed_items, payload, result, attempt_count,
                         next_attempt_time, expires_time, created_time, updated_time)
                    VALUES
                        (:job_id, :owner, 'storage_export', 'pending', '{}'::jsonb,
                         0, 0, 0,
                         jsonb_build_object(
                             'mode', CAST(:mode AS text),
                             'include_trashed', CAST(:include_trashed AS boolean),
                             'snapshot_time', CAST(:snapshot_time AS timestamptz),
                             'total_bytes', CAST(0 AS bigint)
                         ),
                         '{}'::jsonb, 0, :now, :expires, :now, :now)
                    """
                ),
                {
                    'job_id': job_id,
                    'owner': owner_hasn_id,
                    'mode': mode,
                    'include_trashed': include_trashed,
                    'snapshot_time': now,
                    'now': now,
                    'expires': now + timedelta(seconds=ttl_seconds),
                },
            )
            await db.execute(
                text(
                    """
                    WITH RECURSIVE paths AS (
                        SELECT entry_id, parent_entry_id,
                               '/' || display_name AS logical_path
                        FROM hasn_storage_entries
                        WHERE owner_hasn_id = :owner AND parent_entry_id IS NULL
                        UNION ALL
                        SELECT child.entry_id, child.parent_entry_id,
                               parent.logical_path || '/' || child.display_name
                        FROM hasn_storage_entries AS child
                        JOIN paths AS parent
                          ON child.parent_entry_id = parent.entry_id
                        WHERE child.owner_hasn_id = :owner
                    ),
                    frozen_assets AS (
                        SELECT a.asset_id, a.original_name, a.mime, a.source_app,
                               a.access, a.created_time AS asset_created_time,
                               a.lifecycle_status, paths.logical_path,
                               o.object_id, o.storage_id, o.object_key,
                               o.size_bytes, o.sha256,
                               COALESCE(
                                   jsonb_agg(
                                       jsonb_build_object(
                                           'resource_uri', b.resource_uri,
                                           'role', b.role
                                       )
                                       ORDER BY b.created_time, b.id
                                   ) FILTER (
                                       WHERE b.id IS NOT NULL
                                         AND b.status = 'active'
                                   ),
                                   '[]'::jsonb
                               ) AS bindings
                        FROM hasn_assets AS a
                        JOIN hasn_storage_objects AS o ON o.object_id = a.object_id
                        JOIN hasn_storage_entries AS e ON e.asset_id = a.asset_id
                        JOIN paths ON paths.entry_id = e.entry_id
                        LEFT JOIN hasn_asset_bindings AS b
                          ON b.asset_id = a.asset_id
                         AND b.owner_hasn_id = a.owner_hasn_id
                        WHERE a.owner_hasn_id = :owner
                          AND o.state IN ('active', 'deleting')
                          AND (
                              a.lifecycle_status = 'active'
                              OR (
                                  CAST(:include_trashed AS boolean)
                                  AND a.lifecycle_status = 'trashed'
                              )
                          )
                        GROUP BY a.asset_id, a.original_name, a.mime, a.source_app,
                                 a.access, a.created_time, a.lifecycle_status,
                                 paths.logical_path, o.object_id, o.storage_id,
                                 o.object_key, o.size_bytes, o.sha256
                    )
                    INSERT INTO hasn_storage_export_items
                        (item_id, job_id, owner_hasn_id, asset_id, logical_path,
                         original_name, mime, source_app, access, asset_created_time,
                         lifecycle_status, bindings, object_id, storage_id, object_key,
                         size_bytes, sha256, verify_status, created_time, updated_time)
                    SELECT
                        'exi_' || substr(md5(:job_id || ':' || asset_id), 1, 32),
                        :job_id, :owner, asset_id, logical_path,
                        COALESCE(original_name, ''), mime, source_app, access,
                        asset_created_time, lifecycle_status, bindings,
                        object_id, storage_id, object_key, size_bytes, sha256,
                        'pending', :now, :now
                    FROM frozen_assets
                    ORDER BY logical_path, asset_id
                    """
                ),
                {
                    'job_id': job_id,
                    'owner': owner_hasn_id,
                    'include_trashed': include_trashed,
                    'now': now,
                },
            )
            snapshot = (
                await db.execute(
                    text(
                        """
                        WITH unique_objects AS (
                            SELECT DISTINCT object_id, size_bytes
                            FROM hasn_storage_export_items
                            WHERE job_id = :job_id AND owner_hasn_id = :owner
                        )
                        SELECT
                            (
                                SELECT COUNT(*)
                                FROM hasn_storage_export_items
                                WHERE job_id = :job_id AND owner_hasn_id = :owner
                            )::bigint AS asset_count,
                            COALESCE(SUM(size_bytes), 0)::bigint AS total_bytes
                        FROM unique_objects
                        """
                    ),
                    {'job_id': job_id, 'owner': owner_hasn_id},
                )
            ).mappings().one()
            total_items = int(snapshot['asset_count'])
            total_bytes = int(snapshot['total_bytes'])
            if total_bytes > max(usage.quota_bytes, usage.used_bytes):
                raise errors.ServerError(msg='STORAGE_EXPORT_USAGE_INVARIANT_BROKEN')
            if mode == 'archive' and total_bytes > archive_max:
                raise errors.RequestError(
                    code=StandardResponseCode.HTTP_429,
                    msg='STORAGE_EXPORT_THROTTLED',
                    data={
                        'reason': 'archive_too_large',
                        'max_bytes': archive_max,
                        'requested_bytes': total_bytes,
                    },
                )
            await db.execute(
                text(
                    """
                    UPDATE hasn_storage_jobs
                    SET total_items = :total_items,
                        payload = jsonb_set(
                            payload,
                            '{total_bytes}',
                            to_jsonb(CAST(:total_bytes AS bigint))
                        ),
                        updated_time = :now
                    WHERE job_id = :job_id AND owner_hasn_id = :owner
                    """
                ),
                {
                    'job_id': job_id,
                    'owner': owner_hasn_id,
                    'total_items': total_items,
                    'total_bytes': total_bytes,
                    'now': now,
                },
            )
            return {
                'job_id': job_id,
                'status': 'pending',
                'mode': mode,
                'total_items': total_items,
                'total_bytes': total_bytes,
                'expires_time': (now + timedelta(seconds=ttl_seconds)).isoformat(),
            }

    async def export_status(
        self,
        *,
        owner_hasn_id: str,
        job_id: str,
    ) -> dict[str, Any]:
        """读取本人导出作业，跨 Owner 与不存在统一返回 404。"""
        async with self._sessions() as db:
            row = (
                await db.execute(
                    text(
                        """
                        SELECT job_id, status, total_items, processed_items, failed_items,
                               error_code, payload, result, attempt_count, expires_time,
                               created_time, updated_time
                        FROM hasn_storage_jobs
                        WHERE owner_hasn_id = :owner
                          AND job_id = :job_id
                          AND job_type = 'storage_export'
                        """
                    ),
                    {'owner': owner_hasn_id, 'job_id': job_id},
                )
            ).mappings().one_or_none()
        if row is None:
            raise errors.NotFoundError(msg='STORAGE_EXPORT_NOT_FOUND')
        return _export_job_view(row)

    async def list_exports(
        self,
        *,
        owner_hasn_id: str,
        limit: int = 5,
    ) -> dict[str, Any]:
        """按创建时间倒序列出本人的导出作业。

        没有这个接口，客户端只能把 `job_id` 记在页面内存里——换页、重开窗口或换设备后
        作业就再也找不回来（作业还在云端跑完，产物躺到过期没人取）。列表让客户端每次进页面
        都能凭权威数据恢复「进行中 / 可下载」的状态卡。
        """
        if limit <= 0 or limit > 50:
            raise errors.RequestError(msg='STORAGE_EXPORT_LIST_LIMIT_INVALID')
        async with self._sessions() as db:
            rows = (
                await db.execute(
                    text(
                        """
                        SELECT job_id, status, total_items, processed_items, failed_items,
                               error_code, payload, result, attempt_count, expires_time,
                               created_time, updated_time
                        FROM hasn_storage_jobs
                        WHERE owner_hasn_id = :owner
                          AND job_type = 'storage_export'
                        ORDER BY created_time DESC
                        LIMIT :limit
                        """
                    ),
                    {'owner': owner_hasn_id, 'limit': limit},
                )
            ).mappings().all()
        return {'items': [_export_job_view(row) for row in rows]}

    async def export_download(
        self,
        *,
        owner_hasn_id: str,
        job_id: str,
    ) -> dict[str, Any]:
        """为成功且未过期的导出产物签发短期下载 URL。"""
        async with self._sessions() as db:
            row = (
                await db.execute(
                    text(
                        """
                        SELECT status, result, expires_time
                        FROM hasn_storage_jobs
                        WHERE owner_hasn_id = :owner
                          AND job_id = :job_id
                          AND job_type = 'storage_export'
                        """
                    ),
                    {'owner': owner_hasn_id, 'job_id': job_id},
                )
            ).mappings().one_or_none()
            if row is None:
                raise errors.NotFoundError(msg='STORAGE_EXPORT_NOT_FOUND')
            if str(row['status']) != 'succeeded':
                raise errors.ConflictError(
                    msg='STORAGE_EXPORT_NOT_READY',
                    data={'status': str(row['status'])},
                )
            if row['expires_time'] is None or row['expires_time'] <= timezone.now():
                raise errors.ConflictError(msg='STORAGE_EXPORT_EXPIRED')
            result = dict(row['result'])
            storage_id = result.get('storage_id')
            object_key = result.get('object_key')
            if storage_id is None or not object_key:
                raise errors.ServerError(msg='STORAGE_EXPORT_RESULT_INVALID')
            expires_in = min(
                3600,
                max(1, int((row['expires_time'] - timezone.now()).total_seconds())),
            )
            url = await StorageService.signed_url(
                db,
                storage_id=int(storage_id),
                object_key=str(object_key),
                expires_in=expires_in,
            )
        return {
            'job_id': job_id,
            'url': url,
            'expires_at': (timezone.now() + timedelta(seconds=expires_in)).isoformat(),
            'filename': result.get('filename'),
            'size_bytes': result.get('size_bytes'),
            'sha256': result.get('sha256'),
        }

    async def create_migration(
        self,
        *,
        owner_hasn_id: str,
        target_storage_by_access: dict[str, int],
        observation_seconds: int = 7 * 24 * 3600,
        audit_actor_id: str | None = None,
    ) -> dict[str, Any]:
        """固定 Owner 物理对象清单并创建可恢复迁移作业。"""
        if not target_storage_by_access or set(target_storage_by_access) - {'private', 'public'}:
            raise errors.RequestError(
                code=StandardResponseCode.HTTP_422,
                msg='STORAGE_MIGRATION_TARGET_INVALID',
            )
        if observation_seconds < 60 or observation_seconds > 30 * 24 * 3600:
            raise errors.RequestError(
                code=StandardResponseCode.HTTP_422,
                msg='STORAGE_MIGRATION_OBSERVATION_INVALID',
            )
        await self.usage(owner_hasn_id=owner_hasn_id)
        now = timezone.now()
        job_id = f'job_{uuid4().hex}'
        async with self._sessions.begin() as db:
            existing = (
                await db.execute(
                    text(
                        """
                        SELECT job_id
                        FROM hasn_storage_jobs
                        WHERE owner_hasn_id = :owner
                          AND job_type = 'storage_migration'
                          AND status IN ('pending', 'running', 'retrying', 'paused')
                        LIMIT 1
                        FOR UPDATE
                        """
                    ),
                    {'owner': owner_hasn_id},
                )
            ).scalar_one_or_none()
            if existing is not None:
                raise errors.ConflictError(
                    msg='STORAGE_MIGRATION_ALREADY_RUNNING',
                    data={'job_id': str(existing)},
                )
            reserved_bytes = (
                await db.execute(
                    text(
                        """
                        SELECT reserved_bytes
                        FROM hasn_storage_accounts
                        WHERE owner_hasn_id = :owner
                        FOR UPDATE
                        """
                    ),
                    {'owner': owner_hasn_id},
                )
            ).scalar_one()
            if int(reserved_bytes) != 0:
                raise errors.ConflictError(
                    msg='STORAGE_MIGRATION_UPLOADS_IN_PROGRESS',
                    data={'reserved_bytes': int(reserved_bytes)},
                )

            for access, storage_id in target_storage_by_access.items():
                storage = await StorageService.get_storage(db, int(storage_id))
                if str(storage.access) != access:
                    raise errors.RequestError(
                        code=StandardResponseCode.HTTP_422,
                        msg='STORAGE_MIGRATION_TARGET_ACCESS_MISMATCH',
                        data={
                            'access': access,
                            'storage_id': int(storage_id),
                            'storage_access': str(storage.access),
                        },
                    )
            objects = (
                await db.execute(
                    text(
                        """
                        SELECT object_id, storage_id, object_key, key_layout, access,
                               size_bytes, sha256
                        FROM hasn_storage_objects
                        WHERE owner_hasn_id = :owner
                          AND state = 'active'
                        ORDER BY id
                        FOR UPDATE
                        """
                    ),
                    {'owner': owner_hasn_id},
                )
            ).mappings().all()
            missing_access = sorted(
                {str(row['access']) for row in objects} - set(target_storage_by_access)
            )
            if missing_access:
                raise errors.RequestError(
                    code=StandardResponseCode.HTTP_422,
                    msg='STORAGE_MIGRATION_TARGET_INCOMPLETE',
                    data={'missing_access': missing_access},
                )
            planned_objects: list[tuple[Any, int, str]] = []
            for row in objects:
                access = str(row['access'])
                target_storage_id = int(target_storage_by_access[access])
                target_key = build_owner_object_key(
                    owner_hasn_id=owner_hasn_id,
                    access=access,
                    object_id=str(row['object_id']),
                )
                if (
                    int(row['storage_id']) == target_storage_id
                    and str(row['object_key']) == target_key
                ):
                    continue
                planned_objects.append((row, target_storage_id, target_key))

            payload = {
                'target_storage_by_access': {
                    access: int(storage_id)
                    for access, storage_id in sorted(target_storage_by_access.items())
                },
                'snapshot_time': now.isoformat(),
                'observation_seconds': observation_seconds,
            }
            status = 'pending' if planned_objects else 'succeeded'
            await db.execute(
                text(
                    """
                    INSERT INTO hasn_storage_jobs
                        (job_id, owner_hasn_id, job_type, status, cursor, total_items,
                         processed_items, failed_items, payload, result, attempt_count,
                         next_attempt_time, created_time, updated_time)
                    VALUES
                        (:job_id, :owner, 'storage_migration', :status, '{}'::jsonb,
                         :total_items, 0, 0, CAST(:payload AS jsonb), '{}'::jsonb,
                         0, :next_attempt, :now, :now)
                    """
                ),
                {
                    'job_id': job_id,
                    'owner': owner_hasn_id,
                    'status': status,
                    'total_items': len(planned_objects),
                    'payload': json.dumps(payload, ensure_ascii=False),
                    'next_attempt': now if planned_objects else None,
                    'now': now,
                },
            )
            for row, target_storage_id, target_key in planned_objects:
                await db.execute(
                    text(
                        """
                        INSERT INTO hasn_storage_migration_items
                            (item_id, job_id, object_id, source_storage_id,
                             source_object_key, source_key_layout,
                             target_storage_id, target_object_key,
                             source_size_bytes, source_sha256, verify_status,
                             created_time, updated_time)
                        VALUES
                            (:item_id, :job_id, :object_id, :source_storage_id,
                             :source_object_key, :source_key_layout,
                             :target_storage_id, :target_object_key,
                             :source_size_bytes, :source_sha256, 'pending', :now, :now)
                        """
                    ),
                    {
                        'item_id': f'mig_{uuid4().hex}',
                        'job_id': job_id,
                        'object_id': str(row['object_id']),
                        'source_storage_id': int(row['storage_id']),
                        'source_object_key': str(row['object_key']),
                        'source_key_layout': str(row['key_layout']),
                        'target_storage_id': target_storage_id,
                        'target_object_key': target_key,
                        'source_size_bytes': int(row['size_bytes']),
                        'source_sha256': (
                            str(row['sha256']).strip() if row['sha256'] else None
                        ),
                        'now': now,
                    },
                )
            if audit_actor_id is not None:
                from backend.app.hasn.service.hasn_audit_log_service import (
                    hasn_audit_log_service,
                )

                await hasn_audit_log_service.append(
                    db=db,
                    actor_id=audit_actor_id,
                    actor_type='human',
                    action='storage_migration_create',
                    target_type='storage_job',
                    target_id=job_id,
                    details={
                        'job_id': job_id,
                        'owner_hasn_id': owner_hasn_id,
                        'target_storage_by_access': payload['target_storage_by_access'],
                        'observation_seconds': observation_seconds,
                        'total_items': len(planned_objects),
                    },
                    severity='info',
                )
        return {
            'job_id': job_id,
            'status': status,
            'total_items': len(planned_objects),
            'snapshot_time': payload['snapshot_time'],
        }

    async def migration_status(
        self,
        *,
        owner_hasn_id: str,
        job_id: str,
    ) -> dict[str, Any]:
        """读取逐用户迁移状态和明细汇总。"""
        async with self._sessions() as db:
            row = (
                await db.execute(
                    text(
                        """
                        SELECT job_id, status, total_items, processed_items, failed_items,
                               error_code, cursor, payload, result, attempt_count,
                               next_attempt_time, created_time, updated_time
                        FROM hasn_storage_jobs
                        WHERE job_id = :job_id
                          AND owner_hasn_id = :owner
                          AND job_type = 'storage_migration'
                        """
                    ),
                    {'job_id': job_id, 'owner': owner_hasn_id},
                )
            ).mappings().one_or_none()
            if row is None:
                raise errors.NotFoundError(msg='STORAGE_MIGRATION_NOT_FOUND')
            item_states = (
                await db.execute(
                    text(
                        """
                        SELECT verify_status, source_cleanup_status, COUNT(*) AS count
                        FROM hasn_storage_migration_items
                        WHERE job_id = :job_id
                        GROUP BY verify_status, source_cleanup_status
                        ORDER BY verify_status, source_cleanup_status
                        """
                    ),
                    {'job_id': job_id},
                )
            ).mappings().all()
        return {
            'job_id': str(row['job_id']),
            'owner_hasn_id': owner_hasn_id,
            'status': str(row['status']),
            'total_items': int(row['total_items']),
            'processed_items': int(row['processed_items']),
            'failed_items': int(row['failed_items']),
            'error_code': row['error_code'],
            'attempt_count': int(row['attempt_count']),
            'next_attempt_time': (
                row['next_attempt_time'].isoformat() if row['next_attempt_time'] else None
            ),
            'cursor': dict(row['cursor']),
            'payload': dict(row['payload']),
            'result': dict(row['result']),
            'item_states': [
                {
                    'verify_status': str(item['verify_status']),
                    'source_cleanup_status': str(item['source_cleanup_status']),
                    'count': int(item['count']),
                }
                for item in item_states
            ],
            'created_time': row['created_time'].isoformat(),
            'updated_time': row['updated_time'].isoformat() if row['updated_time'] else None,
        }

    async def pause_migration(
        self,
        *,
        owner_hasn_id: str,
        job_id: str,
        audit_actor_id: str,
    ) -> dict[str, str]:
        """在条目边界暂停迁移；运行中的单条复制需完成后再重试暂停。"""
        async with self._sessions.begin() as db:
            status = (
                await db.execute(
                    text(
                        """
                        SELECT status
                        FROM hasn_storage_jobs
                        WHERE job_id = :job_id
                          AND owner_hasn_id = :owner
                          AND job_type = 'storage_migration'
                        FOR UPDATE
                        """
                    ),
                    {'job_id': job_id, 'owner': owner_hasn_id},
                )
            ).scalar_one_or_none()
            if status is None:
                raise errors.NotFoundError(msg='STORAGE_MIGRATION_NOT_FOUND')
            if str(status) == 'paused':
                return {'job_id': job_id, 'status': 'paused'}
            if str(status) == 'running':
                raise errors.ConflictError(msg='STORAGE_MIGRATION_BUSY')
            if str(status) not in {'pending', 'retrying'}:
                raise errors.ConflictError(
                    msg='STORAGE_MIGRATION_NOT_PAUSABLE',
                    data={'status': str(status)},
                )
            await db.execute(
                text(
                    """
                    UPDATE hasn_storage_jobs
                    SET status = 'paused',
                        next_attempt_time = NULL,
                        updated_time = :now
                    WHERE job_id = :job_id
                    """
                ),
                {'job_id': job_id, 'now': timezone.now()},
            )
            from backend.app.hasn.service.hasn_audit_log_service import (
                hasn_audit_log_service,
            )

            await hasn_audit_log_service.append(
                db=db,
                actor_id=audit_actor_id,
                actor_type='human',
                action='storage_migration_pause',
                target_type='storage_job',
                target_id=job_id,
                details={'job_id': job_id, 'owner_hasn_id': owner_hasn_id},
                severity='info',
            )
        return {'job_id': job_id, 'status': 'paused'}

    async def resume_migration(
        self,
        *,
        owner_hasn_id: str,
        job_id: str,
        audit_actor_id: str,
    ) -> dict[str, str]:
        """恢复已暂停迁移，后续由持久作业 worker 从明细断点续跑。"""
        async with self._sessions.begin() as db:
            status = (
                await db.execute(
                    text(
                        """
                        SELECT status
                        FROM hasn_storage_jobs
                        WHERE job_id = :job_id
                          AND owner_hasn_id = :owner
                          AND job_type = 'storage_migration'
                        FOR UPDATE
                        """
                    ),
                    {'job_id': job_id, 'owner': owner_hasn_id},
                )
            ).scalar_one_or_none()
            if status is None:
                raise errors.NotFoundError(msg='STORAGE_MIGRATION_NOT_FOUND')
            if str(status) != 'paused':
                raise errors.ConflictError(
                    msg='STORAGE_MIGRATION_NOT_RESUMABLE',
                    data={'status': str(status)},
                )
            remaining = int(
                (
                    await db.execute(
                        text(
                            """
                            SELECT COUNT(*)
                            FROM hasn_storage_migration_items
                            WHERE job_id = :job_id
                              AND verify_status <> 'switched'
                            """
                        ),
                        {'job_id': job_id},
                    )
                ).scalar_one()
            )
            next_status = 'pending' if remaining else 'succeeded'
            now = timezone.now()
            await db.execute(
                text(
                    """
                    UPDATE hasn_storage_jobs
                    SET status = :status,
                        next_attempt_time = :next_attempt_time,
                        updated_time = :now
                    WHERE job_id = :job_id
                    """
                ),
                {
                    'job_id': job_id,
                    'status': next_status,
                    'next_attempt_time': now if remaining else None,
                    'now': now,
                },
            )
            from backend.app.hasn.service.hasn_audit_log_service import (
                hasn_audit_log_service,
            )

            await hasn_audit_log_service.append(
                db=db,
                actor_id=audit_actor_id,
                actor_type='human',
                action='storage_migration_resume',
                target_type='storage_job',
                target_id=job_id,
                details={
                    'job_id': job_id,
                    'owner_hasn_id': owner_hasn_id,
                    'remaining_items': remaining,
                },
                severity='info',
            )
        return {'job_id': job_id, 'status': next_status}

    async def rollback_migration(
        self,
        *,
        owner_hasn_id: str,
        job_id: str,
        limit: int = 100,
    ) -> dict[str, int]:
        """在源对象观察期内按明细把对象位置切回并清理目标副本。"""
        if limit <= 0 or limit > 1000:
            raise errors.RequestError(
                code=StandardResponseCode.HTTP_422,
                msg='STORAGE_MIGRATION_ROLLBACK_LIMIT_INVALID',
            )
        async with self._sessions() as db:
            job = (
                await db.execute(
                    text(
                        """
                        SELECT status
                        FROM hasn_storage_jobs
                        WHERE job_id = :job_id
                          AND owner_hasn_id = :owner
                          AND job_type = 'storage_migration'
                        """
                    ),
                    {'job_id': job_id, 'owner': owner_hasn_id},
                )
            ).scalar_one_or_none()
            if job is None:
                raise errors.NotFoundError(msg='STORAGE_MIGRATION_NOT_FOUND')
            if str(job) not in {'succeeded', 'cancelled'}:
                raise errors.ConflictError(
                    msg='STORAGE_MIGRATION_NOT_ROLLBACKABLE',
                    data={'status': str(job)},
                )
            purged_sources = int(
                (
                    await db.execute(
                        text(
                            """
                            SELECT COUNT(*)
                            FROM hasn_storage_migration_items
                            WHERE job_id = :job_id
                              AND verify_status = 'switched'
                              AND source_cleanup_status = 'deleted'
                            """
                        ),
                        {'job_id': job_id},
                    )
                ).scalar_one()
            )
            if purged_sources:
                raise errors.ConflictError(
                    msg='STORAGE_MIGRATION_SOURCE_PURGED',
                    data={'purged_items': purged_sources},
                )
            items = (
                await db.execute(
                    text(
                        """
                        SELECT item_id, object_id, source_storage_id, source_object_key,
                               source_key_layout,
                               target_storage_id, target_object_key, source_size_bytes,
                               source_sha256
                        FROM hasn_storage_migration_items
                        WHERE job_id = :job_id
                          AND verify_status = 'switched'
                          AND source_cleanup_status IN ('retained', 'shared')
                        ORDER BY id
                        LIMIT :limit
                        """
                    ),
                    {'job_id': job_id, 'limit': limit},
                )
            ).mappings().all()
            source_storages = {
                int(row['source_storage_id']): copy.copy(
                    await StorageService.get_storage(db, int(row['source_storage_id']))
                )
                for row in items
            }

        rolled_back = 0
        for item in items:
            source_storage = source_storages[int(item['source_storage_id'])]
            source_stat = await StorageService.stat_on_storage(
                source_storage,
                object_key=str(item['source_object_key']),
            )
            source_sha, source_size = await StorageService.sha256_on_storage(
                source_storage,
                object_key=str(item['source_object_key']),
            )
            expected_sha = (
                str(item['source_sha256']).strip() if item['source_sha256'] else None
            )
            if (
                source_stat.size != int(item['source_size_bytes'])
                or source_size != int(item['source_size_bytes'])
                or (expected_sha is not None and source_sha != expected_sha)
            ):
                raise errors.ServerError(msg='STORAGE_MIGRATION_VERIFY_FAILED')
            if await self._rollback_migration_item(
                owner_hasn_id=owner_hasn_id,
                job_id=job_id,
                item=dict(item),
            ):
                rolled_back += 1

        async with self._sessions.begin() as db:
            remaining = int(
                (
                    await db.execute(
                        text(
                            """
                            SELECT COUNT(*)
                            FROM hasn_storage_migration_items
                            WHERE job_id = :job_id
                              AND verify_status = 'switched'
                            """
                        ),
                        {'job_id': job_id},
                    )
                ).scalar_one()
            )
            if remaining == 0:
                await db.execute(
                    text(
                        """
                        UPDATE hasn_storage_jobs
                        SET status = 'cancelled',
                            result = result || jsonb_build_object(
                                'rolled_back', TRUE,
                                'rollback_time', CAST(:now AS timestamptz)
                            ),
                            updated_time = :now
                        WHERE job_id = :job_id
                          AND owner_hasn_id = :owner
                        """
                    ),
                    {
                        'job_id': job_id,
                        'owner': owner_hasn_id,
                        'now': timezone.now(),
                    },
                )
        return {'rolled_back': rolled_back, 'remaining': remaining}

    async def _rollback_migration_item(
        self,
        *,
        owner_hasn_id: str,
        job_id: str,
        item: dict[str, Any],
    ) -> bool:
        now = timezone.now()
        async with self._sessions.begin() as db:
            locked = (
                await db.execute(
                    text(
                        """
                        SELECT verify_status
                        FROM hasn_storage_migration_items
                        WHERE item_id = :item_id
                          AND source_cleanup_status IN ('retained', 'shared')
                        FOR UPDATE
                        """
                    ),
                    {'item_id': str(item['item_id'])},
                )
            ).scalar_one_or_none()
            if locked != 'switched':
                return False
            switched = await db.execute(
                text(
                    """
                    UPDATE hasn_storage_objects
                    SET storage_id = :source_storage_id,
                        object_key = :source_object_key,
                        key_layout = :source_key_layout,
                        updated_time = :now
                    WHERE object_id = :object_id
                      AND owner_hasn_id = :owner
                      AND storage_id = :target_storage_id
                      AND object_key = :target_object_key
                      AND state = 'active'
                    """
                ),
                {
                    **item,
                    'owner': owner_hasn_id,
                    'now': now,
                },
            )
            if switched.rowcount != 1:
                raise errors.ConflictError(msg='STORAGE_MIGRATION_LOCATION_CHANGED')
            await db.execute(
                text(
                    """
                    UPDATE hasn_storage_migration_items
                    SET verify_status = 'rolled_back',
                        error_code = NULL,
                        updated_time = :now
                    WHERE item_id = :item_id
                    """
                ),
                {'item_id': str(item['item_id']), 'now': now},
            )
            await self._insert_orphan_cleanup_job(
                db,
                owner_hasn_id=owner_hasn_id,
                storage_id=int(item['target_storage_id']),
                object_key=str(item['target_object_key']),
                reservation_id=f'migration:{job_id}:{item["item_id"]}',
                reason='migration_rollback',
            )
            return True

    async def process_jobs(
        self,
        *,
        job_type: str | None = None,
        limit: int = 20,
        owner_hasn_id: str | None = None,
    ) -> int:
        """领取并执行持久作业；数据库锁不跨越对象存储网络调用。"""
        if limit <= 0 or limit > 1000:
            raise errors.RequestError(
                code=StandardResponseCode.HTTP_422,
                msg='STORAGE_JOB_LIMIT_INVALID',
            )
        if job_type not in {
            None,
            'object_purge',
            'orphan_cleanup',
            'storage_export',
            'storage_migration',
        }:
            raise errors.RequestError(
                code=StandardResponseCode.HTTP_422,
                msg='STORAGE_JOB_TYPE_UNSUPPORTED',
            )

        succeeded = 0
        for _ in range(limit):
            job = await self._claim_cleanup_job(
                job_type=job_type,
                owner_hasn_id=owner_hasn_id,
            )
            if job is None:
                break
            try:
                await self._execute_cleanup_job(job)
            except _ExportValidationFailed:
                continue
            except Exception as exc:
                await self._retry_cleanup_job(job, exc)
            else:
                succeeded += 1
        return succeeded

    async def _claim_cleanup_job(
        self,
        *,
        job_type: str | None,
        owner_hasn_id: str | None,
    ) -> dict[str, Any] | None:
        async with self._sessions.begin() as db:
            row = (
                await db.execute(
                    text(
                        """
                        SELECT id, job_id, owner_hasn_id, job_type, payload, attempt_count
                        FROM hasn_storage_jobs
                        WHERE status IN ('pending', 'retrying')
                          AND (next_attempt_time IS NULL OR next_attempt_time <= :now)
                          AND (
                              CAST(:job_type AS varchar) IS NULL
                              OR job_type = CAST(:job_type AS varchar)
                          )
                          AND (
                              CAST(:owner AS varchar) IS NULL
                              OR owner_hasn_id = CAST(:owner AS varchar)
                          )
                          AND job_type IN (
                              'object_purge', 'orphan_cleanup',
                              'storage_export', 'storage_migration'
                          )
                        ORDER BY next_attempt_time NULLS FIRST, id
                        FOR UPDATE SKIP LOCKED
                        LIMIT 1
                        """
                    ),
                    {
                        'now': timezone.now(),
                        'job_type': job_type,
                        'owner': owner_hasn_id,
                    },
                )
            ).mappings().one_or_none()
            if row is None:
                return None
            now = timezone.now()
            await db.execute(
                text(
                    """
                    UPDATE hasn_storage_jobs
                    SET status = 'running',
                        attempt_count = attempt_count + 1,
                        error_code = NULL,
                        updated_time = :now
                    WHERE id = :id
                    """
                ),
                {'id': int(row['id']), 'now': now},
            )
            return {
                'id': int(row['id']),
                'job_id': str(row['job_id']),
                'owner_hasn_id': row['owner_hasn_id'],
                'job_type': str(row['job_type']),
                'payload': dict(row['payload']),
                'attempt_count': int(row['attempt_count']) + 1,
            }

    async def _execute_cleanup_job(self, job: dict[str, Any]) -> None:
        if job['job_type'] == 'storage_export':
            await self._execute_export_job(job)
            return
        if job['job_type'] == 'storage_migration':
            await self._execute_migration_job(job)
            return
        payload = job['payload']
        storage_id = int(payload['storage_id'])
        object_key = str(payload['object_key'])
        if job['job_type'] == 'object_purge':
            object_id = str(payload['object_id'])
            async with self._sessions() as db:
                obj = (
                    await db.execute(
                        text(
                            """
                            SELECT object_id, owner_hasn_id, storage_id, object_key, state
                            FROM hasn_storage_objects
                            WHERE object_id = :object_id
                            """
                        ),
                        {'object_id': object_id},
                    )
                ).mappings().one_or_none()
                if obj is None or str(obj['state']) == 'deleted':
                    await self._finish_cleanup_job(job_id=str(job['job_id']))
                    return
                if (
                    str(obj['state']) != 'deleting'
                    or int(obj['storage_id']) != storage_id
                    or str(obj['object_key']) != object_key
                    or obj['owner_hasn_id'] != job['owner_hasn_id']
                ):
                    raise errors.ServerError(msg='STORAGE_PURGE_JOB_TARGET_INVALID')
                storage = copy.copy(await StorageService.get_storage(db, storage_id))

            await StorageService.delete_on_storage(storage, object_key=object_key)
            await self._finish_object_purge(
                job_id=str(job['job_id']),
                owner_hasn_id=str(job['owner_hasn_id']),
                object_id=object_id,
            )
            return

        async with self._sessions() as db:
            if job['job_type'] == 'orphan_cleanup':
                live_target = (
                    await db.execute(
                        text(
                            """
                            SELECT 1
                            FROM hasn_storage_objects AS o
                            WHERE o.storage_id = :storage_id
                              AND o.object_key = :object_key
                              AND (
                                  o.state = 'active'
                                  OR EXISTS (
                                      SELECT 1
                                      FROM hasn_assets AS a
                                      WHERE a.object_id = o.object_id
                                        AND a.lifecycle_status NOT IN ('deleting', 'deleted')
                                  )
                              )
                            LIMIT 1
                            """
                        ),
                        {'storage_id': storage_id, 'object_key': object_key},
                    )
                ).scalar_one_or_none()
                if live_target is not None:
                    log.warning(
                        f'孤儿清理跳过已被活跃对象接管的位置: '
                        f'job_id={job["job_id"]} storage_id={storage_id} object_key={object_key}'
                    )
                    await self._finish_cleanup_job(job_id=str(job['job_id']))
                    return
            storage = copy.copy(await StorageService.get_storage(db, storage_id))
        await StorageService.delete_on_storage(storage, object_key=object_key)
        await self._finish_cleanup_job(job_id=str(job['job_id']))

    async def _execute_migration_job(self, job: dict[str, Any]) -> None:
        """复制并校验一个迁移明细，再原子切换权威物理位置。"""
        job_id = str(job['job_id'])
        owner_hasn_id = str(job['owner_hasn_id'])
        async with self._sessions() as db:
            item = (
                await db.execute(
                    text(
                        """
                        SELECT i.item_id, i.object_id, i.source_storage_id,
                               i.source_object_key, i.source_key_layout,
                               i.target_storage_id, i.target_object_key,
                               i.source_size_bytes, i.source_sha256,
                               i.verify_status,
                               o.storage_id AS current_storage_id,
                               o.object_key AS current_object_key,
                               o.state AS object_state,
                               COALESCE(
                                   (
                                       SELECT a.mime
                                       FROM hasn_assets AS a
                                       WHERE a.object_id = i.object_id
                                         AND a.lifecycle_status <> 'deleted'
                                       ORDER BY a.id
                                       LIMIT 1
                                   ),
                                   'application/octet-stream'
                               ) AS mime
                        FROM hasn_storage_migration_items AS i
                        JOIN hasn_storage_objects AS o ON o.object_id = i.object_id
                        WHERE i.job_id = :job_id
                          AND i.verify_status IN ('pending', 'copied', 'verified', 'failed')
                        ORDER BY i.id
                        LIMIT 1
                        """
                    ),
                    {'job_id': job_id},
                )
            ).mappings().one_or_none()
            if item is None:
                await self._finish_migration_if_complete(
                    job_id=job_id,
                    observation_seconds=int(job['payload'].get('observation_seconds') or 0),
                )
                return
            if (
                str(item['object_state']) != 'active'
                or int(item['current_storage_id']) != int(item['source_storage_id'])
                or str(item['current_object_key']) != str(item['source_object_key'])
            ):
                raise errors.ConflictError(msg='STORAGE_MIGRATION_LOCATION_CHANGED')
            source_storage = copy.copy(
                await StorageService.get_storage(db, int(item['source_storage_id']))
            )
            target_storage = copy.copy(
                await StorageService.get_storage(db, int(item['target_storage_id']))
            )

        try:
            source_stat = await StorageService.stat_on_storage(
                source_storage,
                object_key=str(item['source_object_key']),
            )
            source_sha, source_size = await StorageService.sha256_on_storage(
                source_storage,
                object_key=str(item['source_object_key']),
            )
            expected_size = int(item['source_size_bytes'])
            expected_sha = (
                str(item['source_sha256']).strip() if item['source_sha256'] else source_sha
            )
            if (
                source_stat.size != expected_size
                or source_size != expected_size
                or source_sha != expected_sha
            ):
                raise errors.ServerError(msg='STORAGE_MIGRATION_VERIFY_FAILED')

            await StorageService.copy_between_storages(
                source_storage,
                source_key=str(item['source_object_key']),
                target=target_storage,
                target_key=str(item['target_object_key']),
                size=expected_size,
                content_type=str(item['mime']),
            )
            await self._mark_migration_item_copied(
                item_id=str(item['item_id']),
                source_sha256=source_sha,
            )
            target_stat = await StorageService.stat_on_storage(
                target_storage,
                object_key=str(item['target_object_key']),
            )
            target_sha, target_size = await StorageService.sha256_on_storage(
                target_storage,
                object_key=str(item['target_object_key']),
            )
            if (
                target_stat.size != expected_size
                or target_size != expected_size
                or target_sha != source_sha
            ):
                raise errors.ServerError(msg='STORAGE_MIGRATION_VERIFY_FAILED')
            await self._switch_migration_item(
                job_id=job_id,
                owner_hasn_id=owner_hasn_id,
                item=dict(item),
                source_sha256=source_sha,
                observation_seconds=int(job['payload'].get('observation_seconds') or 0),
            )
        except Exception as exc:
            await self._mark_migration_item_failed(
                item_id=str(item['item_id']),
                exc=exc,
            )
            raise

    async def _mark_migration_item_copied(
        self,
        *,
        item_id: str,
        source_sha256: str,
    ) -> None:
        async with self._sessions.begin() as db:
            await db.execute(
                text(
                    """
                    UPDATE hasn_storage_migration_items
                    SET source_sha256 = :source_sha256,
                        verify_status = 'copied',
                        error_code = NULL,
                        updated_time = :now
                    WHERE item_id = :item_id
                      AND verify_status IN ('pending', 'copied', 'failed')
                    """
                ),
                {
                    'item_id': item_id,
                    'source_sha256': source_sha256,
                    'now': timezone.now(),
                },
            )

    async def _switch_migration_item(
        self,
        *,
        job_id: str,
        owner_hasn_id: str,
        item: dict[str, Any],
        source_sha256: str,
        observation_seconds: int,
    ) -> None:
        now = timezone.now()
        async with self._sessions.begin() as db:
            detail = (
                await db.execute(
                    text(
                        """
                        SELECT verify_status
                        FROM hasn_storage_migration_items
                        WHERE item_id = :item_id
                        FOR UPDATE
                        """
                    ),
                    {'item_id': str(item['item_id'])},
                )
            ).scalar_one_or_none()
            if detail == 'switched':
                return
            if detail not in {'copied', 'verified'}:
                raise errors.ServerError(msg='STORAGE_MIGRATION_ITEM_STATE_INVALID')
            switched = await db.execute(
                text(
                    """
                    UPDATE hasn_storage_objects
                    SET storage_id = :target_storage_id,
                        object_key = :target_object_key,
                        key_layout = 'owner_scoped',
                        updated_time = :now
                    WHERE object_id = :object_id
                      AND owner_hasn_id = :owner
                      AND storage_id = :source_storage_id
                      AND object_key = :source_object_key
                      AND state = 'active'
                    """
                ),
                {
                    **item,
                    'owner': owner_hasn_id,
                    'now': now,
                },
            )
            if switched.rowcount != 1:
                raise errors.ConflictError(msg='STORAGE_MIGRATION_LOCATION_CHANGED')
            await db.execute(
                text(
                    """
                    UPDATE hasn_storage_migration_items
                    SET source_sha256 = :source_sha256,
                        verify_status = 'switched',
                        error_code = NULL,
                        updated_time = :now
                    WHERE item_id = :item_id
                    """
                ),
                {
                    'item_id': str(item['item_id']),
                    'source_sha256': source_sha256,
                    'now': now,
                },
            )
            processed = int(
                (
                    await db.execute(
                        text(
                            """
                            SELECT COUNT(*)
                            FROM hasn_storage_migration_items
                            WHERE job_id = :job_id
                              AND verify_status = 'switched'
                            """
                        ),
                        {'job_id': job_id},
                    )
                ).scalar_one()
            )
            total = int(
                (
                    await db.execute(
                        text(
                            """
                            SELECT total_items
                            FROM hasn_storage_jobs
                            WHERE job_id = :job_id
                            FOR UPDATE
                            """
                        ),
                        {'job_id': job_id},
                    )
                ).scalar_one()
            )
            completed = processed == total
            observation_until = now + timedelta(seconds=observation_seconds)
            await db.execute(
                text(
                    """
                    UPDATE hasn_storage_jobs
                    SET status = :status,
                        processed_items = :processed,
                        failed_items = 0,
                        attempt_count = 0,
                        error_code = NULL,
                        next_attempt_time = :next_attempt,
                        result = CASE
                            WHEN CAST(:completed AS boolean)
                            THEN result || jsonb_build_object(
                                'observation_until',
                                CAST(:observation_until AS timestamptz),
                                'source_cleanup_status',
                                'retained'
                            )
                            ELSE result
                        END,
                        updated_time = :now
                    WHERE job_id = :job_id
                      AND status = 'running'
                    """
                ),
                {
                    'job_id': job_id,
                    'status': 'succeeded' if completed else 'pending',
                    'processed': processed,
                    'next_attempt': None if completed else now,
                    'completed': completed,
                    'observation_until': observation_until,
                    'now': now,
                },
            )

    async def _finish_migration_if_complete(
        self,
        *,
        job_id: str,
        observation_seconds: int,
    ) -> None:
        now = timezone.now()
        async with self._sessions.begin() as db:
            remaining = int(
                (
                    await db.execute(
                        text(
                            """
                            SELECT COUNT(*)
                            FROM hasn_storage_migration_items
                            WHERE job_id = :job_id
                              AND verify_status <> 'switched'
                            """
                        ),
                        {'job_id': job_id},
                    )
                ).scalar_one()
            )
            if remaining:
                raise errors.ServerError(msg='STORAGE_MIGRATION_ITEM_STATE_INVALID')
            await db.execute(
                text(
                    """
                    UPDATE hasn_storage_jobs
                    SET status = 'succeeded',
                        processed_items = total_items,
                        failed_items = 0,
                        attempt_count = 0,
                        error_code = NULL,
                        next_attempt_time = NULL,
                        result = result || jsonb_build_object(
                            'observation_until',
                            CAST(:observation_until AS timestamptz),
                            'source_cleanup_status',
                            'retained'
                        ),
                        updated_time = :now
                    WHERE job_id = :job_id
                      AND status = 'running'
                    """
                ),
                {
                    'job_id': job_id,
                    'observation_until': now + timedelta(seconds=observation_seconds),
                    'now': now,
                },
            )

    async def _mark_migration_item_failed(
        self,
        *,
        item_id: str,
        exc: Exception,
    ) -> None:
        error_code = (
            exc.msg if isinstance(exc, errors.BaseExceptionError) else type(exc).__name__
        )
        async with self._sessions.begin() as db:
            await db.execute(
                text(
                    """
                    UPDATE hasn_storage_migration_items
                    SET verify_status = 'failed',
                        error_code = :error_code,
                        updated_time = :now
                    WHERE item_id = :item_id
                      AND verify_status <> 'switched'
                    """
                ),
                {
                    'item_id': item_id,
                    'error_code': str(error_code)[:64],
                    'now': timezone.now(),
                },
            )

    async def _execute_export_job(self, job: dict[str, Any]) -> None:
        """生成可下载 manifest 或受限归档，并把结果原子写回作业。"""
        owner_hasn_id = str(job['owner_hasn_id'])
        payload = dict(job['payload'])
        mode = str(payload.get('mode'))
        snapshot_time_raw = payload.get('snapshot_time')
        if mode not in {'manifest', 'archive'} or not isinstance(snapshot_time_raw, str):
            raise errors.ServerError(msg='STORAGE_EXPORT_PAYLOAD_INVALID')
        datetime.fromisoformat(snapshot_time_raw)

        async with self._sessions() as db:
            rows = (
                await db.execute(
                    text(
                        """
                        SELECT asset_id, original_name, mime, source_app, access,
                               asset_created_time AS created_time, lifecycle_status,
                               logical_path, object_id, storage_id, object_key,
                               size_bytes, sha256, bindings
                        FROM hasn_storage_export_items
                        WHERE owner_hasn_id = :owner AND job_id = :job_id
                        ORDER BY logical_path, asset_id
                        """
                    ),
                    {
                        'owner': owner_hasn_id,
                        'job_id': str(job['job_id']),
                    },
                )
            ).mappings().all()
            storage_ids = {int(row['storage_id']) for row in rows}
            storages = {
                storage_id: copy.copy(await StorageService.get_storage(db, storage_id))
                for storage_id in storage_ids
            }
            target_storage = await StorageService.get_write_storage(db, access='private')

        failures = await self._validate_export_sources(rows=rows, storages=storages)
        if failures:
            await self._fail_export_validation(
                job_id=str(job['job_id']),
                total_items=len(rows),
                failures=failures,
            )
            raise _ExportValidationFailed
        await self._mark_export_sources_verified(job_id=str(job['job_id']))

        manifest_fd, manifest_raw_path = tempfile.mkstemp(
            prefix='hasn-storage-export-',
            suffix='.jsonl',
        )
        manifest_path = Path(manifest_raw_path)
        archive_path: Path | None = None
        output_path = manifest_path
        output_mime = 'application/json'
        output_filename = f'hasn-storage-{job["job_id"]}.manifest.jsonl'
        try:
            with os.fdopen(manifest_fd, 'wb') as manifest_file:
                for row in rows:
                    source_storage = storages[int(row['storage_id'])]
                    if str(row['access']) == 'public':
                        download_url = StorageService.public_url(
                            source_storage,
                            str(row['object_key']),
                        )
                    else:
                        download_url = await StorageService.signed_url_on_storage(
                            source_storage,
                            object_key=str(row['object_key']),
                            expires_in=_positive_env_int(
                                'STORAGE_EXPORT_TTL_SECONDS',
                                _EXPORT_TTL_DEFAULT,
                            ),
                        )
                    manifest = {
                        'asset_id': str(row['asset_id']),
                        'logical_path': str(row['logical_path']),
                        'original_name': str(row['original_name'] or ''),
                        'mime': str(row['mime']),
                        'size_bytes': int(row['size_bytes']),
                        'sha256': str(row['sha256']).strip() if row['sha256'] else None,
                        'created_time': row['created_time'].isoformat(),
                        'source_app': row['source_app'],
                        'lifecycle_status': str(row['lifecycle_status']),
                        'bindings': list(row['bindings']),
                        'download_url': download_url,
                    }
                    line = (
                        json.dumps(manifest, ensure_ascii=False, separators=(',', ':')) + '\n'
                    ).encode()
                    manifest_file.write(line)
                manifest_file.flush()
                os.fsync(manifest_file.fileno())

            if mode == 'archive':
                archive_fd, archive_raw_path = tempfile.mkstemp(
                    prefix='hasn-storage-export-',
                    suffix='.zip',
                )
                os.close(archive_fd)
                archive_path = Path(archive_raw_path)
                with zipfile.ZipFile(
                    archive_path,
                    mode='w',
                    compression=zipfile.ZIP_DEFLATED,
                    allowZip64=True,
                ) as archive:
                    archive.write(manifest_path, arcname='manifest.jsonl')
                    unique_objects: dict[str, Any] = {}
                    for row in rows:
                        unique_objects.setdefault(str(row['object_id']), row)
                    for object_id, row in unique_objects.items():
                        storage = storages[int(row['storage_id'])]
                        with archive.open(f'objects/{object_id}', mode='w', force_zip64=True) as writer:
                            async for chunk in StorageService.read_stream_on_storage(
                                storage,
                                object_key=str(row['object_key']),
                                expected_size=int(row['size_bytes']),
                            ):
                                await asyncio.to_thread(writer.write, chunk)
                output_path = archive_path
                output_mime = 'application/zip'
                output_filename = f'hasn-storage-{job["job_id"]}.zip'

            output_size = output_path.stat().st_size
            output_sha = await asyncio.to_thread(self._sha256_file, output_path)
            object_key = (
                f'owners/{owner_hasn_id}/exports/{job["job_id"]}/'
                f'{"archive.zip" if mode == "archive" else "manifest.jsonl"}'
            )
            await StorageService.upload_stream_to_storage(
                target_storage,
                self._read_staged_file(output_path),
                size=output_size,
                key=object_key,
                content_type=output_mime,
            )
            stat = await StorageService.stat_on_storage(target_storage, object_key=object_key)
            if stat.size != output_size:
                raise errors.GatewayError(msg='STORAGE_EXPORT_SIZE_MISMATCH')
            stored_sha, stored_size = await StorageService.sha256_on_storage(
                target_storage,
                object_key=object_key,
            )
            if stored_size != output_size or stored_sha != output_sha:
                raise errors.GatewayError(msg='STORAGE_EXPORT_HASH_MISMATCH')

            result = {
                'storage_id': int(target_storage.id),
                'object_key': object_key,
                'size_bytes': output_size,
                'sha256': output_sha,
                'filename': output_filename,
                'mime': output_mime,
            }
            async with self._sessions.begin() as db:
                updated = (
                    await db.execute(
                        text(
                            """
                            UPDATE hasn_storage_jobs
                            SET status = 'succeeded',
                                processed_items = :processed_items,
                                failed_items = 0,
                                error_code = NULL,
                                next_attempt_time = NULL,
                                result = CAST(:result_json AS jsonb),
                                updated_time = :now
                            WHERE job_id = :job_id AND status = 'running'
                            RETURNING job_id
                            """
                        ),
                        {
                            'job_id': str(job['job_id']),
                            'processed_items': len(rows),
                            'result_json': json.dumps(result, ensure_ascii=False),
                            'now': timezone.now(),
                        },
                    )
                ).scalar_one_or_none()
                if updated is None:
                    raise errors.ServerError(msg='STORAGE_JOB_STATE_INVALID')
                await self._notify_export_ready(
                    db,
                    owner_hasn_id=owner_hasn_id,
                    job_id=str(job['job_id']),
                    item_count=len(rows),
                    size_bytes=output_size,
                )
        finally:
            await asyncio.to_thread(manifest_path.unlink, missing_ok=True)
            if archive_path is not None:
                await asyncio.to_thread(archive_path.unlink, missing_ok=True)

    @staticmethod
    async def _notify_export_ready(
        db: AsyncSession,
        *,
        owner_hasn_id: str,
        job_id: str,
        item_count: int,
        size_bytes: int,
    ) -> None:
        """导出跑完告知主人：这是主人主动发起、要等结果的动作，跑完不吭声等于让人干等。

        产物有保留期（默认 24h），过期即不可下载，所以这条要能弹到桌面（category=system
        默认带 toast/push），不像后台清理那种纯告知只留通知中心。
        通知失败不回滚作业——产物已经落桶，告知是 best-effort。
        """
        from backend.app.notification.service.notification_service import NotificationService

        try:
            await NotificationService.emit(
                db,
                recipient_id=owner_hasn_id,
                source={'kind': 'system', 'id': 'owner_storage', 'display_name': '云存储'},
                category='system',
                type='storage_export_ready',
                title='云存储导出已完成',
                body=f'已打包 {item_count} 个文件（{size_bytes} 字节），可在「设置 → 云存储 → 用量」'
                '下载。产物保留 24 小时，过期后需重新导出。',
                payload={
                    'job_id': job_id,
                    'item_count': item_count,
                    'size_bytes': size_bytes,
                    'target': {'id': job_id},
                    'link': 'hasn://storage/usage',
                },
                dedupe_key=f'storage_export_ready:{job_id}',
                # 卡片消息会另开一个服务号会话，对「去设置页点下载」这件事是多余的一跳。
                delivery_hint={'channels': {'card_message': False}},
            )
        except Exception as exc:  # noqa: BLE001 - 告知失败不该让已完成的导出回滚
            log.warning(
                f'云存储导出完成通知发送失败: owner={owner_hasn_id} job_id={job_id} '
                f'{type(exc).__name__}: {exc!s}'
            )

    async def _validate_export_sources(
        self,
        *,
        rows: Sequence[Any],
        storages: dict[int, Any],
    ) -> list[dict[str, str]]:
        """在签发下载地址前校验每个源对象的大小与 SHA-256。"""
        object_errors: dict[str, str] = {}
        checked: set[str] = set()
        for row in rows:
            object_id = str(row['object_id'])
            if object_id in checked:
                continue
            checked.add(object_id)
            storage = storages[int(row['storage_id'])]
            try:
                stat = await StorageService.stat_on_storage(
                    storage,
                    object_key=str(row['object_key']),
                )
            except Exception as exc:
                if self._is_missing_storage_object(exc):
                    object_errors[object_id] = 'STORAGE_OBJECT_MISSING'
                    continue
                raise
            expected_size = int(row['size_bytes'])
            if stat.size != expected_size:
                object_errors[object_id] = 'STORAGE_EXPORT_SIZE_MISMATCH'
                continue
            stored_sha, stored_size = await StorageService.sha256_on_storage(
                storage,
                object_key=str(row['object_key']),
            )
            expected_sha = str(row['sha256']).strip() if row['sha256'] else ''
            if stored_size != expected_size:
                object_errors[object_id] = 'STORAGE_EXPORT_SIZE_MISMATCH'
            elif not expected_sha or stored_sha != expected_sha:
                object_errors[object_id] = 'STORAGE_EXPORT_HASH_MISMATCH'

        return [
            {
                'asset_id': str(row['asset_id']),
                'logical_path': str(row['logical_path']),
                'error_code': object_errors[str(row['object_id'])],
            }
            for row in rows
            if str(row['object_id']) in object_errors
        ]

    async def _mark_export_sources_verified(self, *, job_id: str) -> None:
        """把已通过真实对象大小与哈希校验的快照明细标记为可导出。"""
        async with self._sessions.begin() as db:
            await db.execute(
                text(
                    """
                    UPDATE hasn_storage_export_items
                    SET verify_status = 'verified',
                        error_code = NULL,
                        updated_time = :now
                    WHERE job_id = :job_id AND verify_status = 'pending'
                    """
                ),
                {'job_id': job_id, 'now': timezone.now()},
            )

    async def _fail_export_validation(
        self,
        *,
        job_id: str,
        total_items: int,
        failures: list[dict[str, str]],
    ) -> None:
        """把永久校验错误原子写入作业，阻止导出制品进入可下载状态。"""
        failed_items = len(failures)
        async with self._sessions.begin() as db:
            await db.execute(
                text(
                    """
                    UPDATE hasn_storage_export_items
                    SET verify_status = 'verified',
                        error_code = NULL,
                        updated_time = :now
                    WHERE job_id = :job_id
                    """
                ),
                {'job_id': job_id, 'now': timezone.now()},
            )
            await db.execute(
                text(
                    """
                    UPDATE hasn_storage_export_items
                    SET verify_status = 'failed',
                        error_code = :error_code,
                        updated_time = :now
                    WHERE job_id = :job_id AND asset_id = :asset_id
                    """
                ),
                [
                    {
                        'job_id': job_id,
                        'asset_id': failure['asset_id'],
                        'error_code': failure['error_code'],
                        'now': timezone.now(),
                    }
                    for failure in failures
                ],
            )
            updated = (
                await db.execute(
                    text(
                        """
                        UPDATE hasn_storage_jobs
                        SET status = 'failed',
                            processed_items = :processed_items,
                            failed_items = :failed_items,
                            error_code = 'STORAGE_EXPORT_FAILED',
                            next_attempt_time = NULL,
                            result = jsonb_build_object(
                                'failures',
                                CAST(:failures AS jsonb)
                            ),
                            updated_time = :now
                        WHERE job_id = :job_id AND status = 'running'
                        RETURNING job_id
                        """
                    ),
                    {
                        'job_id': job_id,
                        'processed_items': max(0, total_items - failed_items),
                        'failed_items': failed_items,
                        'failures': json.dumps(failures, ensure_ascii=False),
                        'now': timezone.now(),
                    },
                )
            ).scalar_one_or_none()
        if updated is None:
            raise errors.ServerError(msg='STORAGE_JOB_STATE_INVALID')
        log.error(
            f'用户云存储导出源对象校验失败: job_id={job_id}, failed_items={failed_items}'
        )

    @staticmethod
    def _is_missing_storage_object(exc: Exception) -> bool:
        """识别对象存储明确返回的 404，不把瞬时网络故障误判为永久缺失。"""
        current: BaseException | None = exc
        while current is not None:
            detail = f'{type(current).__name__}: {current!s}'
            if 'NotFound' in detail or 'status: 404' in detail or 'HTTP 404' in detail:
                return True
            current = current.__cause__
        return False

    @staticmethod
    def _sha256_file(path: Path) -> str:
        digest = hashlib.sha256()
        with path.open('rb') as file_obj:
            while chunk := file_obj.read(_UPLOAD_CHUNK_SIZE):
                digest.update(chunk)
        return digest.hexdigest()

    async def _finish_object_purge(
        self,
        *,
        job_id: str,
        owner_hasn_id: str,
        object_id: str,
    ) -> None:
        async with self._sessions.begin() as db:
            job = (
                await db.execute(
                    text(
                        """
                        SELECT id, status
                        FROM hasn_storage_jobs
                        WHERE job_id = :job_id
                        FOR UPDATE
                        """
                    ),
                    {'job_id': job_id},
                )
            ).mappings().one_or_none()
            if job is None:
                raise errors.ServerError(msg='STORAGE_JOB_NOT_FOUND')
            if str(job['status']) == 'succeeded':
                return
            if str(job['status']) != 'running':
                raise errors.ServerError(msg='STORAGE_JOB_STATE_INVALID')

            obj = (
                await db.execute(
                    text(
                        """
                        SELECT object_id, owner_hasn_id, size_bytes, billable_to_owner, state
                        FROM hasn_storage_objects
                        WHERE object_id = :object_id AND owner_hasn_id = :owner
                        FOR UPDATE
                        """
                    ),
                    {'object_id': object_id, 'owner': owner_hasn_id},
                )
            ).mappings().one_or_none()
            if obj is None:
                await self._finish_cleanup_job_in_transaction(db, job_id=job_id)
                return
            if str(obj['state']) == 'deleted':
                await self._finish_cleanup_job_in_transaction(db, job_id=job_id)
                return
            if str(obj['state']) != 'deleting':
                raise errors.ServerError(msg='STORAGE_PURGE_JOB_TARGET_INVALID')

            now = timezone.now()
            await db.execute(
                text(
                    """
                    UPDATE hasn_storage_objects
                    SET state = 'deleted', updated_time = :now
                    WHERE object_id = :object_id
                    """
                ),
                {'object_id': object_id, 'now': now},
            )
            await db.execute(
                text(
                    """
                    UPDATE hasn_assets
                    SET lifecycle_status = 'deleted',
                        deleted_time = COALESCE(deleted_time, :now),
                        updated_time = :now
                    WHERE owner_hasn_id = :owner AND object_id = :object_id
                    """
                ),
                {'owner': owner_hasn_id, 'object_id': object_id, 'now': now},
            )
            if bool(obj['billable_to_owner']):
                await self._decrement_account_usage_or_skip_orphan_identity(
                    db,
                    owner_hasn_id=owner_hasn_id,
                    size_bytes=int(obj['size_bytes']),
                    now=now,
                )
            await self._finish_cleanup_job_in_transaction(db, job_id=job_id)

    async def _finish_cleanup_job(self, *, job_id: str) -> None:
        async with self._sessions.begin() as db:
            await self._finish_cleanup_job_in_transaction(db, job_id=job_id)

    @staticmethod
    async def _finish_cleanup_job_in_transaction(db: AsyncSession, *, job_id: str) -> None:
        result = (
            await db.execute(
                text(
                    """
                    UPDATE hasn_storage_jobs
                    SET status = 'succeeded',
                        processed_items = total_items,
                        failed_items = 0,
                        error_code = NULL,
                        next_attempt_time = NULL,
                        result = jsonb_build_object(
                            'completed_time',
                            CAST(:now AS timestamptz)
                        ),
                        updated_time = :now
                    WHERE job_id = :job_id AND status = 'running'
                    RETURNING job_id
                    """
                ),
                {'job_id': job_id, 'now': timezone.now()},
            )
        ).scalar_one_or_none()
        if result is None:
            raise errors.ServerError(msg='STORAGE_JOB_STATE_INVALID')

    async def _retry_cleanup_job(self, job: dict[str, Any], exc: Exception) -> None:
        attempt_count = int(job['attempt_count'])
        exhausted = attempt_count >= 8
        error_code = exc.msg if isinstance(exc, errors.BaseExceptionError) else type(exc).__name__
        next_attempt = None
        if not exhausted:
            next_attempt = timezone.now() + timedelta(seconds=min(3600, 2 ** min(attempt_count, 10)))
        async with self._sessions.begin() as db:
            await db.execute(
                text(
                    """
                    UPDATE hasn_storage_jobs
                    SET status = :status,
                        failed_items = 1,
                        error_code = :error_code,
                        next_attempt_time = :next_attempt,
                        updated_time = :now
                    WHERE job_id = :job_id AND status = 'running'
                    """
                ),
                {
                    'job_id': str(job['job_id']),
                    'status': 'failed' if exhausted else 'retrying',
                    'error_code': str(error_code)[:64],
                    'next_attempt': next_attempt,
                    'now': timezone.now(),
                },
            )
        message = (
            f"用户云存储清理作业失败: job_id={job['job_id']}, "
            f"attempt={attempt_count}, error={type(exc).__name__}: {exc!r}"
        )
        if exhausted:
            log.error(message)
        else:
            log.warning(message)

    @staticmethod
    async def _lock_owned_asset(
        db: AsyncSession,
        *,
        owner_hasn_id: str,
        asset_id: str,
    ) -> Any:
        row = (
            await db.execute(
                text(
                    """
                    SELECT asset_id, object_id, lifecycle_status
                    FROM hasn_assets
                    WHERE owner_hasn_id = :owner AND asset_id = :asset_id
                    FOR UPDATE
                    """
                ),
                {'owner': owner_hasn_id, 'asset_id': asset_id},
            )
        ).mappings().one_or_none()
        if row is None:
            raise errors.NotFoundError(msg='STORAGE_ASSET_NOT_FOUND')
        return row

    @staticmethod
    async def _active_references_in_transaction(
        db: AsyncSession,
        *,
        owner_hasn_id: str,
        asset_id: str,
    ) -> list[dict[str, str]]:
        rows = (
            await db.execute(
                text(
                    """
                    SELECT binding_id, resource_uri, role, status
                    FROM hasn_asset_bindings
                    WHERE owner_hasn_id = :owner
                      AND asset_id = :asset_id
                      AND status = 'active'
                    ORDER BY created_time, id
                    FOR UPDATE
                    """
                ),
                {'owner': owner_hasn_id, 'asset_id': asset_id},
            )
        ).mappings().all()
        return [
            {
                'binding_id': str(row['binding_id']),
                'resource_uri': str(row['resource_uri']),
                'role': str(row['role']),
                'status': str(row['status']),
            }
            for row in rows
        ]

    @staticmethod
    async def _tombstone_business_references(
        db: AsyncSession,
        *,
        owner_hasn_id: str,
        asset_id: str,
        references: list[dict[str, str]],
        now: datetime,
    ) -> None:
        """在删除资产的同一事务中把业务引用降级为确定墓碑。"""
        asset_uri = f'hasn://asset/{asset_id}'
        for reference in references:
            resource_uri = reference['resource_uri']
            if reference['role'] == 'inline_image':
                # 正文图片可能同时存在于当前正文和不可变历史版本，通用 file-source 墓碑不能安全改写。
                # 要回收该资产，必须先删除引用它的知识文档；文档删除会原子停用全部 inline binding。
                raise errors.ConflictError(
                    msg='STORAGE_INLINE_IMAGE_IN_USE',
                    data={
                        'resource_uri': resource_uri,
                        'action': '请先删除引用该图片的知识文档，再彻底删除资产',
                    },
                )
            updated = 0
            if resource_uri.startswith('hasn://messages/c/'):
                message_location = resource_uri.removeprefix('hasn://messages/c/')
                conversation_id, separator, message_id = message_location.partition('#')
                if not separator or not message_id.isdigit():
                    raise errors.ServerError(msg='STORAGE_TOMBSTONE_RESOURCE_INVALID')
                result = await db.execute(
                    text(
                        f"""
                        UPDATE {SCHEMA_NAMES.im_table("hasn_messages")}
                        SET content = jsonb_set(
                                content,
                                '{{attachments}}',
                                (
                                    SELECT jsonb_agg(
                                        CASE
                                            WHEN item ->> 'uri' = :asset_uri
                                            THEN (item - 'display_url' - 'expires_at')
                                                 || jsonb_build_object(
                                                     'tombstone', true,
                                                     'tombstone_message',
                                                     '文件已被发送方删除'
                                                 )
                                            ELSE item
                                        END
                                        ORDER BY ordinal
                                    )
                                    FROM jsonb_array_elements(
                                        COALESCE(content -> 'attachments', '[]'::jsonb)
                                    ) WITH ORDINALITY AS attachment(item, ordinal)
                                ),
                                true
                            ),
                            updated_time = :now
                        WHERE id = :message_id
                          AND conversation_id = CAST(:conversation_id AS uuid)
                          AND EXISTS (
                              SELECT 1
                              FROM jsonb_array_elements(
                                  COALESCE(content -> 'attachments', '[]'::jsonb)
                              ) AS attachment(item)
                              WHERE item ->> 'uri' = :asset_uri
                          )
                        """
                    ),
                    {
                        'asset_uri': asset_uri,
                        'now': now,
                        'message_id': int(message_id),
                        'conversation_id': conversation_id,
                    },
                )
                updated = int(getattr(result, 'rowcount', 0) or 0)
            elif resource_uri.startswith('hasn://knowledge/documents/'):
                document_id = resource_uri.removeprefix('hasn://knowledge/documents/')
                if not document_id.isdigit():
                    raise errors.ServerError(msg='STORAGE_TOMBSTONE_RESOURCE_INVALID')
                result = await db.execute(
                    text(
                        """
                        UPDATE hasn_knowledge.document
                        SET parse_status = 'failed',
                            parse_error = '源文件缺失：主人已彻底删除云存储原件',
                            chunk_count = 0,
                            updated_time = :now
                        WHERE id = :document_id
                          AND owner_id = :owner
                          AND asset_uri = :asset_uri
                          AND deleted_time IS NULL
                        """
                    ),
                    {
                        'now': now,
                        'document_id': int(document_id),
                        'owner': owner_hasn_id,
                        'asset_uri': asset_uri,
                    },
                )
                updated = int(getattr(result, 'rowcount', 0) or 0)
            elif resource_uri.startswith('hasn://artifact/'):
                artifact_id = resource_uri.removeprefix('hasn://artifact/')
                if not artifact_id:
                    raise errors.ServerError(msg='STORAGE_TOMBSTONE_RESOURCE_INVALID')
                result = await db.execute(
                    text(
                        """
                        UPDATE hasn_artifacts
                        SET status = 'missing',
                            metadata = COALESCE(metadata, '{}'::jsonb)
                                || jsonb_build_object(
                                    'asset_tombstone', true,
                                    'tombstone_message', '源文件缺失',
                                    'tombstoned_time', CAST(:tombstoned_time AS text)
                                ),
                            updated_time = :now
                        WHERE artifact_id = :artifact_id
                          AND owner_hasn_id = :owner
                          AND asset_id = :asset_id
                          AND status = 'active'
                        """
                    ),
                    {
                        'now': now,
                        'tombstoned_time': now.isoformat(),
                        'artifact_id': artifact_id,
                        'owner': owner_hasn_id,
                        'asset_id': asset_id,
                    },
                )
                updated = int(getattr(result, 'rowcount', 0) or 0)
            else:
                raise errors.ServerError(
                    msg='STORAGE_TOMBSTONE_HANDLER_UNSUPPORTED',
                    data={'resource_uri': resource_uri},
                )
            if updated != 1:
                raise errors.ServerError(
                    msg='STORAGE_TOMBSTONE_TARGET_MISMATCH',
                    data={'resource_uri': resource_uri},
                )

    @staticmethod
    async def _mark_asset_deleted(
        db: AsyncSession,
        *,
        owner_hasn_id: str,
        asset_id: str,
        now: datetime,
    ) -> None:
        await db.execute(
            text(
                """
                UPDATE hasn_assets
                SET lifecycle_status = 'deleted',
                    deleted_time = COALESCE(deleted_time, :now),
                    version = version + 1,
                    updated_time = :now
                WHERE owner_hasn_id = :owner AND asset_id = :asset_id
                """
            ),
            {'owner': owner_hasn_id, 'asset_id': asset_id, 'now': now},
        )

    @staticmethod
    async def _decrement_account_usage_or_skip_orphan_identity(
        db: AsyncSession,
        *,
        owner_hasn_id: str,
        size_bytes: int,
        now: datetime,
    ) -> None:
        """历史孤儿 Owner 无账户可扣；正常身份缺账户仍视为不变量破坏。"""
        account_exists = (
            await db.execute(
                text(
                    """
                    SELECT 1
                    FROM hasn_storage_accounts
                    WHERE owner_hasn_id = :owner
                    """
                ),
                {'owner': owner_hasn_id},
            )
        ).scalar_one_or_none()
        if account_exists is not None:
            await OwnerStorageService._decrement_account_usage(
                db,
                owner_hasn_id=owner_hasn_id,
                size_bytes=size_bytes,
                now=now,
            )
            return
        identity_exists = (
            await db.execute(
                text(
                    """
                    SELECT 1
                    FROM hasn_humans
                    WHERE hasn_id = :owner AND status = 'active'
                    """
                ),
                {'owner': owner_hasn_id},
            )
        ).scalar_one_or_none()
        if identity_exists is not None:
            raise errors.ServerError(msg='STORAGE_USAGE_COUNTER_INVALID')
        log.warning(
            f'用户云存储历史孤儿 Owner 无账户，物理删除跳过计数器扣减: '
            f'owner={owner_hasn_id}, size_bytes={size_bytes}'
        )

    @staticmethod
    async def _decrement_account_usage(
        db: AsyncSession,
        *,
        owner_hasn_id: str,
        size_bytes: int,
        now: datetime,
    ) -> None:
        result = (
            await db.execute(
                text(
                    """
                    UPDATE hasn_storage_accounts
                    SET used_bytes = used_bytes - :size_bytes,
                        state = CASE
                            WHEN used_bytes - :size_bytes + reserved_bytes <= quota_bytes
                            THEN 'active' ELSE 'over_quota'
                        END,
                        updated_time = :now
                    WHERE owner_hasn_id = :owner AND used_bytes >= :size_bytes
                    RETURNING owner_hasn_id
                    """
                ),
                {
                    'owner': owner_hasn_id,
                    'size_bytes': size_bytes,
                    'now': now,
                },
            )
        ).scalar_one_or_none()
        if result is None:
            raise errors.ServerError(msg='STORAGE_USAGE_COUNTER_INVALID')

    async def reserve(
        self,
        *,
        owner_hasn_id: str,
        requested_bytes: int,
        idempotency_key: str,
        request_fingerprint: str | None = None,
    ) -> StorageReservation:
        """先独立刷新账户，再以独立小事务原子预占。"""
        await self.usage(owner_hasn_id=owner_hasn_id)
        return await self.reserve_existing_account(
            owner_hasn_id=owner_hasn_id,
            requested_bytes=requested_bytes,
            idempotency_key=idempotency_key,
            request_fingerprint=request_fingerprint,
        )

    async def reserve_existing_account(
        self,
        *,
        owner_hasn_id: str,
        requested_bytes: int,
        idempotency_key: str,
        request_fingerprint: str | None = None,
    ) -> StorageReservation:
        """只执行事务 A；该方法用于守卫账户缺失不变量和底层集成测试。"""
        if requested_bytes <= 0:
            raise errors.RequestError(
                code=StandardResponseCode.HTTP_422,
                msg='STORAGE_RESERVATION_SIZE_INVALID',
            )
        if not idempotency_key or len(idempotency_key) > 128:
            raise errors.RequestError(
                code=StandardResponseCode.HTTP_422,
                msg='STORAGE_IDEMPOTENCY_KEY_INVALID',
            )

        existing = await self._reservation_by_idempotency(owner_hasn_id, idempotency_key)
        if existing is not None:
            return self._validate_replay(
                existing,
                requested_bytes,
                request_fingerprint=request_fingerprint,
            )

        now = timezone.now()
        values = {
            'owner': owner_hasn_id,
            'requested': requested_bytes,
            'now': now,
            'reservation_id': f'res_{uuid4().hex}',
            'object_id': f'obj_{uuid4().hex}',
            'idempotency_key': idempotency_key,
            'request_fingerprint': request_fingerprint,
            'expires': now + RESERVATION_TTL,
        }
        try:
            async with self._sessions.begin() as db:
                updated = (
                    await db.execute(
                        text(
                            """
                            UPDATE hasn_storage_accounts
                            SET reserved_bytes = reserved_bytes + :requested,
                                state = CASE
                                    WHEN used_bytes + reserved_bytes + :requested <= quota_bytes
                                    THEN 'active' ELSE 'over_quota'
                                END,
                                updated_time = :now
                            WHERE owner_hasn_id = :owner
                              AND state <> 'suspended'
                              AND used_bytes + reserved_bytes + :requested <= quota_bytes
                            RETURNING quota_bytes, used_bytes, reserved_bytes
                            """
                        ),
                        values,
                    )
                ).mappings().one_or_none()
                if updated is None:
                    account = (
                        await db.execute(
                            text(
                                """
                                SELECT quota_bytes, used_bytes, reserved_bytes
                                FROM hasn_storage_accounts
                                WHERE owner_hasn_id = :owner
                                """
                            ),
                            values,
                        )
                    ).mappings().one_or_none()
                    if account is None:
                        raise errors.ServerError(
                            msg='STORAGE_ACCOUNT_NOT_READY',
                            data={'owner_hasn_id': owner_hasn_id},
                        )
                    raise errors.RequestError(
                        code=StandardResponseCode.HTTP_507,
                        msg='STORAGE_QUOTA_EXCEEDED',
                        data={
                            'quota_bytes': int(account['quota_bytes']),
                            'used_bytes': int(account['used_bytes']),
                            'reserved_bytes': int(account['reserved_bytes']),
                            'requested_bytes': requested_bytes,
                        },
                    )
                row = (
                    await db.execute(
                        text(
                            """
                            INSERT INTO hasn_storage_reservations
                                (reservation_id, owner_hasn_id, object_id, idempotency_key,
                                 request_fingerprint, reserved_bytes, status, expires_time,
                                 created_time, updated_time)
                            VALUES
                                (:reservation_id, :owner, :object_id, :idempotency_key,
                                 :request_fingerprint, :requested, 'reserved', :expires, :now, :now)
                            RETURNING reservation_id, owner_hasn_id, object_id, result_asset_id,
                                      idempotency_key, request_fingerprint, reserved_bytes,
                                      status, expires_time
                            """
                        ),
                        values,
                    )
                ).mappings().one()
                return _reservation_of(row)
        except IntegrityError:
            replay = await self._reservation_by_idempotency(owner_hasn_id, idempotency_key)
            if replay is None:
                raise
            return self._validate_replay(
                replay,
                requested_bytes,
                request_fingerprint=request_fingerprint,
            )

    async def _reservation_by_idempotency(
        self,
        owner_hasn_id: str,
        idempotency_key: str,
    ) -> StorageReservation | None:
        async with self._sessions() as db:
            row = (
                await db.execute(
                    text(
                        """
                        SELECT reservation_id, owner_hasn_id, object_id, result_asset_id,
                               idempotency_key, request_fingerprint, reserved_bytes,
                               status, expires_time
                        FROM hasn_storage_reservations
                        WHERE owner_hasn_id = :owner AND idempotency_key = :idempotency_key
                        """
                    ),
                    {'owner': owner_hasn_id, 'idempotency_key': idempotency_key},
                )
            ).mappings().one_or_none()
            return _reservation_of(row) if row is not None else None

    @staticmethod
    async def _reservation_by_idempotency_in_transaction(
        db: AsyncSession,
        owner_hasn_id: str,
        idempotency_key: str,
    ) -> StorageReservation | None:
        row = (
            await db.execute(
                text(
                    """
                    SELECT reservation_id, owner_hasn_id, object_id, result_asset_id,
                           idempotency_key, request_fingerprint, reserved_bytes,
                           status, expires_time
                    FROM hasn_storage_reservations
                    WHERE owner_hasn_id = :owner AND idempotency_key = :idempotency_key
                    """
                ),
                {'owner': owner_hasn_id, 'idempotency_key': idempotency_key},
            )
        ).mappings().one_or_none()
        return _reservation_of(row) if row is not None else None

    @staticmethod
    def _validate_replay(
        existing: StorageReservation,
        requested_bytes: int,
        *,
        request_fingerprint: str | None = None,
    ) -> StorageReservation:
        if (
            existing.reserved_bytes != requested_bytes
            or (
                request_fingerprint is not None
                and existing.request_fingerprint is not None
                and existing.request_fingerprint != request_fingerprint
            )
        ):
            raise errors.ConflictError(
                msg='STORAGE_IDEMPOTENCY_CONFLICT',
                data={'reservation_id': existing.reservation_id},
            )
        return existing

    async def commit_reservation(
        self,
        reservation_id: str,
        *,
        actual_bytes: int,
    ) -> StorageReservation:
        """原子把预占转入已用；对象与资产登记接入后并入同一事务 B。"""
        if actual_bytes <= 0:
            raise errors.RequestError(
                code=StandardResponseCode.HTTP_422,
                msg='STORAGE_RESERVATION_SIZE_INVALID',
            )
        async with self._sessions.begin() as db:
            reservation = await self._locked_reservation(db, reservation_id)
            if reservation.status == 'committed':
                if reservation.reserved_bytes != actual_bytes:
                    raise errors.ConflictError(msg='STORAGE_IDEMPOTENCY_CONFLICT')
                return reservation
            if reservation.status != 'reserved':
                raise errors.ConflictError(
                    msg='STORAGE_RESERVATION_NOT_ACTIVE',
                    data={'status': reservation.status},
                )
            account = (
                await db.execute(
                    text(
                        """
                        UPDATE hasn_storage_accounts
                        SET reserved_bytes = reserved_bytes - :reserved,
                            used_bytes = used_bytes + :actual,
                            state = CASE
                                WHEN used_bytes + :actual + reserved_bytes - :reserved <= quota_bytes
                                THEN 'active' ELSE 'over_quota'
                            END,
                            updated_time = :now
                        WHERE owner_hasn_id = :owner
                          AND reserved_bytes >= :reserved
                          AND used_bytes + reserved_bytes + :actual - :reserved <= quota_bytes
                        RETURNING quota_bytes, used_bytes, reserved_bytes
                        """
                    ),
                    {
                        'owner': reservation.owner_hasn_id,
                        'reserved': reservation.reserved_bytes,
                        'actual': actual_bytes,
                        'now': timezone.now(),
                    },
                )
            ).mappings().one_or_none()
            if account is None:
                current = (
                    await db.execute(
                        text(
                            """
                            SELECT quota_bytes, used_bytes, reserved_bytes
                            FROM hasn_storage_accounts
                            WHERE owner_hasn_id = :owner
                            """
                        ),
                        {'owner': reservation.owner_hasn_id},
                    )
                ).mappings().one_or_none()
                if current is None:
                    raise errors.ServerError(msg='STORAGE_ACCOUNT_NOT_READY')
                raise errors.RequestError(
                    code=StandardResponseCode.HTTP_507,
                    msg='STORAGE_QUOTA_EXCEEDED',
                    data={
                        'quota_bytes': int(current['quota_bytes']),
                        'used_bytes': int(current['used_bytes']),
                        'reserved_bytes': int(current['reserved_bytes']),
                        'requested_bytes': actual_bytes,
                    },
                )
            row = (
                await db.execute(
                    text(
                        """
                        UPDATE hasn_storage_reservations
                        SET reserved_bytes = :actual, status = 'committed', updated_time = :now
                        WHERE reservation_id = :reservation_id
                        RETURNING reservation_id, owner_hasn_id, object_id, result_asset_id,
                                  idempotency_key, request_fingerprint, reserved_bytes,
                                  status, expires_time
                        """
                    ),
                    {
                        'reservation_id': reservation_id,
                        'actual': actual_bytes,
                        'now': timezone.now(),
                    },
                )
            ).mappings().one()
            return _reservation_of(row)

    async def release_reservation(self, reservation_id: str) -> StorageReservation:
        """幂等释放尚未提交的预占。"""
        async with self._sessions.begin() as db:
            reservation = await self._locked_reservation(db, reservation_id)
            if reservation.status != 'reserved':
                return reservation
            result = await db.execute(
                text(
                    """
                    UPDATE hasn_storage_accounts
                    SET reserved_bytes = reserved_bytes - :reserved,
                        state = CASE
                            WHEN used_bytes + reserved_bytes - :reserved <= quota_bytes
                            THEN 'active' ELSE 'over_quota'
                        END,
                        updated_time = :now
                    WHERE owner_hasn_id = :owner AND reserved_bytes >= :reserved
                    """
                ),
                {
                    'owner': reservation.owner_hasn_id,
                    'reserved': reservation.reserved_bytes,
                    'now': timezone.now(),
                },
            )
            if result.rowcount != 1:
                raise errors.ServerError(msg='STORAGE_RESERVATION_COUNTER_INVALID')
            row = (
                await db.execute(
                    text(
                        """
                        UPDATE hasn_storage_reservations
                        SET status = 'released', updated_time = :now
                        WHERE reservation_id = :reservation_id
                        RETURNING reservation_id, owner_hasn_id, object_id, result_asset_id,
                                  idempotency_key, request_fingerprint, reserved_bytes,
                                  status, expires_time
                        """
                    ),
                    {'reservation_id': reservation_id, 'now': timezone.now()},
                )
            ).mappings().one()
            return _reservation_of(row)

    @staticmethod
    async def _locked_reservation(db: AsyncSession, reservation_id: str) -> StorageReservation:
        row = (
            await db.execute(
                text(
                    """
                    SELECT reservation_id, owner_hasn_id, object_id, result_asset_id,
                           idempotency_key, request_fingerprint, reserved_bytes,
                           status, expires_time
                    FROM hasn_storage_reservations
                    WHERE reservation_id = :reservation_id
                    FOR UPDATE
                    """
                ),
                {'reservation_id': reservation_id},
            )
        ).mappings().one_or_none()
        if row is None:
            raise errors.NotFoundError(msg='STORAGE_RESERVATION_NOT_FOUND')
        return _reservation_of(row)
