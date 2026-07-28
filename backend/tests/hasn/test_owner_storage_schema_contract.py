"""用户私有存储数据模型契约测试。"""

from __future__ import annotations

from typing import Any

from backend.app.hasn.model.hasn_asset_bindings import HasnAssetBindings
from backend.app.hasn.model.hasn_assets import HasnAssets
from backend.app.hasn.model.hasn_storage_accounts import HasnStorageAccounts
from backend.app.hasn.model.hasn_storage_entries import HasnStorageEntries
from backend.app.hasn.model.hasn_storage_export_items import HasnStorageExportItems
from backend.app.hasn.model.hasn_storage_jobs import HasnStorageJobs
from backend.app.hasn.model.hasn_storage_migration_items import HasnStorageMigrationItems
from backend.app.hasn.model.hasn_storage_objects import HasnStorageObjects
from backend.app.hasn.model.hasn_storage_reservations import HasnStorageReservations
from backend.app.task.tasks.beat import LOCAL_BEAT_SCHEDULE


def _columns(model: Any) -> set[str]:
    return set(model.__table__.columns.keys())


def test_legacy_backfill_has_executable_periodic_entrypoint() -> None:
    scheduled_tasks = {entry['task'] for entry in LOCAL_BEAT_SCHEDULE.values()}

    assert 'owner_storage_legacy_backfill' in scheduled_tasks


def test_asset_layer_only_adds_logical_storage_metadata() -> None:
    assert {
        'object_id',
        'category',
        'original_name',
        'source_app',
        'upload_idempotency_key',
        'derived_from_asset_id',
        'lifecycle_status',
        'trashed_time',
        'deleted_time',
        'version',
    } <= _columns(HasnAssets)


def test_physical_object_and_quota_tables_have_authoritative_fields() -> None:
    assert {
        'object_id',
        'owner_hasn_id',
        'storage_id',
        'object_key',
        'key_layout',
        'access',
        'size_bytes',
        'sha256',
        'billable_to_owner',
        'ref_count',
        'state',
    } <= _columns(HasnStorageObjects)
    assert {
        'owner_hasn_id',
        'quota_bytes',
        'used_bytes',
        'reserved_bytes',
        'quota_source',
        'quota_version',
        'source_subscription_id',
        'quota_valid_until',
        'state',
    } <= _columns(HasnStorageAccounts)
    assert {
        'reservation_id',
        'owner_hasn_id',
        'object_id',
        'result_asset_id',
        'idempotency_key',
        'request_fingerprint',
        'reserved_bytes',
        'status',
        'expires_time',
    } <= _columns(HasnStorageReservations)


def test_directory_binding_and_job_tables_cover_second_batch() -> None:
    assert {
        'entry_id',
        'owner_hasn_id',
        'asset_id',
        'parent_entry_id',
        'entry_type',
        'display_name',
        'normalized_name',
        'system_category',
        'version',
    } <= _columns(HasnStorageEntries)
    assert {
        'binding_id',
        'owner_hasn_id',
        'asset_id',
        'resource_uri',
        'role',
        'status',
    } <= _columns(HasnAssetBindings)
    assert {
        'job_id',
        'owner_hasn_id',
        'job_type',
        'status',
        'cursor',
        'total_items',
        'processed_items',
        'failed_items',
        'error_code',
        'payload',
        'result',
        'expires_time',
    } <= _columns(HasnStorageJobs)
    assert {
        'item_id',
        'job_id',
        'object_id',
        'source_storage_id',
        'source_object_key',
        'target_storage_id',
        'target_object_key',
        'source_size_bytes',
        'source_sha256',
        'verify_status',
        'error_code',
    } <= _columns(HasnStorageMigrationItems)
    assert {
        'item_id',
        'job_id',
        'owner_hasn_id',
        'asset_id',
        'logical_path',
        'bindings',
        'object_id',
        'storage_id',
        'object_key',
        'size_bytes',
        'sha256',
        'verify_status',
        'error_code',
    } <= _columns(HasnStorageExportItems)
