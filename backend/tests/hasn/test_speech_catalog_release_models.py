"""语音原子 release 持久化模型约束测试。"""

from pathlib import Path

from backend.app.hasn.model.hasn_speech_catalog import HasnSpeechCatalog
from backend.app.hasn.model.hasn_speech_catalog_release import HasnSpeechCatalogRelease
from backend.app.hasn.model.hasn_speech_catalog_release_package import (
    HasnSpeechCatalogReleasePackage,
)
from backend.app.hasn.model.hasn_speech_package import HasnSpeechPackage

_SQL_ROOT = Path(__file__).parents[2] / 'sql' / 'hasn'


def _sql(name: str) -> str:
    """读取 codegen 的权威建表 SQL。"""
    return (_SQL_ROOT / name).read_text(encoding='utf-8')


def test_speech_package_is_content_addressed_and_immutable_by_schema() -> None:
    columns = HasnSpeechPackage.__table__.columns
    assert {'sha256', 'storage_id', 'object_key', 'size', 'content_type'} <= set(columns.keys())
    sql = _sql('hasn_speech_package.sql')
    assert 'CONSTRAINT "uq_speech_package_sha256" UNIQUE ("sha256")' in sql
    assert 'CONSTRAINT "uq_speech_package_object_key" UNIQUE ("object_key")' in sql
    assert 'CONSTRAINT "ck_speech_package_size" CHECK ("size" > 0)' in sql
    assert columns.sha256.nullable is False
    assert columns.object_key.nullable is False
    assert columns.size.nullable is False


def test_catalog_release_keeps_sequence_revision_and_verbatim_document() -> None:
    columns = HasnSpeechCatalogRelease.__table__.columns
    assert {
        'revision',
        'release_sequence',
        'key_id',
        'catalog_version',
        'expires_at',
        'catalog_json',
        'model_summary',
        'published_by',
    } <= set(columns.keys())
    sql = _sql('hasn_speech_catalog_release.sql')
    assert 'CONSTRAINT "uq_speech_catalog_release_revision" UNIQUE ("revision")' in sql
    assert 'CONSTRAINT "uq_speech_catalog_release_sequence" UNIQUE ("release_sequence")' in sql
    assert columns.catalog_json.nullable is False


def test_release_package_mapping_freezes_signed_platform_and_license_metadata() -> None:
    columns = HasnSpeechCatalogReleasePackage.__table__.columns
    assert {
        'release_id',
        'package_id',
        'model_id',
        'model_version',
        'os',
        'arch',
        'acceleration',
        'installed_size',
        'license_name',
        'license_url',
        'source_url',
    } <= set(columns.keys())
    sql = _sql('hasn_speech_catalog_release_package.sql')
    assert 'CONSTRAINT "uq_speech_release_package_platform"' in sql
    assert 'REFERENCES "public"."hasn_speech_catalog_release" ("id") ON DELETE CASCADE' in sql
    assert 'REFERENCES "public"."hasn_speech_package" ("id") ON DELETE RESTRICT' in sql


def test_current_catalog_head_points_to_authoritative_release_sequence() -> None:
    columns = HasnSpeechCatalog.__table__.columns
    assert {'current_release_id', 'release_sequence', 'key_id'} <= set(columns.keys())
    migration = _sql('migrations/2026-07-19-speech-catalog-atomic-release.sql')
    assert 'ADD COLUMN IF NOT EXISTS "current_release_id"' in migration
    assert 'ADD COLUMN IF NOT EXISTS "release_sequence"' in migration
    assert 'ADD COLUMN IF NOT EXISTS "key_id"' in migration
