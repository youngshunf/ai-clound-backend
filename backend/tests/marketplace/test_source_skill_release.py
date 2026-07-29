"""官方与 GitHub 来源技能制品发布契约测试。"""

from __future__ import annotations

import io
import stat
import zipfile

import pytest

from backend.app.marketplace.api.v1.publish import (
    SourcePublishUser,
    require_source_publish_admin,
)
from backend.app.marketplace.service.package_validation import parse_skill_package
from backend.app.marketplace.service.source_release_service import (
    validate_git_commit_hash,
    validate_source_namespace,
    validate_source_repo_path,
)
from backend.app.marketplace.storage.s3_storage import MarketplaceStorageService
from backend.common.exception import errors


def _package(
    entries: list[tuple[str, bytes]],
    *,
    timestamp: tuple[int, int, int, int, int, int],
) -> bytes:
    output = io.BytesIO()
    with zipfile.ZipFile(output, 'w', compression=zipfile.ZIP_DEFLATED) as archive:
        for path, content in entries:
            info = zipfile.ZipInfo(path, date_time=timestamp)
            info.compress_type = zipfile.ZIP_DEFLATED
            archive.writestr(info, content)
    return output.getvalue()


SKILL_MD = b"""---
name: Release Demo
description: A deterministic release package.
version: 1.2.3
tags:
  - release
---

# Release Demo

Use this skill to verify source releases.
"""


def test_source_content_hash_ignores_zip_order_and_timestamp() -> None:
    first = _package(
        [
            ('SKILL.md', SKILL_MD),
            ('scripts/run.py', b'print("ok")\n'),
        ],
        timestamp=(2026, 7, 29, 10, 0, 0),
    )
    second = _package(
        [
            ('scripts/run.py', b'print("ok")\n'),
            ('SKILL.md', SKILL_MD),
        ],
        timestamp=(2025, 1, 1, 0, 0, 0),
    )

    first_package = parse_skill_package(first)
    second_package = parse_skill_package(second)

    assert first != second
    assert first_package.content_hash == second_package.content_hash
    assert first_package.markdown.startswith('---')
    assert first_package.files == [
        {
            'path': 'SKILL.md',
            'size': len(SKILL_MD),
            'sha256': first_package.files[0]['sha256'],
        },
        {
            'path': 'scripts/run.py',
            'size': len(b'print("ok")\n'),
            'sha256': first_package.files[1]['sha256'],
        },
    ]


def test_source_content_hash_changes_with_file_content() -> None:
    first = _package(
        [('SKILL.md', SKILL_MD), ('scripts/run.py', b'print("one")\n')],
        timestamp=(2026, 7, 29, 10, 0, 0),
    )
    second = _package(
        [('SKILL.md', SKILL_MD), ('scripts/run.py', b'print("two")\n')],
        timestamp=(2026, 7, 29, 10, 0, 0),
    )

    assert parse_skill_package(first).content_hash != parse_skill_package(second).content_hash


def test_source_namespace_matches_source_type() -> None:
    assert validate_source_namespace('huanxing', 'huanxing/official') == 'huanxing/official'
    assert validate_source_namespace('github', 'github/baoyu-skills') == 'github/baoyu-skills'

    with pytest.raises(errors.RequestError):
        validate_source_namespace('huanxing', 'github/baoyu-skills')
    with pytest.raises(errors.RequestError):
        validate_source_namespace('clawhub', 'clawhub/alice')
    with pytest.raises(errors.RequestError):
        validate_source_namespace('github', 'github')


def test_source_repo_path_and_commit_match_identity() -> None:
    assert validate_source_repo_path(
        'huanxing',
        'huanxing/official',
        'release-demo',
        'huanxing-skills/official/release-demo',
    ) == 'huanxing-skills/official/release-demo'
    assert validate_source_repo_path(
        'github',
        'github/baoyu-skills',
        'release-demo',
        'github/baoyu-skills/skills/release-demo',
    ) == 'github/baoyu-skills/skills/release-demo'
    assert validate_git_commit_hash('A' * 40) == 'a' * 40

    with pytest.raises(errors.RequestError, match='路径必须为'):
        validate_source_repo_path(
            'github',
            'github/baoyu-skills',
            'release-demo',
            'github/other/skills/release-demo',
        )
    with pytest.raises(errors.RequestError, match='40 位'):
        validate_git_commit_hash('not-a-commit')


def test_source_release_package_path_is_content_addressed() -> None:
    path = MarketplaceStorageService.skill_release_path(
        'huanxing/official/release-demo',
        '1.2.3',
        'a' * 64,
    )
    assert path == (
        'marketplace/skills/huanxing/official/release-demo/'
        f'1.2.3/{"a" * 64}.zip'
    )


def test_source_package_rejects_duplicate_path() -> None:
    content = _package(
        [('SKILL.md', SKILL_MD), ('SKILL.md', SKILL_MD)],
        timestamp=(2026, 7, 29, 10, 0, 0),
    )

    with pytest.raises(errors.RequestError, match='重复文件路径'):
        parse_skill_package(content)


def test_source_package_rejects_symbolic_link() -> None:
    output = io.BytesIO()
    with zipfile.ZipFile(output, 'w') as archive:
        archive.writestr('SKILL.md', SKILL_MD)
        link = zipfile.ZipInfo('scripts/current')
        link.create_system = 3
        link.external_attr = (stat.S_IFLNK | 0o777) << 16
        archive.writestr(link, b'../outside')

    with pytest.raises(errors.RequestError, match='符号链接'):
        parse_skill_package(output.getvalue())


def test_source_package_rejects_backslash_path() -> None:
    content = _package(
        [('SKILL.md', SKILL_MD), ('scripts\\..\\outside.py', b'unsafe')],
        timestamp=(2026, 7, 29, 10, 0, 0),
    )

    with pytest.raises(errors.RequestError, match='路径分隔符'):
        parse_skill_package(content)


def test_source_publish_requires_admin_api_key_identity() -> None:
    normal_user = SourcePublishUser(
        user_id=1,
        username='normal',
        nickname='普通用户',
        is_admin=False,
    )
    admin_user = SourcePublishUser(
        user_id=2,
        username='admin',
        nickname='管理员',
        is_admin=True,
    )

    with pytest.raises(errors.AuthorizationError, match='仅允许管理员'):
        require_source_publish_admin(normal_user)
    assert require_source_publish_admin(admin_user) is admin_user
