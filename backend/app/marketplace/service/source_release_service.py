"""官方与 GitHub 来源技能的制品发布服务。"""

from __future__ import annotations

import hashlib
import json

from dataclasses import dataclass
from decimal import Decimal
from pathlib import PurePosixPath
from typing import Any, Literal

import sqlalchemy as sa

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.app.marketplace.crud.crud_marketplace_skill import marketplace_skill_dao
from backend.app.marketplace.crud.crud_marketplace_skill_version import marketplace_skill_version_dao
from backend.app.marketplace.model import MarketplaceSkill, MarketplaceSkillVersion
from backend.app.marketplace.schema.marketplace_skill import (
    CreateMarketplaceSkillParam,
    UpdateMarketplaceSkillParam,
)
from backend.app.marketplace.schema.marketplace_skill_version import (
    CreateMarketplaceSkillVersionParam,
    UpdateMarketplaceSkillVersionParam,
)
from backend.app.marketplace.service.category_taxonomy import normalize_category
from backend.app.marketplace.service.package_validation import parse_skill_package
from backend.app.marketplace.service.resource_id import (
    build_resource_id,
    validate_slug,
    validate_version,
)
from backend.app.marketplace.service.skill_content_extractor import extract_skill_body
from backend.app.marketplace.service.translation_service import translation_service
from backend.app.marketplace.storage.s3_storage import marketplace_storage_service
from backend.common.exception import errors
from backend.utils.timezone import timezone

SourceType = Literal['huanxing', 'github']


@dataclass(frozen=True)
class SourceSkillReleaseResult:
    """一次来源技能发布的结果。"""

    skill_id: str
    namespace: str
    slug: str
    source_type: SourceType
    version: str
    package_url: str
    file_hash: str
    content_hash: str
    file_size: int
    uploaded: bool


def validate_source_namespace(source_type: str, namespace: str) -> str:
    """校验来源类型与命名空间一一对应。"""
    normalized_type = source_type.strip().lower()
    normalized_namespace = namespace.strip().strip('/')
    if normalized_type not in {'huanxing', 'github'}:
        raise errors.RequestError(msg='来源发布仅支持 huanxing 或 github')
    parts = normalized_namespace.split('/')
    if (
        len(parts) != 2
        or parts[0] != normalized_type
        or not parts[1]
    ):
        raise errors.RequestError(
            msg=f'{normalized_type} 来源命名空间必须为 {normalized_type}/<owner-or-category>'
        )
    validate_slug(parts[1])
    return normalized_namespace


def validate_source_repo_path(
    source_type: SourceType,
    namespace: str,
    slug: str,
    source_repo_path: str,
) -> str:
    """校验发布声明的 Hub 相对路径与来源身份完全一致。"""
    normalized = source_repo_path.replace('\\', '/').strip().strip('/')
    path = PurePosixPath(normalized)
    if not normalized or path.is_absolute() or '..' in path.parts:
        raise errors.RequestError(msg='来源仓库路径无效')
    owner_or_category = namespace.rsplit('/', 1)[-1]
    expected = (
        f'huanxing-skills/{owner_or_category}/{slug}'
        if source_type == 'huanxing'
        else f'github/{owner_or_category}/skills/{slug}'
    )
    if path.as_posix() != expected:
        raise errors.RequestError(msg=f'来源仓库路径必须为 {expected}')
    return expected


def validate_git_commit_hash(value: str | None) -> str | None:
    """校验可选的 Git commit SHA-1。"""
    if value is None:
        return None
    normalized = value.strip().lower()
    if len(normalized) != 40 or any(char not in '0123456789abcdef' for char in normalized):
        raise errors.RequestError(msg='来源仓库 commit 必须为 40 位十六进制 SHA-1')
    return normalized


