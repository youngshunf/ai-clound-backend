"""Agent 产物第一阶段的跨端契约测试。"""

from __future__ import annotations

import json

from pathlib import Path

import pytest

from backend.app.hasn.model.hasn_artifacts import HasnArtifacts
from backend.app.hasn.schema.artifact_contract import (
    ArtifactListItem,
    ArtifactMutation,
)
from backend.app.hasn.schema.hasn_artifacts import RecordArtifactParam


def _resource_mutation() -> dict[str, object]:
    """返回具有稳定云端资源身份的最小登记命令。"""
    return {
        'owner_hasn_id': 'owner_phase1',
        'agent_hasn_id': 'agent_phase1',
        'action': 'create',
        'source_kind': 'app_write',
        'resource_uri': 'hasn://deck/deck_phase1',
        'resource_kind': 'deck.presentation',
        'resource_app_id': 'deck',
        'dispatch_id': 'dispatch_phase1',
    }


def test_artifact_mutation_rejects_invalid_enum_without_silent_fallback() -> None:
    """非法动作或来源必须被拒绝，不能默默改写为 create。"""
    payload = _resource_mutation()
    payload['action'] = 'replace'

    with pytest.raises(ValueError):
        ArtifactMutation.model_validate(payload)


def test_artifact_mutation_requires_exactly_one_locator() -> None:
    """产物本体只能是正文、资产、资源或本地 locator 四者之一。"""
    payload = _resource_mutation()
    payload['body'] = '# 不应与资源 URI 同时存在'

    with pytest.raises(ValueError):
        ArtifactMutation.model_validate(payload)


def test_local_mutation_uses_opaque_locator_key_instead_of_absolute_path() -> None:
    """云端登记命令只接收不可逆 locator key，绝对路径必须在边界被拒绝。"""
    payload = {
        'owner_hasn_id': 'owner_phase1',
        'agent_hasn_id': 'agent_phase1',
        'action': 'update',
        'source_kind': 'runtime_file',
        'artifact_kind': 'file',
        'local_locator_key': 'hmac:9d1ea79b',
        'node_id': 'node_phase1',
        'local_entry_kind': 'file',
        'dispatch_id': 'dispatch_phase1',
    }

    mutation = ArtifactMutation.model_validate(payload)
    assert mutation.local_locator_key == 'hmac:9d1ea79b'
    assert 'local_path' not in mutation.model_dump()


def test_legacy_runtime_path_is_hashed_before_artifact_mutation_boundary() -> None:
    """冻结 runtime sink 的旧入参只能在请求边界即时转为不可逆定位键。"""
    params = RecordArtifactParam.model_validate(
        {
            'kind': 'file',
            'local_path': '/runtime/workspace/report.md',
            'node_id': 'node_phase1',
            'source_kind': 'runtime_file',
            'action': 'update',
        }
    )

    assert params.local_locator_key is not None
    assert params.local_locator_key.startswith('legacy-path-v1:')
    assert params.local_entry_kind == 'file'
    assert 'local_path' not in params.model_dump()


def test_artifact_list_item_serialization_never_contains_local_absolute_path() -> None:
    """唯一读模型仅暴露设备和条目类型，序列化边界没有本地路径。"""
    item = ArtifactListItem.model_validate(
        {
            'artifact_id': 'art_phase1',
            'artifact_kind': 'file',
            'resource_kind': None,
            'resource_app_id': None,
            'title': '周报.md',
            'summary': None,
            'body_preview': None,
            'asset_uri': None,
            'preview_url': None,
            'download_url': None,
            'resource_uri': None,
            'local_entry': {
                'node_id': 'node_phase1',
                'entry_kind': 'file',
                'device_name': '测试设备',
            },
            'availability': 'local_other_device',
            'allowed_actions': [],
            'sync_state': 'synced',
            'latest_contribution': {
                'contribution_id': 'con_phase1',
                'agent_hasn_id': 'agent_phase1',
                'work_session_id': None,
                'project_id': None,
                'action': 'update',
                'source_kind': 'runtime_file',
                'source_tool': 'write_file',
                'source_app_id': None,
                'source_link': None,
                'occurred_time': '2026-07-22T00:00:00Z',
            },
            'agent_identity': None,
            'project_relation': None,
            'created_time': '2026-07-22T00:00:00Z',
            'updated_time': '2026-07-22T00:00:00Z',
        }
    )

    serialized = json.loads(item.model_dump_json())
    assert 'local_path' not in serialized
    assert serialized['local_entry']['node_id'] == 'node_phase1'


def test_current_state_migration_supports_databases_without_legacy_local_path() -> None:
    """当前态迁移必须兼容从未落过 local_path 的初始产物表。"""
    columns = HasnArtifacts.__table__.columns
    assert 'local_path' not in columns

    migration = (
        Path(__file__).resolve().parents[2]
        / 'sql/hasn/migrations/2026-07-22-artifact-current-state-and-contributions.sql'
    ).read_text(encoding='utf-8')
    compatibility_statement = 'ADD COLUMN IF NOT EXISTS "local_path" VARCHAR(512)'
    assert compatibility_statement in migration
    assert migration.index(compatibility_statement) < migration.index('WHEN "local_locator_key" IS NULL')
