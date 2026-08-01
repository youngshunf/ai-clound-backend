"""官方 Hub 四类来源制品的纯契约测试。"""

from __future__ import annotations

import hashlib
import io
import zipfile

from types import SimpleNamespace
from typing import cast
from unittest.mock import AsyncMock

import pytest
import yaml

from sqlalchemy.ext.asyncio import AsyncSession

from backend.app.hasn_task.service.workflow_template_service import (
    apply_builtin_template_updates,
)
from backend.app.marketplace.api.v1.publish import (
    SourceReconcileRequest,
    source_publish_user_from_authenticated_user,
)
from backend.app.marketplace.crud.crud_marketplace_template_version import (
    marketplace_template_version_dao,
)
from backend.app.marketplace.service.package_validation import (
    parse_skill_pack_package,
    parse_template_package,
    parse_workflow_package,
)
from backend.app.marketplace.service.source_release_service import (
    validate_hub_source_repo_path,
)
from backend.app.marketplace.storage.s3_storage import MarketplaceStorageService
from backend.common.exception import errors


def _package(files: dict[str, str], *, reverse: bool = False) -> bytes:
    """构造真实 ZIP 字节，条目顺序可控。"""
    output = io.BytesIO()
    items = list(files.items())
    if reverse:
        items.reverse()
    with zipfile.ZipFile(output, 'w', compression=zipfile.ZIP_DEFLATED) as archive:
        for name, content in items:
            archive.writestr(name, content)
    return output.getvalue()


def _manifest_hash(files: dict[str, str]) -> str:
    digest = hashlib.sha256()
    for path, content in sorted(files.items()):
        digest.update(path.encode())
        digest.update(b'\0')
        digest.update(hashlib.sha256(content.encode()).hexdigest().encode())
        digest.update(b'\0')
    return digest.hexdigest()


def test_skill_pack_package_has_order_independent_content_hash() -> None:
    files = {
        'bundle.yaml': yaml.safe_dump(
            {
                'name': 'office-docs',
                'display_name': '办公文档',
                'description': '办公能力包',
                'version': '1.2.0',
                'skills': ['huanxing/official/docx'],
                'instruction': '按需使用。',
            },
            allow_unicode=True,
            sort_keys=False,
        ),
        'README.md': '# 办公文档\n',
    }

    first = parse_skill_pack_package(_package(files))
    second = parse_skill_pack_package(_package(files, reverse=True))

    assert first.metadata['name'] == 'office-docs'
    assert first.hermes_yaml
    assert first.content_hash == second.content_hash == _manifest_hash(files)
    assert [item['path'] for item in first.files] == ['README.md', 'bundle.yaml']


def test_agent_template_package_returns_composed_markdown_and_manifest() -> None:
    files = {
        'template.yaml': (
            'id: assistant\n'
            'name: 全能助理\n'
            'description: 协助主人完成工作。\n'
            'version: 1.0.0\n'
        ),
        'SOUL.md': '# 人格\n',
        'USER.md': '# 主人\n',
        'MEMORY.md': '# 记忆\n',
    }

    package = parse_template_package(_package(files))

    assert package.soul_md == '# 人格\n'
    assert package.user_md == '# 主人\n'
    assert package.memory_md == '# 记忆\n'
    assert package.content_hash == _manifest_hash(files)
    assert len(package.files) == 4


def test_developer_template_keeps_user_and_memory_files_optional() -> None:
    files = {
        'template.yaml': (
            'id: developer-template\n'
            'name: 开发者模板\n'
            'description: 预留开发者发布支持。\n'
        ),
        'SOUL.md': '# 人格\n',
    }

    package = parse_template_package(_package(files))

    assert package.user_md is None
    assert package.memory_md is None


def test_workflow_package_returns_yaml_and_manifest() -> None:
    files = {
        'workflow-template.yaml': (
            'template_key: research\n'
            'name: 深度研究\n'
            'version: 2\n'
            'graph_spec:\n'
            '  nodes:\n'
            '    - node_key: origin\n'
            '      is_origin: true\n'
            '  edges: []\n'
        ),
        'README.md': '# 深度研究\n',
    }

    package = parse_workflow_package(_package(files))

    assert package.metadata['template_key'] == 'research'
    assert package.metadata['version'] == 2
    assert package.content_hash == _manifest_hash(files)