def _localized_source_fields(
    *,
    source_language: str,
    name: str,
    description: str,
    body: str,
    existing: MarketplaceSkill | None,
) -> dict[str, str | None]:
    """写入源语言原文；只有源文未变时才保留既有译文，禁止保留陈旧译文。"""
    fields: dict[str, str | None] = {
        'name_en': None,
        'name_zh': None,
        'description_en': None,
        'description_zh': None,
        'body_en': None,
        'body_zh': None,
    }
    if source_language == 'zh':
        source_matches = (
            existing is not None
            and existing.name_zh == name
            and existing.description_zh == description
            and (existing.body_zh or '') == body
        )
        fields.update(
            {
                'name_zh': name,
                'description_zh': description,
                'body_zh': body or None,
                'name_en': existing.name_en if source_matches and existing else None,
                'description_en': (
                    existing.description_en if source_matches and existing else None
                ),
                'body_en': existing.body_en if source_matches and existing else None,
            }
        )
    else:
        source_matches = (
            existing is not None
            and existing.name_en == name
            and existing.description_en == description
            and (existing.body_en or '') == body
        )
        fields.update(
            {
                'name_en': name,
                'description_en': description,
                'body_en': body or None,
                'name_zh': existing.name_zh if source_matches and existing else None,
                'description_zh': (
                    existing.description_zh if source_matches and existing else None
                ),
                'body_zh': existing.body_zh if source_matches and existing else None,
            }
        )
    return fields


def _changelog(metadata: dict[str, Any], explicit: str | None) -> str | None:
    if explicit is not None:
        return explicit
    value = metadata.get('changelog')
    if isinstance(value, list):
        return '\n'.join(str(item) for item in value)
    return str(value) if value is not None else None


