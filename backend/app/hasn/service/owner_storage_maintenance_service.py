"""用户云存储存量回填、对象核验与计数器修复。"""

from __future__ import annotations

import copy
import hashlib
import os
import posixpath

from datetime import timedelta
from typing import Any
from uuid import uuid4

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from backend.app.hasn.service.owner_storage_names import (
    display_name_for_upload,
    normalize_storage_name,
    suffixed_name,
)
from backend.app.hasn.service.owner_storage_service import OwnerStorageService, SessionFactory
from backend.common.exception import errors
from backend.common.log import log
from backend.plugin.s3.service.storage_service import StorageService
from backend.utils.timezone import timezone


def _legacy_object_id(owner_hasn_id: str, storage_id: int, object_key: str) -> str:
    """为同一 Owner 的同一旧位置生成可重入物理对象 ID。"""
    payload = f'{owner_hasn_id}\0{storage_id}\0{object_key}'.encode()
    return f'obj_{hashlib.sha256(payload).hexdigest()[:32]}'


def _legacy_entry_id(asset_id: str) -> str:
    """为旧逻辑资产生成可重入目录项 ID。"""
    return f'ent_{hashlib.sha256(asset_id.encode()).hexdigest()[:32]}'


def _legacy_category(object_key: str) -> tuple[str, str, str]:
    """把旧键前缀映射到规范类别、来源应用和系统目录。"""
    prefix = object_key.strip('/').partition('/')[0]
    return {
        'dm': ('dm_attachment', 'hasn_dm', 'conversation_attachments'),
        'docs': ('private_doc', 'knowledge', 'knowledge'),
        'published': ('published_artifact', 'artifact', 'agent_artifacts'),
        'avatars': ('user_avatar', 'profile', 'app_files'),
        'posts': ('post_image', 'community', 'app_files'),
        'files': ('user_upload', 'legacy_general_file', 'my_uploads'),
        'agent_uploads': ('published_artifact', 'legacy_agent_upload', 'agent_artifacts'),
    }.get(prefix, ('user_upload', 'legacy_unknown', 'app_files'))


def _legacy_display_name(asset_id: str, object_key: str) -> str:
    basename = posixpath.basename(object_key.strip('/')) or asset_id
    try:
        return display_name_for_upload(basename)
    except errors.BaseExceptionError:
        return display_name_for_upload(asset_id)


def _is_missing_object_error(exc: Exception) -> bool:
    message = str(exc).lower()
    return 'notfound' in message or 'not found' in message or '404' in message or '不存在' in message