def test_hub_package_reports_malformed_yaml_as_request_error() -> None:
    with pytest.raises(errors.RequestError, match='bundle.yaml'):
        parse_skill_pack_package(_package({'bundle.yaml': 'name: [\n'}))


def test_hub_source_paths_are_resource_type_specific() -> None:
    assert validate_hub_source_repo_path(
        'skill_pack',
        'office-docs',
        'bundles/office-docs',
    ) == 'bundles/office-docs'
    assert validate_hub_source_repo_path(
        'agent_template',
        'assistant',
        'templates/agent/assistant',
    ) == 'templates/agent/assistant'
    assert validate_hub_source_repo_path(
        'workflow',
        'research',
        'workflow-templates/research',
    ) == 'workflow-templates/research'
    assert validate_hub_source_repo_path(
        'workflow',
        'fin_research',
        'workflow-templates/fin-research',
    ) == 'workflow-templates/fin-research'


def test_release_paths_are_content_addressed_for_every_resource_type() -> None:
    file_hash = 'a' * 64

    assert MarketplaceStorageService.skill_pack_release_path(
        'office-docs',
        '1.0.0',
        file_hash,
    ) == f'marketplace/skill-packs/office-docs/1.0.0/{file_hash}.zip'
    assert MarketplaceStorageService.template_release_path(
        'huanxing/agent/assistant',
        '1.0.0',
        file_hash,
    ) == (
        'marketplace/templates/huanxing/agent/assistant/'
        f'1.0.0/{file_hash}.zip'
    )
    assert MarketplaceStorageService.workflow_release_path(
        'research',
        '2',
        file_hash,
    ) == f'marketplace/workflows/research/2/{file_hash}.zip'


def test_reconcile_request_accepts_new_and_legacy_skill_manifests() -> None:
    current = SourceReconcileRequest.model_validate(
        {
            'resource_type': 'agent_template',
            'source_type': 'huanxing',
            'active_resource_ids': ['huanxing/agent/assistant'],
        }
    )
    legacy = SourceReconcileRequest.model_validate(
        {
            'source_type': 'github',
            'active_skill_ids': ['github/example/demo'],
        }
    )

    assert current.resource_type == 'agent_template'
    assert current.active_resource_ids == ['huanxing/agent/assistant']
    assert legacy.resource_type == 'skill'
    assert legacy.active_resource_ids == ['github/example/demo']


@pytest.mark.asyncio
async def test_mark_template_history_not_latest_allows_multiple_versions(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """发布第三版模板时必须一次取消全部历史版本的 latest 标记。"""
    update_many = AsyncMock(return_value=2)
    monkeypatch.setattr(
        marketplace_template_version_dao,
        'update_model_by_column',
        update_many,
    )
    session = cast(AsyncSession, SimpleNamespace())

    updated = await marketplace_template_version_dao.mark_all_not_latest(
        session,
        'huanxing/agent/sales-advisor',
    )

    assert updated == 2
    update_many.assert_awaited_once_with(
        session,
        {'is_latest': False},
        allow_multiple=True,
        template_id='huanxing/agent/sales-advisor',
    )


def test_authenticated_admin_maps_to_bearer_source_publisher() -> None:
    user = source_publish_user_from_authenticated_user(
        SimpleNamespace(
            id=42,
            username='admin',
            nickname='管理员',
            is_superuser=False,
            is_staff=True,
        )
    )

    assert user.user_id == 42
    assert user.is_admin is True
    assert user.auth_type == 'bearer'


def test_legacy_workflow_seed_does_not_clear_release_metadata() -> None:
    template = SimpleNamespace(
        name='旧名称',
        package_url='https://cdn.example/workflow.zip',
        file_hash='a' * 64,
        content_hash='b' * 64,
        file_size=1024,
        source_repo_path='workflow-templates/research',
        git_commit_hash='c' * 40,
        synced_at='2026-07-29T12:00:00Z',
    )

    apply_builtin_template_updates(template, {'name': '新名称'})

    assert template.name == '新名称'
    assert template.package_url == 'https://cdn.example/workflow.zip'
    assert template.file_hash == 'a' * 64
    assert template.content_hash == 'b' * 64
    assert template.file_size == 1024
    assert template.source_repo_path == 'workflow-templates/research'
    assert template.git_commit_hash == 'c' * 40
    assert template.synced_at == '2026-07-29T12:00:00Z'