class SourceReleaseService:
    """接收本地 Hub 打包产物并原子更新目录元数据。"""

    async def publish_skill(
        self,
        *,
        db: AsyncSession,
        source_type: SourceType,
        namespace: str,
        slug: str,
        content: bytes,
        source_repo_url: str | None,
        source_repo_path: str,
        git_commit_hash: str | None,
        is_common: bool,
        changelog: str | None = None,
        expected_content_hash: str | None = None,
        expected_file_hash: str | None = None,
    ) -> SourceSkillReleaseResult:
        namespace = validate_source_namespace(source_type, namespace)
        slug = validate_slug(slug)
        source_repo_path = validate_source_repo_path(
            source_type,
            namespace,
            slug,
            source_repo_path,
        )
        git_commit_hash = validate_git_commit_hash(git_commit_hash)
        package = parse_skill_package(content)
        version = validate_version(str(package.metadata.get('version') or '1.0.0'))
        skill_id = build_resource_id(namespace, slug)
        file_hash = hashlib.sha256(content).hexdigest()
        if expected_content_hash and expected_content_hash != package.content_hash:
            raise errors.RequestError(msg='发布清单 content_hash 与技能包内容不一致')
        if expected_file_hash and expected_file_hash != file_hash:
            raise errors.RequestError(msg='发布清单 file_hash 与技能包 SHA256 不一致')

        existing = await marketplace_skill_dao.get_by_id(db, skill_id)
        existing_version = await marketplace_skill_version_dao.get_by_skill_and_version(
            db,
            skill_id,
            version,
        )
        reusable_version = existing_version if (
            existing_version
            and existing_version.content_hash == package.content_hash
            and existing_version.package_url
            and existing_version.file_hash
            and existing_version.file_size is not None
        ) else None
        if reusable_version is None:
            uploaded = True
            package_url, file_hash, file_size = (
                await marketplace_storage_service.upload_skill_release_package(
                    db=db,
                    skill_id=skill_id,
                    version=version,
                    content=content,
                )
            )
        else:
            uploaded = False
            assert reusable_version.package_url is not None
            assert reusable_version.file_hash is not None
            assert reusable_version.file_size is not None
            package_url = str(reusable_version.package_url)
            file_hash = str(reusable_version.file_hash)
            file_size = int(reusable_version.file_size)

        icon_url = None
        if package.icon:
            if uploaded or existing is None or not existing.icon_url:
                icon_url = await marketplace_storage_service.upload_icon(
                    db=db,
                    item_type='skill',
                    item_id=skill_id,
                    content=package.icon.content,
                    filename=package.icon.filename,
                    version=package.content_hash[:16],
                )
            else:
                icon_url = existing.icon_url

        metadata = package.metadata
        name = str(metadata['name'])
        description = str(metadata['description'])
        body = extract_skill_body(package.markdown)
        detected = translation_service.detect_language(f'{name}\n{description}\n{body[:2000]}')
        source_language = 'zh' if detected == 'zh' else 'en'
        localized = _localized_source_fields(
            source_language=source_language,
            name=name,
            description=description,
            body=body,
            existing=existing,
        )
        tags = [str(item) for item in metadata.get('tags') or []]
        tags_json = json.dumps(tags, ensure_ascii=False)
        files_json = json.dumps(package.files, ensure_ascii=False)
        translation_preserved = (
            localized['name_en'] is not None and localized['name_zh'] is not None
        )
        category_hint = metadata.get('category') or namespace.rsplit('/', 1)[-1]
        now = timezone.now()
        skill_record = {
            'skill_id': skill_id,
            'namespace': namespace,
            'slug': slug,
            'status': 'published',
            'visibility': 'public',
            'reviewed_by': existing.reviewed_by if existing else None,
            'reviewed_at': existing.reviewed_at if existing else None,
            'review_note': existing.review_note if existing else None,
            'published_at': existing.published_at if existing and existing.published_at else now,
            'suspended_at': None,
            'suspend_reason': None,
            'name': name,
            **localized,
            'files': files_json,
            'source_language': source_language,
            'icon_url': icon_url,
            'emoji': metadata.get('emoji'),
            'author_id': None,
            'author_name': metadata.get('author') or namespace.rsplit('/', 1)[-1],
            'category': normalize_category(category_hint),
            'tags': tags_json,
            'tags_en': tags_json if source_language == 'en' else None,
            'tags_zh': tags_json if source_language == 'zh' else None,
            'source_type': source_type,
            'source_repo_url': source_repo_url,
            'source_repo_path': source_repo_path,
            'repo_path': None,
            'pricing_type': 'free',
            'price': Decimal(0),
            'is_private': False,
            'is_official': source_type == 'huanxing',
            'is_common': is_common,
            'download_count': existing.download_count if existing else 0,
            'star_count': existing.star_count if existing else 0,
            'git_commit_hash': git_commit_hash,
            'synced_at': now,
            'translated_at': (
                existing.translated_at if existing and translation_preserved else None
            ),
        }
        if existing:
            await marketplace_skill_dao.update(
                db,
                existing.id,
                UpdateMarketplaceSkillParam(**skill_record),
            )
        else:
            await marketplace_skill_dao.create(
                db,
                CreateMarketplaceSkillParam(**skill_record),
            )
            await db.flush()

        await db.execute(
            sa.update(MarketplaceSkillVersion)
            .where(MarketplaceSkillVersion.skill_id == skill_id)
            .values(is_latest=False)
        )
        version_record = {
            'skill_id': skill_id,
            'version': version,
            'changelog': _changelog(metadata, changelog),
            'package_url': package_url,
            'file_hash': file_hash,
            'content_hash': package.content_hash,
            'file_size': file_size,
            'is_latest': True,
            'published_at': now,
        }
        if existing_version:
            await marketplace_skill_version_dao.update(
                db,
                existing_version.id,
                UpdateMarketplaceSkillVersionParam(**version_record),
            )
        else:
            await marketplace_skill_version_dao.create(
                db,
                CreateMarketplaceSkillVersionParam(**version_record),
            )
        await db.flush()
        return SourceSkillReleaseResult(
            skill_id=skill_id,
            namespace=namespace,
            slug=slug,
            source_type=source_type,
            version=version,
            package_url=package_url,
            file_hash=file_hash,
            content_hash=package.content_hash,
            file_size=file_size,
            uploaded=uploaded,
        )

    async def reconcile(
        self,
        *,
        db: AsyncSession,
        source_type: SourceType,
        active_skill_ids: list[str],
    ) -> list[str]:
        """把完整发布清单之外的同来源技能下架，不物理删除目录或历史版本。"""
        normalized_ids: set[str] = set()
        for skill_id in active_skill_ids:
            normalized = skill_id.strip().strip('/')
            namespace, slug = normalized.rsplit('/', 1) if '/' in normalized else ('', '')
            validate_source_namespace(source_type, namespace)
            normalized_ids.add(build_resource_id(namespace, slug))

        rows = (
            await db.execute(
                select(MarketplaceSkill).where(MarketplaceSkill.source_type == source_type)
            )
        ).scalars().all()
        unpublished: list[str] = []
        for skill in rows:
            if skill.skill_id in normalized_ids:
                continue
            if skill.status != 'unpublished' or skill.visibility != 'private':
                skill.status = 'unpublished'
                skill.visibility = 'private'
                skill.is_private = True
                skill.is_common = False
                unpublished.append(skill.skill_id)
        await db.flush()
        return sorted(unpublished)


source_release_service = SourceReleaseService()
