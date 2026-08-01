"""官方与 GitHub 来源技能的制品发布服务。"""

from __future__ import annotations

import hashlib
import json

from dataclasses import dataclass
from decimal import Decimal
from pathlib import PurePosixPath
from typing import Any, Literal

import sqlalchemy as sa
import yaml

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.app.hasn_task.model import HasnWorkflowTemplate
from backend.app.hasn_task.service.workflow_template_service import (
    build_builtin_template_data,
    validate_graph_spec,
    workflow_template_service,
)
from backend.app.marketplace.crud.crud_marketplace_skill import marketplace_skill_dao
from backend.app.marketplace.crud.crud_marketplace_skill_version import marketplace_skill_version_dao
from backend.app.marketplace.crud.crud_marketplace_template import marketplace_template_dao
from backend.app.marketplace.crud.crud_marketplace_template_version import (
    marketplace_template_version_dao,
)
from backend.app.marketplace.model import (
    MarketplaceSkill,
    MarketplaceSkillVersion,
    MarketplaceTemplate,
    MarketplaceTemplateVersion,
)
from backend.app.marketplace.schema.marketplace_skill import (
    CreateMarketplaceSkillParam,
    UpdateMarketplaceSkillParam,
)
from backend.app.marketplace.schema.marketplace_skill_version import (
    CreateMarketplaceSkillVersionParam,
    UpdateMarketplaceSkillVersionParam,
)
from backend.app.marketplace.schema.marketplace_template import (
    CreateMarketplaceTemplateParam,
    UpdateMarketplaceTemplateParam,
)
from backend.app.marketplace.schema.marketplace_template_version import (
    CreateMarketplaceTemplateVersionParam,
    UpdateMarketplaceTemplateVersionParam,
)
from backend.app.marketplace.schema.skill_pack import SkillPackCreateRequest
from backend.app.marketplace.service.category_taxonomy import normalize_category
from backend.app.marketplace.service.package_validation import (
    parse_skill_pack_package,
    parse_skill_package,
    parse_template_package,
    parse_workflow_package,
)
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
HubResourceType = Literal['skill_pack', 'agent_template', 'workflow']


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


@dataclass(frozen=True)
class SourceHubReleaseResult:
    """一次官方 Hub 非技能资源发布结果。"""

    resource_id: str
    resource_type: HubResourceType
    slug: str
    source_type: Literal['huanxing']
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


def validate_hub_source_repo_path(
    resource_type: HubResourceType,
    slug: str,
    source_repo_path: str,
) -> str:
    """校验非技能官方资源的 Hub 相对路径与资源类型一致。"""
    slug = validate_slug(slug)
    normalized = source_repo_path.replace('\\', '/').strip().strip('/')
    path = PurePosixPath(normalized)
    if not normalized or path.is_absolute() or '..' in path.parts:
        raise errors.RequestError(msg='来源仓库路径无效')
    prefixes = {
        'skill_pack': 'bundles',
        'agent_template': 'templates/agent',
        'workflow': 'workflow-templates',
    }
    prefix = prefixes.get(resource_type)
    if prefix is None:
        raise errors.RequestError(msg='不支持的官方 Hub 资源类型')
    directory_slug = slug.replace('_', '-') if resource_type == 'workflow' else slug
    expected = f'{prefix}/{directory_slug}'
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


def _verify_release_hashes(
    *,
    content: bytes,
    content_hash: str,
    expected_content_hash: str | None,
    expected_file_hash: str | None,
) -> str:
    """校验 CLI 发布清单的双指纹，并返回 ZIP SHA256。"""
    file_hash = hashlib.sha256(content).hexdigest()
    if expected_content_hash and expected_content_hash != content_hash:
        raise errors.RequestError(msg='发布清单 content_hash 与制品内容不一致')
    if expected_file_hash and expected_file_hash != file_hash:
        raise errors.RequestError(msg='发布清单 file_hash 与制品 SHA256 不一致')
    return file_hash


def _template_localized_fields(name: str, description: str) -> dict[str, str | None]:
    """按源语言写模板原文，不制造翻译文本。"""
    source_language = (
        'zh'
        if translation_service.detect_language(f'{name}\n{description}') == 'zh'
        else 'en'
    )
    return {
        'source_language': source_language,
        'name_en': name if source_language == 'en' else None,
        'name_zh': name if source_language == 'zh' else None,
        'description_en': description if source_language == 'en' else None,
        'description_zh': description if source_language == 'zh' else None,
    }


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