class OwnerStorageMaintenanceService:
    """执行可重入维护任务；对象网络调用不持有数据库事务。"""

    def __init__(self, session_factory: SessionFactory) -> None:
        self._sessions = session_factory
        self._owner_storage = OwnerStorageService(session_factory)

    async def sweep_expired_multipart(
        self,
        *,
        owner_hasn_id: str | None = None,
        limit: int = 200,
    ) -> dict[str, int]:
        """终止已超时 multipart 会话；供应商确认终止后才释放预占。"""
        if limit <= 0 or limit > 2000:
            raise errors.RequestError(msg='STORAGE_MULTIPART_SWEEP_LIMIT_INVALID')
        async with self._sessions() as db:
            rows = (
                await db.execute(
                    text(
                        """
                        SELECT job_id, owner_hasn_id
                        FROM hasn_storage_jobs
                        WHERE job_type = 'multipart_abort_sweep'
                          AND status IN ('running', 'retrying')
                          AND expires_time <= :now
                          AND (
                              CAST(:owner AS varchar) IS NULL
                              OR owner_hasn_id = CAST(:owner AS varchar)
                          )
                        ORDER BY expires_time, id
                        LIMIT :limit
                        """
                    ),
                    {
                        'now': timezone.now(),
                        'owner': owner_hasn_id,
                        'limit': limit,
                    },
                )
            ).mappings().all()
        aborted = 0
        failed = 0
        for row in rows:
            try:
                await self._owner_storage.abort_multipart(
                    owner_hasn_id=str(row['owner_hasn_id']),
                    upload_id=str(row['job_id']),
                )
            except Exception as exc:
                failed += 1
                log.warning(
                    f'multipart 超时终止将在后续重试: job_id={row["job_id"]} '
                    f'{type(exc).__name__}: {exc!s}'
                )
            else:
                aborted += 1
        return {'checked': len(rows), 'aborted': aborted, 'failed': failed}

    async def sweep_expired_reservations(self, *, limit: int = 500) -> dict[str, int]:
        """核实已过期预占的资产状态，再完成或释放预占。

        资产已经与物理对象一同落库时，说明历史故障只漏了预占提交，本方法按对象表
        重建账户用量并把预占置为 committed。不存在可见资产时才置 expired；若发现
        只有物理对象，则先进入可重试清理队列，避免静默留下孤儿。
        """
        if limit <= 0 or limit > 5000:
            raise errors.RequestError(msg='STORAGE_RESERVATION_SWEEP_LIMIT_INVALID')
        async with self._sessions() as db:
            reservation_ids = list(
                (
                    await db.execute(
                        text(
                            """
                            SELECT reservation_id
                            FROM hasn_storage_reservations
                            WHERE status = 'reserved'
                              AND expires_time <= :now
                            ORDER BY expires_time, id
                            LIMIT :limit
                            """
                        ),
                        {'now': timezone.now(), 'limit': limit},
                    )
                ).scalars()
            )

        completed = 0
        expired = 0
        for reservation_id in reservation_ids:
            outcome = await self._settle_expired_reservation(str(reservation_id))
            if outcome == 'completed':
                completed += 1
            elif outcome == 'expired':
                expired += 1
        return {
            'checked': completed + expired,
            'completed': completed,
            'expired': expired,
        }

    async def _settle_expired_reservation(self, reservation_id: str) -> str:
        """在一个数据库事务内收敛单条过期预占。"""
        async with self._sessions.begin() as db:
            reservation = (
                await db.execute(
                    text(
                        """
                        SELECT reservation_id, owner_hasn_id, object_id, idempotency_key,
                               reserved_bytes, status, expires_time
                        FROM hasn_storage_reservations
                        WHERE reservation_id = :reservation_id
                        FOR UPDATE
                        """
                    ),
                    {'reservation_id': reservation_id},
                )
            ).mappings().one_or_none()
            if (
                reservation is None
                or str(reservation['status']) != 'reserved'
                or reservation['expires_time'] > timezone.now()
            ):
                return 'skipped'

            owner_hasn_id = str(reservation['owner_hasn_id'])
            account = (
                await db.execute(
                    text(
                        """
                        SELECT owner_hasn_id, quota_bytes, used_bytes, reserved_bytes
                        FROM hasn_storage_accounts
                        WHERE owner_hasn_id = :owner
                        FOR UPDATE
                        """
                    ),
                    {'owner': owner_hasn_id},
                )
            ).mappings().one_or_none()
            if account is None:
                raise errors.ServerError(msg='STORAGE_ACCOUNT_NOT_READY')
            reserved_bytes = int(reservation['reserved_bytes'])
            if int(account['reserved_bytes']) < reserved_bytes:
                raise errors.ServerError(msg='STORAGE_RESERVATION_COUNTER_INVALID')

            asset = (
                await db.execute(
                    text(
                        """
                        SELECT a.asset_id
                        FROM hasn_assets AS a
                        JOIN hasn_storage_objects AS o ON o.object_id = a.object_id
                        WHERE a.owner_hasn_id = :owner
                          AND a.upload_idempotency_key = :idempotency_key
                          AND a.object_id = :object_id
                          AND a.lifecycle_status <> 'deleted'
                          AND o.state IN ('active', 'deleting')
                        ORDER BY a.id
                        LIMIT 1
                        FOR UPDATE OF a, o
                        """
                    ),
                    {
                        'owner': owner_hasn_id,
                        'idempotency_key': str(reservation['idempotency_key']),
                        'object_id': str(reservation['object_id']),
                    },
                )
            ).mappings().one_or_none()
            now = timezone.now()
            outcome = 'completed' if asset is not None else 'expired'

            if asset is None:
                orphan = (
                    await db.execute(
                        text(
                            """
                            SELECT object_id, storage_id, object_key, state, ref_count
                            FROM hasn_storage_objects
                            WHERE object_id = :object_id
                              AND owner_hasn_id = :owner
                            FOR UPDATE
                            """
                        ),
                        {
                            'object_id': str(reservation['object_id']),
                            'owner': owner_hasn_id,
                        },
                    )
                ).mappings().one_or_none()
                if (
                    orphan is not None
                    and str(orphan['state']) in {'pending', 'active'}
                    and int(orphan['ref_count']) == 0
                ):
                    await db.execute(
                        text(
                            """
                            UPDATE hasn_storage_objects
                            SET state = 'deleting', updated_time = :now
                            WHERE object_id = :object_id
                            """
                        ),
                        {'object_id': str(orphan['object_id']), 'now': now},
                    )
                    await db.execute(
                        text(
                            """
                            INSERT INTO hasn_storage_jobs
                                (job_id, owner_hasn_id, job_type, status, cursor,
                                 total_items, processed_items, failed_items, payload,
                                 result, attempt_count, next_attempt_time,
                                 created_time, updated_time)
                            VALUES
                                (:job_id, :owner, 'object_purge', 'pending', '{}'::jsonb,
                                 1, 0, 0,
                                 jsonb_build_object(
                                     'object_id', CAST(:object_id AS text),
                                     'storage_id', CAST(:storage_id AS bigint),
                                     'object_key', CAST(:object_key AS text)
                                 ),
                                 '{}'::jsonb, 0, :now, :now, :now)
                            """
                        ),
                        {
                            'job_id': f'job_{uuid4().hex}',
                            'owner': owner_hasn_id,
                            'object_id': str(orphan['object_id']),
                            'storage_id': int(orphan['storage_id']),
                            'object_key': str(orphan['object_key']),
                            'now': now,
                        },
                    )

            authoritative_used = int(
                (
                    await db.execute(
                        text(
                            """
                            SELECT COALESCE(SUM(size_bytes), 0)
                            FROM hasn_storage_objects
                            WHERE owner_hasn_id = :owner
                              AND billable_to_owner
                              AND state IN ('pending', 'active', 'deleting')
                            """
                        ),
                        {'owner': owner_hasn_id},
                    )
                ).scalar_one()
            )
            remaining_reserved = int(account['reserved_bytes']) - reserved_bytes
            account_state = (
                'active'
                if authoritative_used + remaining_reserved <= int(account['quota_bytes'])
                else 'over_quota'
            )
            await db.execute(
                text(
                    """
                    UPDATE hasn_storage_accounts
                    SET used_bytes = :used_bytes,
                        reserved_bytes = :reserved_bytes,
                        state = :state,
                        updated_time = :now
                    WHERE owner_hasn_id = :owner
                    """
                ),
                {
                    'owner': owner_hasn_id,
                    'used_bytes': authoritative_used,
                    'reserved_bytes': remaining_reserved,
                    'state': account_state,
                    'now': now,
                },
            )
            await db.execute(
                text(
                    """
                    UPDATE hasn_storage_reservations
                    SET status = :status,
                        result_asset_id = :asset_id,
                        updated_time = :now
                    WHERE reservation_id = :reservation_id
                      AND status = 'reserved'
                    """
                ),
                {
                    'status': 'committed' if asset is not None else 'expired',
                    'asset_id': str(asset['asset_id']) if asset is not None else None,
                    'now': now,
                    'reservation_id': reservation_id,
                },
            )
            return outcome

    async def sweep_unbound_assets(
        self,
        *,
        limit: int = 500,
        retention_days: int | None = None,
        categories: list[str] | None = None,
        owner_hasn_id: str | None = None,
    ) -> dict[str, int]:
        """把超过保留期且无活动引用的业务附件移入垃圾箱并通知主人。"""
        if limit <= 0 or limit > 5000:
            raise errors.RequestError(msg='STORAGE_UNBOUND_SWEEP_LIMIT_INVALID')
        if retention_days is None:
            raw_days = os.getenv('STORAGE_UNBOUND_RETENTION_DAYS', '30')
            try:
                retention_days = int(raw_days)
            except ValueError as exc:
                raise errors.ServerError(msg='STORAGE_UNBOUND_RETENTION_INVALID') from exc
        if retention_days <= 0:
            raise errors.ServerError(msg='STORAGE_UNBOUND_RETENTION_INVALID')
        effective_categories = sorted(
            set(categories or ['dm_attachment', 'private_doc', 'published_artifact'])
        )
        cutoff = timezone.now() - timedelta(days=retention_days)
        async with self._sessions() as db:
            asset_ids = list(
                (
                    await db.execute(
                        text(
                            """
                            SELECT a.asset_id
                            FROM hasn_assets AS a
                            JOIN hasn_storage_entries AS e ON e.asset_id = a.asset_id
                            WHERE a.lifecycle_status = 'active'
                              AND a.category = ANY(CAST(:categories AS varchar[]))
                              AND a.created_time <= :cutoff
                              AND (
                                  CAST(:owner AS varchar) IS NULL
                                  OR a.owner_hasn_id = CAST(:owner AS varchar)
                              )
                              AND e.system_category <> 'my_uploads'
                              AND NOT EXISTS (
                                  SELECT 1
                                  FROM hasn_asset_bindings AS b
                                  WHERE b.owner_hasn_id = a.owner_hasn_id
                                    AND b.asset_id = a.asset_id
                                    AND b.status = 'active'
                              )
                            ORDER BY a.created_time, a.id
                            LIMIT :limit
                            """
                        ),
                        {
                            'categories': effective_categories,
                            'cutoff': cutoff,
                            'owner': owner_hasn_id,
                            'limit': limit,
                        },
                    )
                ).scalars()
            )

        trashed = 0
        # 逐主人汇总本轮被清理的文件名，扫描结束后每人只发一条聚合通知（见 _notify_unbound_trashed）。
        trashed_by_owner: dict[str, list[str]] = {}
        for asset_id in asset_ids:
            outcome = await self._trash_unbound_asset(str(asset_id), cutoff=cutoff)
            if outcome is None:
                continue
            trashed += 1
            owner_hasn_id, display_name = outcome
            trashed_by_owner.setdefault(owner_hasn_id, []).append(display_name)
        for owner, names in trashed_by_owner.items():
            await self._notify_unbound_trashed(owner_hasn_id=owner, names=names)
        return {'checked': len(asset_ids), 'trashed': trashed}

    async def _trash_unbound_asset(self, asset_id: str, *, cutoff: Any) -> tuple[str, str] | None:
        """把单个未绑定资产移入垃圾箱；命中时返回 (主人, 展示名)，未命中返回 None。

        只负责权威状态变更，不发通知——通知按批次聚合，避免一次扫描清理 N 个文件就发 N 条。
        """
        async with self._sessions.begin() as db:
            asset = (
                await db.execute(
                    text(
                        """
                        SELECT a.asset_id, a.owner_hasn_id, a.original_name
                        FROM hasn_assets AS a
                        JOIN hasn_storage_entries AS e ON e.asset_id = a.asset_id
                        WHERE a.asset_id = :asset_id
                          AND a.lifecycle_status = 'active'
                          AND a.created_time <= :cutoff
                          AND e.system_category <> 'my_uploads'
                          AND NOT EXISTS (
                              SELECT 1
                              FROM hasn_asset_bindings AS b
                              WHERE b.owner_hasn_id = a.owner_hasn_id
                                AND b.asset_id = a.asset_id
                                AND b.status = 'active'
                          )
                        FOR UPDATE OF a
                        """
                    ),
                    {'asset_id': asset_id, 'cutoff': cutoff},
                )
            ).mappings().one_or_none()
            if asset is None:
                return None
            now = timezone.now()
            await db.execute(
                text(
                    """
                    UPDATE hasn_assets
                    SET lifecycle_status = 'trashed',
                        trashed_time = :now,
                        version = version + 1,
                        updated_time = :now
                    WHERE asset_id = :asset_id
                    """
                ),
                {'asset_id': asset_id, 'now': now},
            )
            return str(asset['owner_hasn_id']), str(asset['original_name'] or asset_id)

    async def _notify_unbound_trashed(self, *, owner_hasn_id: str, names: list[str]) -> None:
        """本轮清理结束后给主人发一条聚合通知（存储维护属于告知，不该刷屏）。

        承载只留通知中心：显式关掉卡片消息/toast/系统推送，避免同一件事在消息列表再开一个
        服务号会话、又弹一次系统通知（主人反馈「只需要有一个」）。通知发送失败不回滚已完成的
        垃圾箱状态变更——资产状态是权威，告知是 best-effort，下轮扫描不会重复处理已 trashed 的行。
        """
        from backend.app.notification.service.notification_service import NotificationService

        if not names:
            return
        quoted = '、'.join(f'“{name}”' for name in names[:3])
        summary = quoted if len(names) <= 3 else f'{quoted}等 {len(names)} 个文件'
        # dedupe/group 键按天取：同一天重复扫描收敛成一条，跨天的清理各自成条不互相覆盖。
        sweep_day = timezone.now().strftime('%Y-%m-%d')
        dedupe_key = f'storage_unbound_asset_trashed:{owner_hasn_id}:{sweep_day}'
        try:
            async with self._sessions.begin() as db:
                await NotificationService.emit(
                    db,
                    recipient_id=owner_hasn_id,
                    source={'kind': 'system', 'id': 'owner_storage', 'display_name': '云存储'},
                    category='system',
                    type='storage_unbound_asset_trashed',
                    title=f'已清理 {len(names)} 个未使用的云端附件',
                    body=f'{summary}超过保留期且未被任何内容使用，已移入垃圾箱。'
                    '垃圾箱中的文件仍占用存储空间。',
                    payload={
                        'trashed_count': len(names),
                        'trashed_names': names[:20],
                        # 跳转键必须是 `link`：卡片投影 build_card_body 只认它（旧写法 payload
                        # `primary_action` 既不产出卡片按钮也不产出通知 link，一直是死字段）。
                        'link': 'hasn://storage/trash',
                    },
                    priority='normal',
                    dedupe_key=dedupe_key,
                    group_key=dedupe_key,
                    delivery_hint={
                        'channels': {'card_message': False, 'toast': False, 'push': False}
                    },
                )
        except Exception as exc:  # noqa: BLE001 - 告知失败不该拖垮已完成的清理
            log.warning(
                f'云存储无引用清理通知发送失败: owner={owner_hasn_id} count={len(names)} '
                f'{type(exc).__name__}: {exc!s}'
            )

    async def sweep_expired_exports(
        self,
        *,
        owner_hasn_id: str | None = None,
        limit: int = 100,
    ) -> dict[str, int]:
        """删除到期导出制品，并从作业结果移除已失效的物理位置。"""
        if limit <= 0 or limit > 1000:
            raise errors.RequestError(msg='STORAGE_EXPORT_SWEEP_LIMIT_INVALID')
        async with self._sessions() as db:
            rows = (
                await db.execute(
                    text(
                        """
                        SELECT job_id, owner_hasn_id,
                               (result ->> 'storage_id')::bigint AS storage_id,
                               result ->> 'object_key' AS object_key
                        FROM hasn_storage_jobs
                        WHERE job_type = 'storage_export'
                          AND status = 'succeeded'
                          AND expires_time <= :now
                          AND result ? 'storage_id'
                          AND result ? 'object_key'
                          AND (
                              CAST(:owner AS varchar) IS NULL
                              OR owner_hasn_id = CAST(:owner AS varchar)
                          )
                        ORDER BY expires_time, id
                        LIMIT :limit
                        """
                    ),
                    {
                        'owner': owner_hasn_id,
                        'now': timezone.now(),
                        'limit': limit,
                    },
                )
            ).mappings().all()
            storages = {
                int(row['storage_id']): copy.copy(
                    await StorageService.get_storage(db, int(row['storage_id']))
                )
                for row in rows
            }

        purged = 0
        for row in rows:
            storage = storages[int(row['storage_id'])]
            try:
                await StorageService.delete_on_storage(
                    storage,
                    object_key=str(row['object_key']),
                )
                if await self._finish_expired_export(
                    job_id=str(row['job_id']),
                    storage_id=int(row['storage_id']),
                    object_key=str(row['object_key']),
                ):
                    purged += 1
            except Exception as exc:
                log.warning(
                    f'用户云存储导出过期制品清理失败，将在后续批次重试: '
                    f'job_id={row["job_id"]}, error={type(exc).__name__}: {exc!r}'
                )
        return {'checked': len(rows), 'purged': purged}

    async def _finish_expired_export(
        self,
        *,
        job_id: str,
        storage_id: int,
        object_key: str,
    ) -> bool:
        """仅在作业仍指向刚删除的位置且仍已过期时提交清理结果。"""
        now = timezone.now()
        async with self._sessions.begin() as db:
            result = (
                await db.execute(
                    text(
                        """
                        UPDATE hasn_storage_jobs
                        SET result = (
                                result - 'storage_id' - 'object_key'
                            ) || jsonb_build_object(
                                'expired', TRUE,
                                'purged_time', CAST(:now AS timestamptz)
                            ),
                            updated_time = :now
                        WHERE job_id = :job_id
                          AND job_type = 'storage_export'
                          AND status = 'succeeded'
                          AND expires_time <= :now
                          AND (result ->> 'storage_id')::bigint = :storage_id
                          AND result ->> 'object_key' = :object_key
                        RETURNING job_id
                        """
                    ),
                    {
                        'job_id': job_id,
                        'storage_id': storage_id,
                        'object_key': object_key,
                        'now': now,
                    },
                )
            ).scalar_one_or_none()
            return result is not None

    async def sweep_migration_sources(
        self,
        *,
        owner_hasn_id: str | None = None,
        limit: int = 100,
    ) -> dict[str, int]:
        """观察期结束后安全清理迁移源对象；跨 Owner 共享位置只标记保留。"""
        if limit <= 0 or limit > 1000:
            raise errors.RequestError(msg='STORAGE_MIGRATION_SWEEP_LIMIT_INVALID')
        async with self._sessions() as db:
            rows = (
                await db.execute(
                    text(
                        """
                        SELECT i.item_id, i.job_id, i.object_id,
                               i.source_storage_id, i.source_object_key,
                               i.source_key_layout, i.target_storage_id,
                               i.target_object_key, i.source_cleanup_status,
                               j.owner_hasn_id
                        FROM hasn_storage_migration_items AS i
                        JOIN hasn_storage_jobs AS j ON j.job_id = i.job_id
                        JOIN hasn_storage_objects AS o ON o.object_id = i.object_id
                        WHERE j.job_type = 'storage_migration'
                          AND j.status = 'succeeded'
                          AND (j.result ->> 'observation_until')::timestamptz <= :now
                          AND i.verify_status = 'switched'
                          AND i.source_cleanup_status IN ('retained', 'deleting', 'failed')
                          AND o.owner_hasn_id = j.owner_hasn_id
                          AND o.storage_id = i.target_storage_id
                          AND o.object_key = i.target_object_key
                          AND o.state = 'active'
                          AND (
                              CAST(:owner AS varchar) IS NULL
                              OR j.owner_hasn_id = CAST(:owner AS varchar)
                          )
                        ORDER BY i.id
                        LIMIT :limit
                        """
                    ),
                    {
                        'owner': owner_hasn_id,
                        'now': timezone.now(),
                        'limit': limit,
                    },
                )
            ).mappings().all()
            storages = {
                int(row['source_storage_id']): copy.copy(
                    await StorageService.get_storage(db, int(row['source_storage_id']))
                )
                for row in rows
            }

        deleted = 0
        shared = 0
        for row in rows:
            claim = await self._claim_migration_source_cleanup(dict(row))
            if claim == 'shared':
                shared += 1
                continue
            if claim != 'delete':
                continue
            try:
                await StorageService.delete_on_storage(
                    storages[int(row['source_storage_id'])],
                    object_key=str(row['source_object_key']),
                )
                if await self._finish_migration_source_cleanup(dict(row)):
                    deleted += 1
            except Exception as exc:
                await self._fail_migration_source_cleanup(
                    item_id=str(row['item_id']),
                    exc=exc,
                )
                log.warning(
                    f'用户云存储迁移源对象清理失败，将在后续批次重试: '
                    f'job_id={row["job_id"]}, item_id={row["item_id"]}, '
                    f'error={type(exc).__name__}: {exc!r}'
                )
        return {'checked': len(rows), 'deleted': deleted, 'shared': shared}

    async def _claim_migration_source_cleanup(self, item: dict[str, Any]) -> str:
        """领取一条源清理；共享位置在数据库事务内确定性标记为保留。"""
        async with self._sessions.begin() as db:
            current = (
                await db.execute(
                    text(
                        """
                        SELECT source_cleanup_status
                        FROM hasn_storage_migration_items
                        WHERE item_id = :item_id
                        FOR UPDATE
                        """
                    ),
                    {'item_id': str(item['item_id'])},
                )
            ).scalar_one_or_none()
            if current not in {'retained', 'deleting', 'failed'}:
                return 'skip'
            shared_count = int(
                (
                    await db.execute(
                        text(
                            """
                            SELECT COUNT(*)
                            FROM hasn_storage_objects
                            WHERE storage_id = :storage_id
                              AND object_key = :object_key
                              AND object_id <> :object_id
                              AND state IN ('pending', 'active', 'deleting')
                            """
                        ),
                        {
                            'storage_id': int(item['source_storage_id']),
                            'object_key': str(item['source_object_key']),
                            'object_id': str(item['object_id']),
                        },
                    )
                ).scalar_one()
            )
            now = timezone.now()
            if shared_count > 0:
                await db.execute(
                    text(
                        """
                        UPDATE hasn_storage_migration_items
                        SET source_cleanup_status = 'shared',
                            error_code = NULL,
                            updated_time = :now
                        WHERE item_id = :item_id
                        """
                    ),
                    {'item_id': str(item['item_id']), 'now': now},
                )
                await self._refresh_migration_source_cleanup_result(
                    db,
                    job_id=str(item['job_id']),
                    now=now,
                )
                return 'shared'
            await db.execute(
                text(
                    """
                    UPDATE hasn_storage_migration_items
                    SET source_cleanup_status = 'deleting',
                        error_code = NULL,
                        updated_time = :now
                    WHERE item_id = :item_id
                    """
                ),
                {'item_id': str(item['item_id']), 'now': now},
            )
            return 'delete'

    async def _finish_migration_source_cleanup(self, item: dict[str, Any]) -> bool:
        now = timezone.now()
        async with self._sessions.begin() as db:
            updated = (
                await db.execute(
                    text(
                        """
                        UPDATE hasn_storage_migration_items
                        SET source_cleanup_status = 'deleted',
                            source_deleted_time = :now,
                            error_code = NULL,
                            updated_time = :now
                        WHERE item_id = :item_id
                          AND source_cleanup_status = 'deleting'
                        RETURNING item_id
                        """
                    ),
                    {'item_id': str(item['item_id']), 'now': now},
                )
            ).scalar_one_or_none()
            if updated is None:
                return False
            await self._refresh_migration_source_cleanup_result(
                db,
                job_id=str(item['job_id']),
                now=now,
            )
            return True

    async def _fail_migration_source_cleanup(
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
                    SET source_cleanup_status = 'failed',
                        error_code = :error_code,
                        updated_time = :now
                    WHERE item_id = :item_id
                      AND source_cleanup_status = 'deleting'
                    """
                ),
                {
                    'item_id': item_id,
                    'error_code': str(error_code)[:64],
                    'now': timezone.now(),
                },
            )

    @staticmethod
    async def _refresh_migration_source_cleanup_result(
        db: AsyncSession,
        *,
        job_id: str,
        now: Any,
    ) -> None:
        summary = (
            await db.execute(
                text(
                    """
                    SELECT COUNT(*) FILTER (
                               WHERE source_cleanup_status = 'deleted'
                           ) AS deleted_count,
                           COUNT(*) FILTER (
                               WHERE source_cleanup_status = 'shared'
                           ) AS shared_count,
                           COUNT(*) FILTER (
                               WHERE source_cleanup_status NOT IN ('deleted', 'shared')
                           ) AS remaining_count
                    FROM hasn_storage_migration_items
                    WHERE job_id = :job_id
                    """
                ),
                {'job_id': job_id},
            )
        ).mappings().one()
        remaining = int(summary['remaining_count'])
        status = (
            'deleted'
            if remaining == 0 and int(summary['shared_count']) == 0
            else 'shared_retained'
            if remaining == 0
            else 'cleaning'
        )
        await db.execute(
            text(
                """
                UPDATE hasn_storage_jobs
                SET result = result || jsonb_build_object(
                        'source_cleanup_status', CAST(:status AS text),
                        'source_deleted_items', CAST(:deleted_count AS bigint),
                        'source_shared_items', CAST(:shared_count AS bigint)
                    ),
                    updated_time = :now
                WHERE job_id = :job_id
                """
            ),
            {
                'job_id': job_id,
                'status': status,
                'deleted_count': int(summary['deleted_count']),
                'shared_count': int(summary['shared_count']),
                'now': now,
            },
        )

    async def backfill_legacy_assets(
        self,
        *,
        owner_hasn_ids: list[str] | None = None,
        batch_size: int = 500,
        verify_objects: bool = False,
    ) -> dict[str, Any]:
        """把旧 `hasn_assets` 回填到两层模型，并初始化账户投影。"""
        if batch_size <= 0 or batch_size > 5000:
            raise errors.RequestError(msg='STORAGE_BACKFILL_BATCH_INVALID')
        owners_filter = sorted({owner for owner in owner_hasn_ids or [] if owner})
        assets_backfilled = 0
        objects_created = 0
        entries_created = 0
        touched_owners: set[str] = set(owners_filter)

        while True:
            async with self._sessions.begin() as db:
                await db.execute(
                    text("SELECT pg_advisory_xact_lock(hashtextextended('owner-storage-legacy-backfill', 0))")
                )
                rows = (
                    await db.execute(
                        text(
                            """
                            SELECT id, owner_hasn_id, storage_id, object_key
                            FROM hasn_assets
                            WHERE object_id IS NULL
                              AND (
                                  CAST(:filter_enabled AS boolean) = FALSE
                                  OR owner_hasn_id = ANY(CAST(:owners AS varchar[]))
                              )
                            ORDER BY id
                            FOR UPDATE SKIP LOCKED
                            LIMIT :batch_size
                            """
                        ),
                        {
                            'filter_enabled': bool(owners_filter),
                            'owners': owners_filter,
                            'batch_size': batch_size,
                        },
                    )
                ).mappings().all()
                if not rows:
                    break
                groups = {
                    (str(row['owner_hasn_id']), int(row['storage_id']), str(row['object_key']))
                    for row in rows
                }
                for owner_hasn_id, storage_id, object_key in sorted(groups):
                    touched_owners.add(owner_hasn_id)
                    result = await self._backfill_legacy_group(
                        db,
                        owner_hasn_id=owner_hasn_id,
                        storage_id=storage_id,
                        object_key=object_key,
                    )
                    assets_backfilled += result['assets_backfilled']
                    objects_created += result['objects_created']
                    entries_created += result['entries_created']

        if not owners_filter:
            async with self._sessions() as db:
                existing_owners = (
                    (
                        await db.execute(
                            text(
                                """
                                SELECT DISTINCT owner_hasn_id
                                FROM hasn_storage_objects
                                WHERE key_layout = 'legacy'
                                  AND owner_hasn_id IS NOT NULL
                                """
                            )
                        )
                    )
                    .scalars()
                    .all()
                )
            touched_owners.update(str(owner) for owner in existing_owners)

        verification = {
            'objects_checked': 0,
            'objects_verified': 0,
            'objects_missing': 0,
            'objects_merged': 0,
            'verification_failed': 0,
        }
        reconciliations: list[dict[str, Any]] = []
        unresolved_owner_hasn_ids: list[str] = []
        for owner_hasn_id in sorted(touched_owners):
            try:
                await self._owner_storage.usage(owner_hasn_id=owner_hasn_id)
            except errors.ServerError as exc:
                if exc.msg != 'STORAGE_OWNER_IDENTITY_NOT_READY':
                    raise
                unresolved_owner_hasn_ids.append(owner_hasn_id)
                log.warning(
                    f'用户云存储存量 Owner 身份不存在，跳过账户初始化: owner={owner_hasn_id}'
                )
                if verify_objects:
                    reconciled = await self.reconcile_owner(
                        owner_hasn_id=owner_hasn_id,
                        verify_objects=True,
                        repair_counters=False,
                    )
                    reconciliations.append(reconciled)
                    for key in verification:
                        verification[key] += int(reconciled[key])
                continue
            reconciled = await self.reconcile_owner(
                owner_hasn_id=owner_hasn_id,
                verify_objects=verify_objects,
                repair_counters=True,
            )
            reconciliations.append(reconciled)
            for key in verification:
                verification[key] += int(reconciled[key])

        shared_legacy_locations = await self._shared_legacy_location_count(
            owner_hasn_ids=owners_filter or None,
        )
        return {
            'assets_backfilled': assets_backfilled,
            'objects_created': objects_created,
            'entries_created': entries_created,
            'owners_reconciled': len(reconciliations),
            'owners_without_identity': len(unresolved_owner_hasn_ids),
            'unresolved_owner_hasn_ids': unresolved_owner_hasn_ids,
            'shared_legacy_locations': shared_legacy_locations,
            **verification,
        }

    async def _backfill_legacy_group(
        self,
        db: AsyncSession,
        *,
        owner_hasn_id: str,
        storage_id: int,
        object_key: str,
    ) -> dict[str, int]:
        group = (
            await db.execute(
                text(
                    """
                    SELECT COUNT(*) FILTER (WHERE lifecycle_status <> 'deleted') AS ref_count,
                           MAX(size_bytes) AS size_bytes,
                           MIN(created_time) AS created_time,
                           BOOL_AND(access = 'public') AS all_public
                    FROM hasn_assets
                    WHERE owner_hasn_id = :owner
                      AND storage_id = :storage_id
                      AND object_key = :object_key
                    """
                ),
                {
                    'owner': owner_hasn_id,
                    'storage_id': storage_id,
                    'object_key': object_key,
                },
            )
        ).mappings().one()
        object_id = _legacy_object_id(owner_hasn_id, storage_id, object_key)
        created = (
            await db.execute(
                text(
                    """
                    INSERT INTO hasn_storage_objects
                        (object_id, owner_hasn_id, storage_id, object_key, key_layout,
                         access, size_bytes, sha256, billable_to_owner, ref_count,
                         state, created_time, updated_time)
                    VALUES
                        (:object_id, :owner, :storage_id, :object_key, 'legacy',
                         :access, :size_bytes, NULL, TRUE, :ref_count,
                         'active', :created_time, :now)
                    ON CONFLICT (object_id) DO NOTHING
                    RETURNING object_id
                    """
                ),
                {
                    'object_id': object_id,
                    'owner': owner_hasn_id,
                    'storage_id': storage_id,
                    'object_key': object_key,
                    'access': 'public' if bool(group['all_public']) else 'private',
                    'size_bytes': int(group['size_bytes'] or 0),
                    'ref_count': int(group['ref_count'] or 0),
                    'created_time': group['created_time'] or timezone.now(),
                    'now': timezone.now(),
                },
            )
        ).scalar_one_or_none()
        category, source_app, _ = _legacy_category(object_key)
        updated_assets = await db.execute(
            text(
                """
                UPDATE hasn_assets
                SET object_id = :object_id,
                    category = COALESCE(category, :category),
                    original_name = COALESCE(original_name, :original_name),
                    source_app = COALESCE(source_app, :source_app),
                    lifecycle_status = COALESCE(lifecycle_status, 'active'),
                    version = GREATEST(version, 1),
                    updated_time = COALESCE(updated_time, :now)
                WHERE owner_hasn_id = :owner
                  AND storage_id = :storage_id
                  AND object_key = :object_key
                  AND object_id IS NULL
                RETURNING asset_id
                """
            ),
            {
                'object_id': object_id,
                'category': category,
                'original_name': _legacy_display_name(object_id, object_key),
                'source_app': source_app,
                'now': timezone.now(),
                'owner': owner_hasn_id,
                'storage_id': storage_id,
                'object_key': object_key,
            },
        )
        asset_ids = [str(asset_id) for asset_id in updated_assets.scalars().all()]
        entries_created = 0
        for asset_id in asset_ids:
            entries_created += await self._ensure_legacy_entry(
                db,
                owner_hasn_id=owner_hasn_id,
                asset_id=asset_id,
                object_key=object_key,
                category=category,
            )
        await db.execute(
            text(
                """
                UPDATE hasn_storage_objects AS o
                SET ref_count = (
                        SELECT COUNT(*)
                        FROM hasn_assets AS a
                        WHERE a.object_id = o.object_id
                          AND a.lifecycle_status <> 'deleted'
                    ),
                    updated_time = :now
                WHERE o.object_id = :object_id
                """
            ),
            {'object_id': object_id, 'now': timezone.now()},
        )
        return {
            'assets_backfilled': len(asset_ids),
            'objects_created': 1 if created is not None else 0,
            'entries_created': entries_created,
        }

    async def _ensure_legacy_entry(
        self,
        db: AsyncSession,
        *,
        owner_hasn_id: str,
        asset_id: str,
        object_key: str,
        category: str,
    ) -> int:
        existing = (
            await db.execute(
                text('SELECT 1 FROM hasn_storage_entries WHERE asset_id = :asset_id'),
                {'asset_id': asset_id},
            )
        ).scalar_one_or_none()
        if existing is not None:
            return 0
        await db.execute(
            text('SELECT pg_advisory_xact_lock(hashtextextended(:lock_key, 0))'),
            {'lock_key': f'owner-storage-dir-name:{owner_hasn_id}:root'},
        )
        base_name = _legacy_display_name(asset_id, object_key)
        display_name = base_name
        for sequence in range(1, 10001):
            if sequence > 1:
                display_name = suffixed_name(base_name, sequence)
            normalized_name = normalize_storage_name(display_name)
            conflict = (
                await db.execute(
                    text(
                        """
                        SELECT 1
                        FROM hasn_storage_entries
                        WHERE owner_hasn_id = :owner
                          AND parent_entry_id IS NULL
                          AND normalized_name = :normalized_name
                        """
                    ),
                    {'owner': owner_hasn_id, 'normalized_name': normalized_name},
                )
            ).scalar_one_or_none()
            if conflict is None:
                break
        else:
            raise errors.ServerError(msg='STORAGE_NAME_CONFLICT_EXHAUSTED')
        _, _, system_category = _legacy_category(object_key)
        inserted = await db.execute(
            text(
                """
                INSERT INTO hasn_storage_entries
                    (entry_id, owner_hasn_id, asset_id, parent_entry_id, entry_type,
                     display_name, normalized_name, system_category, version,
                     created_time, updated_time)
                VALUES
                    (:entry_id, :owner, :asset_id, NULL, 'file',
                     :display_name, :normalized_name, :system_category, 1, :now, :now)
                ON CONFLICT (asset_id) WHERE asset_id IS NOT NULL DO NOTHING
                RETURNING entry_id
                """
            ),
            {
                'entry_id': _legacy_entry_id(asset_id),
                'owner': owner_hasn_id,
                'asset_id': asset_id,
                'display_name': display_name,
                'normalized_name': normalized_name,
                'system_category': system_category,
                'now': timezone.now(),
            },
        )
        return 1 if inserted.scalar_one_or_none() is not None else 0

    async def reconcile_owner(
        self,
        *,
        owner_hasn_id: str,
        verify_objects: bool,
        repair_counters: bool,
    ) -> dict[str, Any]:
        """按数据库权威游标核验对象，并并发安全地修复计数器。"""
        report: dict[str, Any] = {
            'owner_hasn_id': owner_hasn_id,
            'objects_checked': 0,
            'objects_verified': 0,
            'objects_missing': 0,
            'objects_merged': 0,
            'verification_failed': 0,
            'ref_count_repairs': 0,
            'used_bytes_before': 0,
            'used_bytes_after': 0,
        }
        if verify_objects:
            async with self._sessions() as db:
                object_ids = list(
                    (
                        await db.execute(
                            text(
                                """
                                SELECT object_id
                                FROM hasn_storage_objects
                                WHERE owner_hasn_id = :owner
                                  AND state IN ('pending', 'active', 'deleting')
                                ORDER BY id
                                """
                            ),
                            {'owner': owner_hasn_id},
                        )
                    )
                    .scalars()
                    .all()
                )
            for object_id in object_ids:
                outcome = await self._verify_object(
                    owner_hasn_id=owner_hasn_id,
                    object_id=str(object_id),
                )
                report['objects_checked'] += 1
                report[outcome] += 1

        if repair_counters:
            repaired = await self._repair_owner_counters(owner_hasn_id=owner_hasn_id)
            report.update(repaired)
        return report

    async def _verify_object(self, *, owner_hasn_id: str, object_id: str) -> str:
        async with self._sessions() as db:
            row = (
                await db.execute(
                    text(
                        """
                        SELECT object_id, storage_id, object_key
                        FROM hasn_storage_objects
                        WHERE object_id = :object_id
                          AND owner_hasn_id = :owner
                          AND state IN ('pending', 'active', 'deleting')
                        """
                    ),
                    {'object_id': object_id, 'owner': owner_hasn_id},
                )
            ).mappings().one_or_none()
            if row is None:
                return 'objects_verified'
            storage = copy.copy(await StorageService.get_storage(db, int(row['storage_id'])))
            object_key = str(row['object_key'])
        try:
            stat = await StorageService.stat_on_storage(storage, object_key=object_key)
            digest, actual_size = await StorageService.sha256_on_storage(storage, object_key=object_key)
            if stat.size != actual_size:
                raise errors.ServerError(msg='STORAGE_OBJECT_SIZE_MISMATCH')
        except Exception as exc:
            if _is_missing_object_error(exc):
                async with self._sessions.begin() as db:
                    await db.execute(
                        text(
                            """
                            UPDATE hasn_storage_objects
                            SET state = 'missing', updated_time = :now
                            WHERE object_id = :object_id AND owner_hasn_id = :owner
                            """
                        ),
                        {'object_id': object_id, 'owner': owner_hasn_id, 'now': timezone.now()},
                    )
                log.error(
                    f'用户云存储对象永久缺失: owner={owner_hasn_id}, object_id={object_id}, '
                    f'error={type(exc).__name__}: {exc!r}'
                )
                return 'objects_missing'
            log.warning(
                f'用户云存储对象核验暂时失败: owner={owner_hasn_id}, object_id={object_id}, '
                f'error={type(exc).__name__}: {exc!r}'
            )
            return 'verification_failed'

        async with self._sessions.begin() as db:
            await db.execute(
                text('SELECT pg_advisory_xact_lock(hashtextextended(:lock_key, 0))'),
                {'lock_key': f'owner-storage-content:{owner_hasn_id}:{digest}'},
            )
            current = (
                await db.execute(
                    text(
                        """
                        SELECT object_id, storage_id, object_key, key_layout, state
                        FROM hasn_storage_objects
                        WHERE object_id = :object_id AND owner_hasn_id = :owner
                        FOR UPDATE
                        """
                    ),
                    {'object_id': object_id, 'owner': owner_hasn_id},
                )
            ).mappings().one_or_none()
            if current is None or str(current['state']) not in {'pending', 'active', 'deleting'}:
                return 'objects_verified'
            canonical = (
                await db.execute(
                    text(
                        """
                        SELECT object_id
                        FROM hasn_storage_objects
                        WHERE owner_hasn_id = :owner
                          AND sha256 = :sha256
                          AND object_id <> :object_id
                          AND billable_to_owner
                          AND state <> 'deleted'
                        ORDER BY id
                        FOR UPDATE
                        LIMIT 1
                        """
                    ),
                    {'owner': owner_hasn_id, 'sha256': digest, 'object_id': object_id},
                )
            ).scalar_one_or_none()
            if canonical is not None:
                await self._merge_duplicate_object(
                    db,
                    owner_hasn_id=owner_hasn_id,
                    object_id=object_id,
                    canonical_object_id=str(canonical),
                    current=current,
                )
                return 'objects_merged'
            await db.execute(
                text(
                    """
                    UPDATE hasn_storage_objects
                    SET size_bytes = :size_bytes,
                        sha256 = :sha256,
                        state = CASE WHEN state = 'pending' THEN 'active' ELSE state END,
                        updated_time = :now
                    WHERE object_id = :object_id AND owner_hasn_id = :owner
                    """
                ),
                {
                    'size_bytes': actual_size,
                    'sha256': digest,
                    'now': timezone.now(),
                    'object_id': object_id,
                    'owner': owner_hasn_id,
                },
            )
        return 'objects_verified'

    async def _merge_duplicate_object(
        self,
        db: AsyncSession,
        *,
        owner_hasn_id: str,
        object_id: str,
        canonical_object_id: str,
        current: Any,
    ) -> None:
        now = timezone.now()
        await db.execute(
            text(
                """
                UPDATE hasn_assets
                SET object_id = :canonical_object_id, updated_time = :now
                WHERE owner_hasn_id = :owner AND object_id = :object_id
                """
            ),
            {
                'canonical_object_id': canonical_object_id,
                'now': now,
                'owner': owner_hasn_id,
                'object_id': object_id,
            },
        )
        await db.execute(
            text(
                """
                UPDATE hasn_storage_objects AS o
                SET ref_count = (
                        SELECT COUNT(*)
                        FROM hasn_assets AS a
                        WHERE a.object_id = o.object_id
                          AND a.lifecycle_status <> 'deleted'
                    ),
                    updated_time = :now
                WHERE o.object_id = :canonical_object_id
                """
            ),
            {'canonical_object_id': canonical_object_id, 'now': now},
        )
        shared_location_count = (
            await db.execute(
                text(
                    """
                    SELECT COUNT(*)
                    FROM hasn_storage_objects
                    WHERE storage_id = :storage_id
                      AND object_key = :object_key
                      AND object_id <> :object_id
                      AND state IN ('pending', 'active', 'deleting')
                    """
                ),
                {
                    'storage_id': int(current['storage_id']),
                    'object_key': str(current['object_key']),
                    'object_id': object_id,
                },
            )
        ).scalar_one()
        if str(current['key_layout']) == 'legacy' and int(shared_location_count) > 0:
            await db.execute(
                text(
                    """
                    UPDATE hasn_storage_objects
                    SET ref_count = 0, state = 'deleted', updated_time = :now
                    WHERE object_id = :object_id
                    """
                ),
                {'object_id': object_id, 'now': now},
            )
            return
        await db.execute(
            text(
                """
                UPDATE hasn_storage_objects
                SET ref_count = 0, state = 'deleting', updated_time = :now
                WHERE object_id = :object_id
                """
            ),
            {'object_id': object_id, 'now': now},
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
                         'object_key', CAST(:object_key AS text)
                     ),
                     '{}'::jsonb, 0, :now, :now, :now)
                """
            ),
            {
                'job_id': f'job_{uuid4().hex}',
                'owner': owner_hasn_id,
                'object_id': object_id,
                'storage_id': int(current['storage_id']),
                'object_key': str(current['object_key']),
                'now': now,
            },
        )

    async def _repair_owner_counters(self, *, owner_hasn_id: str) -> dict[str, int]:
        async with self._sessions.begin() as db:
            account = (
                await db.execute(
                    text(
                        """
                        SELECT used_bytes, reserved_bytes, quota_bytes
                        FROM hasn_storage_accounts
                        WHERE owner_hasn_id = :owner
                        FOR UPDATE
                        """
                    ),
                    {'owner': owner_hasn_id},
                )
            ).mappings().one_or_none()
            if account is None:
                raise errors.ServerError(msg='STORAGE_ACCOUNT_NOT_READY')
            mismatches = int(
                (
                    await db.execute(
                        text(
                            """
                            SELECT COUNT(*)
                            FROM hasn_storage_objects AS o
                            WHERE o.owner_hasn_id = :owner
                              AND o.ref_count <> (
                                  SELECT COUNT(*)
                                  FROM hasn_assets AS a
                                  WHERE a.object_id = o.object_id
                                    AND a.lifecycle_status <> 'deleted'
                              )
                            """
                        ),
                        {'owner': owner_hasn_id},
                    )
                ).scalar_one()
            )
            await db.execute(
                text(
                    """
                    UPDATE hasn_storage_objects AS o
                    SET ref_count = (
                            SELECT COUNT(*)
                            FROM hasn_assets AS a
                            WHERE a.object_id = o.object_id
                              AND a.lifecycle_status <> 'deleted'
                        ),
                        updated_time = :now
                    WHERE o.owner_hasn_id = :owner
                      AND o.ref_count <> (
                          SELECT COUNT(*)
                          FROM hasn_assets AS a
                          WHERE a.object_id = o.object_id
                            AND a.lifecycle_status <> 'deleted'
                      )
                    """
                ),
                {'owner': owner_hasn_id, 'now': timezone.now()},
            )
            used_after = int(
                (
                    await db.execute(
                        text(
                            """
                            SELECT COALESCE(SUM(size_bytes), 0)
                            FROM hasn_storage_objects
                            WHERE owner_hasn_id = :owner
                              AND billable_to_owner
                              AND state IN ('pending', 'active', 'deleting')
                            """
                        ),
                        {'owner': owner_hasn_id},
                    )
                ).scalar_one()
            )
            used_before = int(account['used_bytes'])
            await db.execute(
                text(
                    """
                    UPDATE hasn_storage_accounts
                    SET used_bytes = :used_bytes,
                        state = CASE
                            WHEN :used_bytes + reserved_bytes <= quota_bytes
                            THEN 'active' ELSE 'over_quota'
                        END,
                        updated_time = :now
                    WHERE owner_hasn_id = :owner
                    """
                ),
                {
                    'used_bytes': used_after,
                    'now': timezone.now(),
                    'owner': owner_hasn_id,
                },
            )
            return {
                'ref_count_repairs': mismatches,
                'used_bytes_before': used_before,
                'used_bytes_after': used_after,
            }

    async def _shared_legacy_location_count(self, *, owner_hasn_ids: list[str] | None) -> int:
        async with self._sessions() as db:
            return int(
                (
                    await db.execute(
                        text(
                            """
                            SELECT COUNT(*)
                            FROM (
                                SELECT storage_id, object_key
                                FROM hasn_storage_objects
                                WHERE key_layout = 'legacy'
                                  AND state <> 'deleted'
                                  AND (
                                      CAST(:filter_enabled AS boolean) = FALSE
                                      OR owner_hasn_id = ANY(CAST(:owners AS varchar[]))
                                  )
                                GROUP BY storage_id, object_key
                                HAVING COUNT(DISTINCT owner_hasn_id) > 1
                            ) AS shared_locations
                            """
                        ),
                        {
                            'filter_enabled': bool(owner_hasn_ids),
                            'owners': owner_hasn_ids or [],
                        },
                    )
                ).scalar_one()
            )