async def _is_reusable_public_release(
    db: AsyncSession,
    release: Any,
    *,
    content_hash: str | None = None,
    file_hash: str | None = None,
) -> bool:
    """只有内容一致且已在公开桶的制品才允许跳过上传。"""
    return bool(
        release
        and (content_hash is not None or file_hash is not None)
        and (content_hash is None or release.content_hash == content_hash)
        and (file_hash is None or release.file_hash == file_hash)
        and release.package_url
        and release.file_hash
        and release.file_size is not None
        and await marketplace_storage_service.is_public_url(
            db,
            str(release.package_url),
        )
    )


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
        reusable_version = (
            existing_version
            if await _is_reusable_public_release(
                db,
                existing_version,
                content_hash=package.content_hash,
            )
            else None
        )
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

    async def publish_skill_pack(
        self,
        *,
        db: AsyncSession,
        slug: str,
        content: bytes,
        source_repo_path: str,
        git_commit_hash: str | None,
        is_common: bool,
        expected_content_hash: str | None = None,
        expected_file_hash: str | None = None,
    ) -> SourceHubReleaseResult:
        """发布官方技能包 ZIP，并更新技能包目录与不可变 CDN 制品。"""
        from backend.app.marketplace.service import skill_pack_service

        slug = validate_slug(slug)
        source_repo_path = validate_hub_source_repo_path(
            'skill_pack',
            slug,
            source_repo_path,
        )
        git_commit_hash = validate_git_commit_hash(git_commit_hash)
        package = parse_skill_pack_package(content)
        if str(package.metadata.get('name') or '').strip() != slug:
            raise errors.RequestError(msg='bundle.yaml name 必须与发布 slug 一致')
        version = validate_version(str(package.metadata.get('version') or '1.0.0'))
        release_file_hash = _verify_release_hashes(
            content=content,
            content_hash=package.content_hash,
            expected_content_hash=expected_content_hash,
            expected_file_hash=expected_file_hash,
        )
        template_id = f'huanxing/{slug}'
        existing_version = await marketplace_template_version_dao.get_by_template_and_version(
            db,
            template_id,
            version,
        )
        reusable_version = (
            existing_version
            if await _is_reusable_public_release(
                db,
                existing_version,
                file_hash=release_file_hash,
            )
            else None
        )
        if reusable_version:
            uploaded = False
            package_url = str(reusable_version.package_url)
            file_hash = str(reusable_version.file_hash)
            assert reusable_version.file_size is not None
            file_size = int(reusable_version.file_size)
        else:
            uploaded = True
            package_url, file_hash, file_size = (
                await marketplace_storage_service.upload_skill_pack_release_package(
                    db,
                    slug,
                    version,
                    content,
                )
            )

        icon_url = None
        if package.icon:
            icon_url = await marketplace_storage_service.upload_icon(
                db=db,
                item_type='template',
                item_id=template_id,
                content=package.icon.content,
                filename=package.icon.filename,
                version=package.content_hash[:16],
            )
        marketplace_keys = {
            'category',
            'display_name',
            'is_common',
            'is_official',
            'tags',
            'version',
        }
        hermes_spec = {
            key: value
            for key, value in package.metadata.items()
            if key not in marketplace_keys
        }
        hermes_yaml = yaml.safe_dump(
            hermes_spec,
            allow_unicode=True,
            sort_keys=False,
        )
        payload = SkillPackCreateRequest(
            template_id=template_id,
            namespace='huanxing',
            name=str(
                package.metadata.get('display_name')
                or package.metadata.get('name')
                or slug
            ),
            description=str(package.metadata.get('description') or ''),
            icon_url=icon_url,
            category=package.metadata.get('category'),
            bundle_slug=slug,
            command_key=f'/{slug}',
            version=version,
            hermes_bundle_json=hermes_spec,
            hermes_yaml=hermes_yaml,
            is_private=False,
            is_official=True,
            status='published',
        )
        pack_record = await skill_pack_service.upsert_skill_pack(
            db,
            payload,
            author_id=None,
            is_common=is_common,
            strict_members=True,
        )
        now = timezone.now()
        await db.execute(
            sa.update(MarketplaceTemplate)
            .where(MarketplaceTemplate.template_id == template_id)
            .values(
                visibility='public',
                source_type='huanxing',
                source_repo_path=source_repo_path,
                repo_path=None,
                tags=','.join(
                    str(item) for item in package.metadata.get('tags') or []
                ),
                skill_dependencies=','.join(pack_record['skill_ids']),
                author_name='huanxing',
                git_commit_hash=git_commit_hash,
                synced_at=now,
                published_at=now,
            )
        )
        await db.execute(
            sa.update(MarketplaceTemplateVersion)
            .where(
                MarketplaceTemplateVersion.template_id == template_id,
                MarketplaceTemplateVersion.version == version,
            )
            .values(
                package_url=package_url,
                file_hash=file_hash,
                file_size=file_size,
                content_hash=pack_record['content_hash'],
                is_latest=True,
                published_at=now,
            )
        )
        await db.flush()
        return SourceHubReleaseResult(
            resource_id=template_id,
            resource_type='skill_pack',
            slug=slug,
            source_type='huanxing',
            version=version,
            package_url=package_url,
            file_hash=file_hash,
            content_hash=package.content_hash,
            file_size=file_size,
            uploaded=uploaded,
        )

    async def publish_agent_template(
        self,
        *,
        db: AsyncSession,
        slug: str,
        content: bytes,
        source_repo_path: str,
        git_commit_hash: str | None,
        expected_content_hash: str | None = None,
        expected_file_hash: str | None = None,
    ) -> SourceHubReleaseResult:
        """发布官方分身模板 ZIP，并将合成后的 profile 文档写入权威目录。"""
        slug = validate_slug(slug)
        source_repo_path = validate_hub_source_repo_path(
            'agent_template',
            slug,
            source_repo_path,
        )
        git_commit_hash = validate_git_commit_hash(git_commit_hash)
        package = parse_template_package(content, require_runtime_files=True)
        metadata = package.metadata
        declared_id = str(metadata.get('id') or slug).strip()
        if declared_id != slug:
            raise errors.RequestError(msg='template.yaml id 必须与发布 slug 一致')
        version = validate_version(str(metadata.get('version') or '1.0.0'))
        _verify_release_hashes(
            content=content,
            content_hash=package.content_hash,
            expected_content_hash=expected_content_hash,
            expected_file_hash=expected_file_hash,
        )
        template_id = f'huanxing/agent/{slug}'
        existing = await marketplace_template_dao.get_by_id(db, template_id)
        existing_version = await marketplace_template_version_dao.get_by_template_and_version(
            db,
            template_id,
            version,
        )
        reusable_version = (
            existing_version
            if await _is_reusable_public_release(
                db,
                existing_version,
                content_hash=package.content_hash,
            )
            else None
        )
        if reusable_version:
            uploaded = False
            package_url = str(reusable_version.package_url)
            file_hash = str(reusable_version.file_hash)
            assert reusable_version.file_size is not None
            file_size = int(reusable_version.file_size)
        else:
            uploaded = True
            package_url, file_hash, file_size = (
                await marketplace_storage_service.upload_template_release_package(
                    db,
                    template_id,
                    version,
                    content,
                )
            )

        icon_url = existing.icon_url if existing else None
        if package.icon:
            icon_url = await marketplace_storage_service.upload_icon(
                db=db,
                item_type='template',
                item_id=template_id,
                content=package.icon.content,
                filename=package.icon.filename,
                version=package.content_hash[:16],
            )
        name = str(metadata.get('name') or metadata.get('display_name') or slug)
        description = str(metadata.get('description') or '')
        raw_name_pool = metadata.get('name_pool')
        name_pool = (
            ','.join(
                str(item).strip()
                for item in raw_name_pool
                if str(item).strip()
            )
            if isinstance(raw_name_pool, list)
            else None
        )
        if not name_pool and metadata.get('display_name'):
            name_pool = str(metadata['display_name']).strip()
        skill_dependencies = metadata.get('skills') or metadata.get(
            'skill_dependencies'
        ) or []
        sop_dependencies = metadata.get('sops') or metadata.get('sop_dependencies') or []
        now = timezone.now()
        template_record = {
            'template_id': template_id,
            'namespace': 'huanxing/agent',
            'slug': slug,
            'status': 'published',
            'visibility': 'public',
            'published_at': existing.published_at if existing and existing.published_at else now,
            'template_type': 'agent_template',
            'name': name,
            **_template_localized_fields(name, description),
            'description': description,
            'icon_url': icon_url,
            'emoji': metadata.get('emoji'),
            'author_name': metadata.get('author') or 'huanxing',
            'pricing_type': 'free',
            'price': Decimal(0),
            'is_private': False,
            'is_official': True,
            'builtin': bool(metadata.get('builtin', False)),
            'builtin_key': (
                str(metadata['builtin_key']).strip()
                if metadata.get('builtin') and metadata.get('builtin_key')
                else None
            ),
            'download_count': existing.download_count if existing else 0,
            'category': metadata.get('category') or 'agent',
            'tags': ','.join(str(item) for item in metadata.get('tags') or []),
            'name_pool': name_pool,
            'source_type': 'huanxing',
            'source_repo_path': source_repo_path,
            'skill_dependencies': (
                ','.join(str(item) for item in skill_dependencies)
                if isinstance(skill_dependencies, list)
                else str(skill_dependencies)
            ),
            'soul_md': package.soul_md,
            'agents_md': None,
            'user_md': package.user_md,
            'memory_md': package.memory_md,
            'sop_dependencies': (
                ','.join(str(item) for item in sop_dependencies)
                if isinstance(sop_dependencies, list)
                else str(sop_dependencies)
            ),
            'repo_path': None,
            'git_commit_hash': git_commit_hash,
            'synced_at': now,
        }
        if existing:
            await marketplace_template_dao.update(
                db,
                existing.id,
                UpdateMarketplaceTemplateParam(**template_record),
            )
        else:
            await marketplace_template_dao.create(
                db,
                CreateMarketplaceTemplateParam(**template_record),
            )
            await db.flush()
        await marketplace_template_version_dao.mark_all_not_latest(db, template_id)
        version_record = {
            'template_id': template_id,
            'version': version,
            'changelog': str(metadata.get('changelog') or f'Version {version}'),
            'skill_dependencies_versioned': (
                dict.fromkeys(
                    [str(item) for item in skill_dependencies],
                    '*',
                )
                if isinstance(skill_dependencies, list)
                else None
            ),
            'content_hash': package.content_hash,
            'package_url': package_url,
            'file_hash': file_hash,
            'file_size': file_size,
            'is_latest': True,
            'published_at': now,
        }
        if existing_version:
            await marketplace_template_version_dao.update(
                db,
                existing_version.id,
                UpdateMarketplaceTemplateVersionParam(**version_record),
            )
        else:
            await marketplace_template_version_dao.create(
                db,
                CreateMarketplaceTemplateVersionParam(**version_record),
            )
        await db.flush()
        return SourceHubReleaseResult(
            resource_id=template_id,
            resource_type='agent_template',
            slug=slug,
            source_type='huanxing',
            version=version,
            package_url=package_url,
            file_hash=file_hash,
            content_hash=package.content_hash,
            file_size=file_size,
            uploaded=uploaded,
        )

    async def publish_workflow(
        self,
        *,
        db: AsyncSession,
        slug: str,
        content: bytes,
        source_repo_path: str,
        git_commit_hash: str | None,
        expected_content_hash: str | None = None,
        expected_file_hash: str | None = None,
    ) -> SourceHubReleaseResult:
        """发布官方场景工作流 ZIP，并原子更新内置模板与制品指纹。"""
        slug = validate_slug(slug)
        source_repo_path = validate_hub_source_repo_path(
            'workflow',
            slug,
            source_repo_path,
        )
        git_commit_hash = validate_git_commit_hash(git_commit_hash)
        package = parse_workflow_package(content)
        metadata = package.metadata
        if str(metadata.get('template_key') or '').strip() != slug:
            raise errors.RequestError(
                msg='workflow-template.yaml template_key 必须与发布 slug 一致'
            )
        try:
            version_int = int(metadata.get('version') or 1)
        except (TypeError, ValueError) as exc:
            raise errors.RequestError(msg='场景工作流 version 必须为正整数') from exc
        if version_int < 1:
            raise errors.RequestError(msg='场景工作流 version 必须为正整数')
        version = str(version_int)
        _verify_release_hashes(
            content=content,
            content_hash=package.content_hash,
            expected_content_hash=expected_content_hash,
            expected_file_hash=expected_file_hash,
        )
        existing = (
            await db.execute(
                select(HasnWorkflowTemplate).where(
                    HasnWorkflowTemplate.template_key == slug
                )
            )
        ).scalar_one_or_none()
        reusable = (
            existing
            if await _is_reusable_public_release(
                db,
                existing,
                content_hash=package.content_hash,
            )
            else None
        )
        if reusable:
            uploaded = False
            package_url = str(reusable.package_url)
            file_hash = str(reusable.file_hash)
            assert reusable.file_size is not None
            file_size = int(reusable.file_size)
        else:
            uploaded = True
            package_url, file_hash, file_size = (
                await marketplace_storage_service.upload_workflow_release_package(
                    db,
                    slug,
                    version,
                    content,
                )
            )
        validate_graph_spec(metadata.get('graph_spec'))
        now = timezone.now()
        release_metadata = {
            **metadata,
            'is_builtin': True,
            'builtin_key': slug,
            'source': 'builtin',
            'owner_id': None,
            'package_url': package_url,
            'file_hash': file_hash,
            'content_hash': package.content_hash,
            'file_size': file_size,
            'source_repo_path': source_repo_path,
            'git_commit_hash': git_commit_hash,
            'synced_at': now,
        }
        data = build_builtin_template_data(release_metadata)
        outcome = await workflow_template_service.upsert_builtin_template(db, data=data)
        if outcome == 'skipped':
            raise errors.ConflictError(msg='同名工作流模板已被非内置资源占用')
        await db.flush()
        return SourceHubReleaseResult(
            resource_id=f'huanxing/workflow/{slug}',
            resource_type='workflow',
            slug=slug,
            source_type='huanxing',
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

    async def reconcile_resource(
        self,
        *,
        db: AsyncSession,
        resource_type: Literal['skill', 'skill_pack', 'agent_template', 'workflow'],
        source_type: SourceType,
        active_resource_ids: list[str],
    ) -> list[str]:
        """按资源类型软下架完整清单之外的官方来源资源。"""
        if resource_type == 'skill':
            return await self.reconcile(
                db=db,
                source_type=source_type,
                active_skill_ids=active_resource_ids,
            )
        if source_type != 'huanxing':
            raise errors.RequestError(msg='GitHub 来源仅支持 skill 资源')

        if resource_type in {'skill_pack', 'agent_template'}:
            expected_prefix = (
                'huanxing/' if resource_type == 'skill_pack' else 'huanxing/agent/'
            )
            active_ids: set[str] = set()
            for resource_id in active_resource_ids:
                normalized = resource_id.strip().strip('/')
                if not normalized.startswith(expected_prefix):
                    raise errors.RequestError(
                        msg=f'{resource_type} 资源 ID 必须以 {expected_prefix} 开头'
                    )
                validate_slug(normalized.removeprefix(expected_prefix))
                active_ids.add(normalized)
            rows = (
                await db.execute(
                    select(MarketplaceTemplate).where(
                        MarketplaceTemplate.template_type == resource_type,
                        MarketplaceTemplate.is_official,
                        MarketplaceTemplate.source_type.in_(
                            (
                                'huanxing',
                                'local'
                                if resource_type == 'skill_pack'
                                else 'official',
                            )
                        ),
                    )
                )
            ).scalars().all()
            unpublished: list[str] = []
            for template in rows:
                if template.template_id in active_ids:
                    continue
                if template.status != 'unpublished' or template.visibility != 'private':
                    template.status = 'unpublished'
                    template.visibility = 'private'
                    template.is_private = True
                    template.is_common = False
                    unpublished.append(template.template_id)
            await db.flush()
            return sorted(unpublished)

        active_keys: set[str] = set()
        prefix = 'huanxing/workflow/'
        for resource_id in active_resource_ids:
            normalized = resource_id.strip().strip('/')
            if not normalized.startswith(prefix):
                raise errors.RequestError(msg=f'workflow 资源 ID 必须以 {prefix} 开头')
            active_keys.add(validate_slug(normalized.removeprefix(prefix)))
        workflow_rows = (
            await db.execute(
                select(HasnWorkflowTemplate).where(
                    HasnWorkflowTemplate.is_builtin,
                    HasnWorkflowTemplate.source == 'builtin',
                )
            )
        ).scalars().all()
        archived: list[str] = []
        for workflow in workflow_rows:
            if workflow.template_key in active_keys:
                continue
            if workflow.status != 'archived':
                workflow.status = 'archived'
                archived.append(f'{prefix}{workflow.template_key}')
        await db.flush()
        return sorted(archived)


source_release_service = SourceReleaseService()
