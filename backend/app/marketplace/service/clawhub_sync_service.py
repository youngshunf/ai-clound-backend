"""ClawHub 技能元数据同步服务。

ClawHub 是外部技能目录和制品源。唤星只同步目录、版本、文件清单和上游下载地址，
不再把技能 ZIP 下载、解压或持久化到服务器磁盘。
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import operator

from collections import Counter, defaultdict
from collections.abc import Iterable
from datetime import datetime
from pathlib import PurePosixPath
from typing import Any
from urllib.parse import urlencode

import httpx
import sqlalchemy as sa

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import load_only

from backend.app.marketplace.crud.crud_marketplace_category import marketplace_category_dao
from backend.app.marketplace.crud.crud_marketplace_skill import marketplace_skill_dao
from backend.app.marketplace.crud.crud_marketplace_skill_version import marketplace_skill_version_dao
from backend.app.marketplace.crud.crud_marketplace_sync_log import marketplace_sync_log_dao
from backend.app.marketplace.model import MarketplaceSkill, MarketplaceSkillVersion, MarketplaceSyncLog
from backend.app.marketplace.schema.marketplace_skill import (
    CreateMarketplaceSkillParam,
    UpdateMarketplaceSkillParam,
)
from backend.app.marketplace.schema.marketplace_skill_version import (
    CreateMarketplaceSkillVersionParam,
    UpdateMarketplaceSkillVersionParam,
)
from backend.app.marketplace.schema.marketplace_sync_log import (
    CreateMarketplaceSyncLogParam,
    UpdateMarketplaceSyncLogParam,
)
from backend.app.marketplace.service.category_taxonomy import DEFAULT_CATEGORY, normalize_category
from backend.app.marketplace.service.github_sync_service import (
    metadata_unchanged,
    translation_from_existing,
)
from backend.app.marketplace.service.translation_service import translation_service
from backend.common.exception import errors
from backend.common.log import log
from backend.core.conf import settings
from backend.utils.timezone import timezone


class ClawHubUpstreamError(RuntimeError):
    """ClawHub 上游契约或可用性错误。"""


class ClawHubIdentityError(ClawHubUpstreamError):
    """ClawHub 技能身份无法唯一解析。"""


class ClawHubAmbiguousSkillError(ClawHubIdentityError):
    """ClawHub 裸 slug 对应多个作者。"""

    def __init__(self, slug: str, owner_handles: Iterable[str]) -> None:
        self.slug = slug
        self.owner_handles = tuple(dict.fromkeys(owner_handles))
        super().__init__(f'ambiguous slug {slug!r}，必须提供 ownerHandle')


MAX_CLAWHUB_FILE_COUNT = 1000
MAX_CLAWHUB_TOTAL_SIZE = 100 * 1024 * 1024


def _bounded_catalog_text(value: Any, max_length: int) -> str | None:
    """按数据库 VARCHAR 契约截断外部目录文本。"""
    if value is None:
        return None
    return str(value)[:max_length]


def build_clawhub_download_url(
    api_url: str,
    *,
    owner_handle: str,
    slug: str,
    version: str,
) -> str:
    """按 ClawHub 当前公开契约构造可消歧的下载地址。"""
    query = urlencode(
        {
            'slug': slug,
            'version': version,
            'ownerHandle': owner_handle,
        }
    )
    return f"{api_url.rstrip('/')}/download?{query}"


def _owner_from_skill(skill: dict[str, Any]) -> str | None:
    owner_handle = skill.get('ownerHandle')
    if isinstance(owner_handle, str) and owner_handle.strip():
        return owner_handle.strip()
    owner = skill.get('owner')
    if isinstance(owner, dict):
        handle = owner.get('handle')
        if isinstance(handle, str) and handle.strip():
            return handle.strip()
    return None


def _skill_key(skill: dict[str, Any]) -> str:
    slug = str(skill.get('slug') or '').strip()
    owner_handle = _owner_from_skill(skill)
    return f'{owner_handle}/{slug}' if owner_handle else slug


def _safe_manifest_path(value: Any) -> str:
    path = str(value or '').replace('\\', '/').strip()
    pure_path = PurePosixPath(path)
    if not path or pure_path.is_absolute() or '..' in pure_path.parts:
        raise ClawHubUpstreamError(f'ClawHub 版本文件路径不安全: {path!r}')
    return pure_path.as_posix()


def _normalize_file_manifest(files: Any) -> list[dict[str, Any]]:
    if not isinstance(files, list):
        raise ClawHubUpstreamError('ClawHub 版本响应缺少 files 数组')
    if not files or len(files) > MAX_CLAWHUB_FILE_COUNT:
        raise ClawHubUpstreamError('ClawHub 版本文件数量无效')
    normalized: list[dict[str, Any]] = []
    seen_paths: set[str] = set()
    for item in files:
        if not isinstance(item, dict):
            raise ClawHubUpstreamError('ClawHub 版本文件项不是对象')
        path = _safe_manifest_path(item.get('path'))
        size = item.get('size')
        if isinstance(size, bool) or not isinstance(size, int | float) or size < 0:
            raise ClawHubUpstreamError(f'ClawHub 版本文件大小无效: {path}')
        if path in seen_paths:
            raise ClawHubUpstreamError(f'ClawHub 版本文件路径重复: {path}')
        seen_paths.add(path)
        sha256 = str(item.get('sha256') or '').lower()
        if len(sha256) != 64 or any(char not in '0123456789abcdef' for char in sha256):
            raise ClawHubUpstreamError(f'ClawHub 版本文件缺少合法 SHA256: {path}')
        normalized.append(
            {
                'path': path,
                'size': int(size),
                'sha256': sha256,
                'contentType': item.get('contentType'),
            }
        )
    normalized.sort(key=operator.itemgetter('path'))
    if 'SKILL.md' not in seen_paths:
        raise ClawHubUpstreamError('ClawHub 版本缺少 SKILL.md')
    if sum(item['size'] for item in normalized) > MAX_CLAWHUB_TOTAL_SIZE:
        raise ClawHubUpstreamError('ClawHub 版本文件总大小超过限制')
    return normalized


def _manifest_content_hash(files: list[dict[str, Any]]) -> str:
    digest = hashlib.sha256()
    for item in files:
        digest.update(f"{item['path']}:{item['sha256']}\n".encode())
    return digest.hexdigest()


class ClawHubSyncService:
    """把 ClawHub 权威元数据投影到唤星技能目录。"""

    def __init__(self) -> None:
        self.clawhub_api_url = getattr(settings, 'CLAWHUB_API_URL', 'https://clawhub.ai/api/v1')
        self.sync_filters = {
            'official_only': False,
            'limit': getattr(settings, 'MARKETPLACE_CLAWHUB_SYNC_LIMIT', 100),
            'min_downloads': getattr(settings, 'MARKETPLACE_CLAWHUB_MIN_DOWNLOADS', 0),
        }

    async def sync_from_clawhub(
        self,
        db: AsyncSession,
        force: bool = False,
        skill_ids: list[str] | None = None,
        limit: int | None = None,
        min_downloads: int | None = None,
        dry_run: bool = False,
        translate_body: bool = True,
        batch_commit_size: int = 50,
        resume: bool = False,
        require_engagement: bool = False,
    ) -> dict[str, Any]:
        """同步 ClawHub 元数据。

        ``translate_body`` 为兼容旧调用签名保留；元数据联邦不下载 ``SKILL.md``，因此不再
        翻译正文。版本变化时只读取版本 JSON 文件清单并更新上游下载 URL。
        """
        del translate_body
        effective_limit = self.sync_filters['limit'] if limit is None else limit
        effective_min_downloads = (
            self.sync_filters['min_downloads'] if min_downloads is None else min_downloads
        )
        if dry_run:
            skills_data = (
                await self._fetch_specific_skills(skill_ids)
                if skill_ids
                else await self._fetch_all_skills(limit=effective_limit)
            )
            filtered_skills = self._filter_skills(
                skills_data,
                effective_limit,
                effective_min_downloads,
                require_engagement=require_engagement,
            )
            return self._build_dry_run_report(
                total_fetched=len(skills_data),
                filtered=filtered_skills,
                min_downloads=effective_min_downloads,
                limit=effective_limit,
            )

        sync_log = await marketplace_sync_log_dao.create(
            db,
            CreateMarketplaceSyncLogParam(
                sync_type='clawhub',
                status='in_progress',
                started_at=timezone.now(),
            ),
        )
        await db.flush()
        sync_log_id = sync_log.id if sync_log else None
        if sync_log_id is None:
            latest_log = (
                await db.execute(
                    select(MarketplaceSyncLog).order_by(MarketplaceSyncLog.id.desc()).limit(1)
                )
            ).scalar_one_or_none()
            sync_log_id = latest_log.id if latest_log else None
        if sync_log_id is None:
            raise errors.ServerError(msg='创建 ClawHub 同步日志失败')
        await db.commit()

        try:
            skills_data = (
                await self._fetch_specific_skills(skill_ids)
                if skill_ids
                else await self._fetch_all_skills(limit=effective_limit)
            )
            filtered_skills = self._filter_skills(
                skills_data,
                effective_limit,
                effective_min_downloads,
                require_engagement=require_engagement,
            )
            outcome = await self._sync_filtered_skills(
                db,
                filtered_skills,
                force=force,
                batch_commit_size=batch_commit_size,
                resume=resume,
            )
            status = 'success' if outcome['failed'] == 0 else 'partial'
            await marketplace_sync_log_dao.update(
                db,
                sync_log_id,
                UpdateMarketplaceSyncLogParam(
                    status=status,
                    items_synced=outcome['synced'],
                    items_failed=outcome['failed'],
                    error_message='\n'.join(outcome['errors']) if outcome['errors'] else None,
                    completed_at=timezone.now(),
                ),
            )
            await db.commit()
            return {
                'success': True,
                'mode': 'metadata_only',
                'synced': outcome['synced'],
                'failed': outcome['failed'],
                'skipped_existing': outcome['skipped_existing'],
                'skipped_unchanged': outcome['skipped_unchanged'],
                'errors': outcome['errors'],
            }
        except Exception as exc:
            await db.rollback()
            try:
                await marketplace_sync_log_dao.update(
                    db,
                    sync_log_id,
                    UpdateMarketplaceSyncLogParam(
                        status='failed',
                        error_message=str(exc),
                        completed_at=timezone.now(),
                    ),
                )
                await db.commit()
            except Exception as log_exc:
                await db.rollback()
                log.error(f'ClawHub 同步失败且同步日志回写失败: {log_exc}')
            raise

    async def _sync_filtered_skills(
        self,
        db: AsyncSession,
        filtered_skills: list[dict[str, Any]],
        *,
        force: bool = False,
        batch_commit_size: int = 50,
        resume: bool = False,
    ) -> dict[str, Any]:
        """按稳定身份增量落库，不接触服务器技能文件系统。"""
        existing_by_slug = await self._existing_clawhub_skills_by_slug(db)
        all_existing = [
            row
            for rows in existing_by_slug.values()
            for row in rows
        ]
        latest_versions = await self._latest_versions_by_skill_id(
            db,
            [row.skill_id for row in all_existing],
        )

        process: list[tuple[dict[str, Any], MarketplaceSkill | None]] = []
        unchanged: list[tuple[dict[str, Any], MarketplaceSkill]] = []
        skipped_existing = 0
        for skill_data in filtered_skills:
            existing = self._match_existing(skill_data, existing_by_slug)
            if resume and existing is not None:
                skipped_existing += 1
                continue
            if (
                not force
                and existing is not None
                and self._is_version_unchanged(existing, skill_data, latest_versions)
            ):
                unchanged.append((skill_data, existing))
            else:
                process.append((skill_data, existing))

        failed = 0
        errors_found: list[str] = []
        for skill_data, existing in unchanged:
            try:
                async with db.begin_nested():
                    await self._refresh_engagement(db, existing, skill_data, timezone.now())
            except Exception as exc:
                failed += 1
                errors_found.append(f"{_skill_key(skill_data)}(refresh): {exc!s}")
                log.warning(f'ClawHub 人气元数据刷新失败: {_skill_key(skill_data)}: {exc}')

        process, prepare_errors = await self._prepare_distribution_batch(process)
        failed += len(prepare_errors)
        errors_found.extend(prepare_errors)
        existing_by_key = {
            _skill_key(
                {
                    'slug': row.slug,
                    'ownerHandle': row.author_name,
                }
            ): row
            for row in all_existing
            if row.slug and row.author_name
        }
        for skill_data, existing in process:
            if existing is not None:
                existing_by_key.setdefault(_skill_key(skill_data), existing)
        prepared_map = await self._batch_prepare_metadata(
            [skill_data for skill_data, _ in process],
            existing_by_key,
            force=force,
        )

        synced = 0
        commit_size = max(1, batch_commit_size)
        for index, (skill_data, existing) in enumerate(process):
            key = _skill_key(skill_data)
            try:
                async with db.begin_nested():
                    await self._sync_skill(
                        db,
                        skill_data,
                        existing=existing,
                        prepared=prepared_map.get(key),
                    )
                synced += 1
            except Exception as exc:
                failed += 1
                errors_found.append(f'{key or "unknown"}: {exc!s}')
                log.warning(f'ClawHub 技能元数据同步失败: {key}: {exc}')

            if (index + 1) % commit_size == 0:
                await db.commit()
                log.info(
                    f'ClawHub 元数据同步进度 {index + 1}/{len(process)}，'
                    f'synced={synced} failed={failed}'
                )

        return {
            'synced': synced,
            'failed': failed,
            'skipped_existing': skipped_existing,
            'skipped_unchanged': len(unchanged),
            'errors': errors_found,
        }

    async def _prepare_distribution_batch(
        self,
        process: list[tuple[dict[str, Any], MarketplaceSkill | None]],
    ) -> tuple[
        list[tuple[dict[str, Any], MarketplaceSkill | None]],
        list[str],
    ]:
        """并发解析作者和版本 JSON，避免全量目录按技能串行等待外网。"""
        if not process:
            return [], []
        concurrency = max(
            1,
            int(getattr(settings, 'MARKETPLACE_CLAWHUB_METADATA_CONCURRENCY', 8) or 8),
        )
        semaphore = asyncio.Semaphore(concurrency)
        timeout = httpx.Timeout(30.0, connect=10.0)
        limits = httpx.Limits(
            max_connections=concurrency,
            max_keepalive_connections=concurrency,
        )

        async with httpx.AsyncClient(timeout=timeout, limits=limits) as client:
            async def prepare(
                item: tuple[dict[str, Any], MarketplaceSkill | None],
            ) -> tuple[dict[str, Any], MarketplaceSkill | None]:
                async with semaphore:
                    skill_data, existing = item
                    return (
                        await self._prepare_distribution_metadata(
                            client,
                            skill_data,
                            existing,
                        ),
                        existing,
                    )

            results = await asyncio.gather(
                *(prepare(item) for item in process),
                return_exceptions=True,
            )

        prepared: list[tuple[dict[str, Any], MarketplaceSkill | None]] = []
        errors_found: list[str] = []
        for item, result in zip(process, results, strict=True):
            if isinstance(result, BaseException):
                key = _skill_key(item[0]) or str(item[0].get('slug') or 'unknown')
                errors_found.append(f'{key}(metadata): {result!s}')
                log.warning(f'ClawHub 分发元数据读取失败: {key}: {result}')
                continue
            prepared.append(result)
        return prepared, errors_found

    async def _prepare_distribution_metadata(
        self,
        client: httpx.AsyncClient,
        skill_data: dict[str, Any],
        existing: MarketplaceSkill | None,
    ) -> dict[str, Any]:
        """为单个技能补齐稳定作者和版本清单，不下载 ZIP。"""
        enriched = dict(skill_data)
        slug = str(enriched.get('slug') or '').strip()
        if not slug:
            raise ClawHubIdentityError('ClawHub 技能缺少 slug')
        owner_handle = _owner_from_skill(enriched)
        if not owner_handle and existing and existing.author_name not in {None, '', 'community'}:
            owner_handle = existing.author_name
        if not owner_handle:
            try:
                detail = await self._fetch_skill_detail(client, slug=slug)
            except ClawHubAmbiguousSkillError as exc:
                detail = await self._resolve_ranked_ambiguous_detail(
                    client,
                    listed_skill=enriched,
                    ambiguity=exc,
                )
            owner_handle = self._extract_owner_handle(detail)
            if not owner_handle:
                raise ClawHubIdentityError(f'ClawHub 技能详情缺少作者: {slug}')
            enriched.update(detail.get('skill') or {})
            enriched['owner'] = detail.get('owner') or {'handle': owner_handle}
            enriched['latestVersion'] = detail.get('latestVersion') or enriched.get(
                'latestVersion'
            )
        enriched['ownerHandle'] = owner_handle
        if owner_handle == 'community':
            raise ClawHubIdentityError(f'ClawHub 技能作者不能是伪造值 community: {slug}')

        latest_version = enriched.get('latestVersion')
        if not isinstance(latest_version, dict) or not latest_version.get('version'):
            raise ClawHubUpstreamError(f'ClawHub 技能缺少最新版本: {owner_handle}/{slug}')
        version = str(latest_version['version'])
        enriched['_distribution_version'] = await self._fetch_version_metadata(
            owner_handle=owner_handle,
            slug=slug,
            version=version,
            client=client,
        )
        return enriched

    async def _existing_clawhub_skills_by_slug(
        self,
        db: AsyncSession,
    ) -> dict[str, list[MarketplaceSkill]]:
        """按 slug 分组读取存量，保留同名不同作者的全部记录。"""
        stmt = (
            select(MarketplaceSkill)
            .options(
                load_only(
                    MarketplaceSkill.id,
                    MarketplaceSkill.skill_id,
                    MarketplaceSkill.slug,
                    MarketplaceSkill.namespace,
                    MarketplaceSkill.name,
                    MarketplaceSkill.name_en,
                    MarketplaceSkill.name_zh,
                    MarketplaceSkill.description_en,
                    MarketplaceSkill.description_zh,
                    MarketplaceSkill.files,
                    MarketplaceSkill.source_language,
                    MarketplaceSkill.tags_en,
                    MarketplaceSkill.tags_zh,
                    MarketplaceSkill.emoji,
                    MarketplaceSkill.category,
                    MarketplaceSkill.author_name,
                )
            )
            .where(MarketplaceSkill.source_type == 'clawhub')
        )
        rows = (await db.execute(stmt)).scalars().all()
        grouped: dict[str, list[MarketplaceSkill]] = defaultdict(list)
        for row in rows:
            if row.slug:
                grouped[row.slug].append(row)
        return dict(grouped)

    @staticmethod
    def _match_existing(
        skill_data: dict[str, Any],
        existing_by_slug: dict[str, list[MarketplaceSkill]],
    ) -> MarketplaceSkill | None:
        slug = str(skill_data.get('slug') or '')
        rows = existing_by_slug.get(slug, [])
        owner_handle = _owner_from_skill(skill_data)
        if owner_handle:
            for row in rows:
                if row.author_name == owner_handle or row.namespace == f'clawhub/{owner_handle}':
                    return row
            legacy = [row for row in rows if row.author_name in {None, '', 'community'}]
            return legacy[0] if len(legacy) == 1 else None
        return rows[0] if len(rows) == 1 else None

    async def _latest_versions_by_skill_id(
        self,
        db: AsyncSession,
        skill_ids: list[str],
    ) -> dict[str, str]:
        if not skill_ids:
            return {}
        stmt = select(
            MarketplaceSkillVersion.skill_id,
            MarketplaceSkillVersion.version,
        ).where(
            MarketplaceSkillVersion.skill_id.in_(skill_ids),
            MarketplaceSkillVersion.is_latest.is_(True),
        )
        return {
            skill_id: version
            for skill_id, version in (await db.execute(stmt)).all()
            if skill_id and version
        }

    @staticmethod
    def _is_version_unchanged(
        existing: MarketplaceSkill,
        skill_data: dict[str, Any],
        latest_versions: dict[str, str],
        body_skill_ids: set[str] | None = None,
    ) -> bool:
        """版本和目录元数据都未变化时跳过。

        ``body_skill_ids`` 仅为兼容旧调用签名保留。元数据模式不再以服务器正文或
        ``repo_path`` 作为完整性条件。
        """
        del body_skill_ids
        upstream_version = (skill_data.get('latestVersion') or {}).get('version')
        if not upstream_version or latest_versions.get(existing.skill_id) != upstream_version:
            return False
        if not ClawHubSyncService._has_verified_file_manifest(existing.files):
            return False
        owner_handle = _owner_from_skill(skill_data) or existing.author_name
        if not owner_handle or owner_handle == 'community':
            return False
        scanned = {
            'name': skill_data.get('displayName') or skill_data.get('slug') or '',
            'description': skill_data.get('summary') or '',
        }
        source_language = existing.source_language or 'en'
        source_description = (
            existing.description_zh
            if source_language == 'zh'
            else existing.description_en
        )
        return (
            (existing.name or '') == scanned['name']
            and (source_description or '') == scanned['description']
        )

    @staticmethod
    def _has_verified_file_manifest(value: Any) -> bool:
        """判断存量文件清单是否已包含可校验的逐文件 SHA256。"""
        if isinstance(value, str):
            try:
                value = json.loads(value)
            except json.JSONDecodeError:
                return False
        if not isinstance(value, list) or not value:
            return False
        for item in value:
            if not isinstance(item, dict):
                return False
            sha256 = str(item.get('sha256') or '').lower()
            if (
                not item.get('path')
                or len(sha256) != 64
                or any(char not in '0123456789abcdef' for char in sha256)
            ):
                return False
        return True

    async def _refresh_engagement(
        self,
        db: AsyncSession,
        existing: MarketplaceSkill,
        skill_data: dict[str, Any],
        now: datetime,
    ) -> None:
        stats = skill_data.get('stats') or {}
        await marketplace_skill_dao.update_model(
            db,
            existing.id,
            {
                'download_count': int(stats.get('downloads') or 0),
                'star_count': int(stats.get('stars') or 0),
                'synced_at': now,
            },
        )

    async def _batch_prepare_metadata(
        self,
        skills: list[dict[str, Any]],
        existing_by_key: dict[str, Any],
        *,
        force: bool = False,
    ) -> dict[str, dict[str, Any]]:
        """只翻译新增或元数据变化的技能，键使用 ``owner/slug`` 消除重名碰撞。"""
        if not skills:
            return {}
        prepared: dict[str, dict[str, Any]] = {}
        pending_items: list[dict[str, Any]] = []
        pending_keys: list[str] = []
        for skill in skills:
            key = _skill_key(skill)
            if not key:
                continue
            existing = existing_by_key.get(key)
            if existing is None:
                existing = existing_by_key.get(str(skill.get('slug') or ''))
            scanned = {
                'name': skill.get('displayName') or skill.get('slug') or '',
                'description': skill.get('summary') or '',
            }
            if not force and existing is not None and metadata_unchanged(scanned, existing):
                prepared[key] = translation_from_existing(existing)
                continue
            pending_keys.append(key)
            pending_items.append(
                {
                    'name': scanned['name'],
                    'description': scanned['description'],
                    'tag_hints': self._extract_tag_hints(skill),
                    'source_lang': None,
                }
            )

        if pending_items:
            batch_size = int(getattr(settings, 'TRANSLATION_BATCH_SIZE', 10) or 10)
            concurrency = int(
                getattr(settings, 'MARKETPLACE_CLAWHUB_TRANSLATE_CONCURRENCY', 3) or 3
            )
            results = await translation_service.batch_translate_skill_metadata(
                pending_items,
                batch_size=batch_size,
                concurrency=concurrency,
            )
            if len(results) != len(pending_keys):
                raise errors.ServerError(msg='ClawHub 技能元数据翻译结果数量不完整')
            prepared.update(dict(zip(pending_keys, results, strict=True)))

        log.info(
            f'ClawHub 元数据翻译：{len(pending_items)} 个需翻译，'
            f'{len(skills) - len(pending_items)} 个复用缓存'
        )
        return prepared

    async def _fetch_all_skills(
        self,
        *,
        limit: int | None = None,
    ) -> list[dict[str, Any]]:
        """按下载量降序分页，达到调用方 top-N 后立即停止枚举。"""
        skills: list[dict[str, Any]] = []
        cursor: str | None = None
        page_size = 100
        seen_cursors: set[str] = set()
        max_pages = 2000
        limits = httpx.Limits(max_keepalive_connections=0)
        timeout = httpx.Timeout(30.0, connect=10.0)
        async with httpx.AsyncClient(timeout=timeout, limits=limits) as client:
            for page_index in range(max_pages):
                params: dict[str, Any] = {
                    'limit': page_size,
                    'nonSuspiciousOnly': 'true',
                    'sort': 'downloads',
                }
                if cursor:
                    params['cursor'] = cursor
                data: dict[str, Any] | None = None
                last_error: Exception | None = None
                for attempt in range(3):
                    try:
                        response = await asyncio.wait_for(
                            client.get(f'{self.clawhub_api_url}/skills', params=params),
                            timeout=25.0,
                        )
                        response.raise_for_status()
                        payload = response.json()
                        if not isinstance(payload, dict):
                            raise ClawHubUpstreamError('ClawHub 技能列表响应不是对象')
                        data = payload
                        break
                    except Exception as exc:
                        last_error = exc
                        log.warning(
                            f'ClawHub 枚举第 {page_index + 1} 页失败，'
                            f'第 {attempt + 1}/3 次尝试: {type(exc).__name__}: {exc}'
                        )
                        if attempt < 2:
                            await asyncio.sleep(2.0 * (attempt + 1))
                if data is None:
                    raise ClawHubUpstreamError(
                        f'ClawHub 枚举第 {page_index + 1} 页连续失败: {last_error}'
                    )
                items = data.get('items')
                if not isinstance(items, list):
                    raise ClawHubUpstreamError('ClawHub 技能列表缺少 items 数组')
                if not items:
                    break
                skills.extend(item for item in items if isinstance(item, dict))
                if limit is not None and limit > 0 and len(skills) >= limit:
                    skills = skills[:limit]
                    break
                cursor = data.get('nextCursor')
                if not isinstance(cursor, str) or not cursor or cursor in seen_cursors:
                    break
                seen_cursors.add(cursor)
            else:
                raise ClawHubUpstreamError(f'ClawHub 技能列表超过安全页数上限 {max_pages}')

            expanded = await self._expand_duplicate_slugs(client, skills)
        log.info(
            f'ClawHub 枚举完成：{len(skills)} 条列表记录'
            f'（limit={limit if limit is not None else "all"}），'
            f'{len(expanded)} 个稳定身份'
        )
        return expanded

    async def _expand_duplicate_slugs(
        self,
        client: httpx.AsyncClient,
        skills: list[dict[str, Any]],
    ) -> list[dict[str, Any]]:
        """对列表中重复 slug 调搜索和带 owner 的详情接口，得到稳定身份。"""
        counts = Counter(
            str(skill.get('slug') or '')
            for skill in skills
            if skill.get('slug')
        )
        duplicate_slugs = {slug for slug, count in counts.items() if count > 1}
        if not duplicate_slugs:
            return skills

        expanded = [
            skill
            for skill in skills
            if str(skill.get('slug') or '') not in duplicate_slugs
        ]
        base_by_slug = {
            slug: next(skill for skill in skills if skill.get('slug') == slug)
            for slug in duplicate_slugs
        }
        for slug in sorted(duplicate_slugs):
            response = await client.get(
                f'{self.clawhub_api_url}/search',
                params={
                    'q': slug,
                    'limit': 100,
                    'nonSuspiciousOnly': 'true',
                },
            )
            response.raise_for_status()
            payload = response.json()
            results = payload.get('results') if isinstance(payload, dict) else None
            if not isinstance(results, list):
                raise ClawHubIdentityError(f'ambiguous slug {slug!r} 的搜索响应无 results')
            owners = sorted(
                {
                    str(result.get('ownerHandle')).strip()
                    for result in results
                    if isinstance(result, dict)
                    and result.get('slug') == slug
                    and result.get('ownerHandle')
                }
            )
            if not owners:
                raise ClawHubIdentityError(f'ambiguous slug {slug!r} 无法解析作者')
            if len(owners) >= 100:
                raise ClawHubIdentityError(f'ambiguous slug {slug!r} 作者数达到搜索上限')

            for owner_handle in owners:
                detail = await self._fetch_skill_detail(
                    client,
                    slug=slug,
                    owner_handle=owner_handle,
                )
                merged = dict(base_by_slug[slug])
                merged.update(detail.get('skill') or {})
                merged['owner'] = detail.get('owner') or {'handle': owner_handle}
                merged['ownerHandle'] = owner_handle
                merged['latestVersion'] = detail.get('latestVersion') or merged.get(
                    'latestVersion'
                )
                expanded.append(merged)
        return expanded

    async def _fetch_skill_detail(
        self,
        client: httpx.AsyncClient,
        *,
        slug: str,
        owner_handle: str | None = None,
    ) -> dict[str, Any]:
        params = {'ownerHandle': owner_handle} if owner_handle else None
        response = await self._get_clawhub_response(
            client,
            f'{self.clawhub_api_url}/skills/{slug}',
            params=params,
            operation=f'读取技能详情 {owner_handle or "unknown"}/{slug}',
        )
        if response.status_code == 409:
            try:
                payload = response.json()
            except ValueError:
                payload = {}
            matches = payload.get('matches') if isinstance(payload, dict) else None
            owners = [
                str(match.get('ownerHandle')).strip()
                for match in matches or []
                if isinstance(match, dict) and match.get('ownerHandle')
            ]
            if owners:
                raise ClawHubAmbiguousSkillError(slug, owners)
            raise ClawHubIdentityError(f'ambiguous slug {slug!r}，必须提供 ownerHandle')
        response.raise_for_status()
        payload = response.json()
        if not isinstance(payload, dict):
            raise ClawHubUpstreamError(f'ClawHub 技能详情响应不是对象: {slug}')
        moderation = payload.get('moderation')
        if isinstance(moderation, dict) and (
            moderation.get('isMalwareBlocked') is True
            or moderation.get('verdict') == 'malicious'
        ):
            raise ClawHubUpstreamError(f'ClawHub 技能被标记为恶意: {slug}')
        return payload

    @staticmethod
    async def _get_clawhub_response(
        client: httpx.AsyncClient,
        url: str,
        *,
        params: dict[str, Any] | None,
        operation: str,
    ) -> httpx.Response:
        """对上游 429/5xx 和网络瞬时错误做有限重试。"""
        last_error: Exception | None = None
        for attempt in range(3):
            try:
                response = await client.get(url, params=params)
            except httpx.RequestError as exc:
                last_error = exc
            else:
                if response.status_code != 429 and response.status_code < 500:
                    return response
                last_error = httpx.HTTPStatusError(
                    f'{operation} 收到瞬时 HTTP {response.status_code}',
                    request=response.request,
                    response=response,
                )
            log.warning(
                f'ClawHub {operation} 失败，第 {attempt + 1}/3 次尝试: '
                f'{type(last_error).__name__}: {last_error}'
            )
            if attempt < 2:
                await asyncio.sleep(float(attempt + 1))
        assert last_error is not None
        raise last_error

    async def _resolve_ranked_ambiguous_detail(
        self,
        client: httpx.AsyncClient,
        *,
        listed_skill: dict[str, Any],
        ambiguity: ClawHubAmbiguousSkillError,
    ) -> dict[str, Any]:
        """用榜单元数据精确匹配 409 返回的具体作者，不做任意回落。"""
        candidates = [
            await self._fetch_skill_detail(
                client,
                slug=ambiguity.slug,
                owner_handle=owner_handle,
            )
            for owner_handle in ambiguity.owner_handles
        ]
        listed_version = str(
            (listed_skill.get('latestVersion') or {}).get('version') or ''
        )
        listed_stats = listed_skill.get('stats') or {}
        listed_downloads = int(listed_stats.get('downloads') or 0)
        listed_stars = int(listed_stats.get('stars') or 0)
        matched: list[dict[str, Any]] = []
        for candidate in candidates:
            skill = candidate.get('skill') or {}
            latest_version = candidate.get('latestVersion') or {}
            stats = skill.get('stats') or {}
            if (
                str(latest_version.get('version') or '') == listed_version
                and int(stats.get('downloads') or 0) == listed_downloads
                and int(stats.get('stars') or 0) == listed_stars
                and str(skill.get('displayName') or '')
                == str(listed_skill.get('displayName') or '')
                and str(skill.get('summary') or '')
                == str(listed_skill.get('summary') or '')
            ):
                matched.append(candidate)
        if len(matched) != 1:
            raise ClawHubIdentityError(
                f'ambiguous slug {ambiguity.slug!r} 的榜单记录无法唯一匹配作者'
            )
        return matched[0]

    async def _fetch_specific_skills(
        self,
        skill_ids: list[str],
    ) -> list[dict[str, Any]]:
        """按 ``owner/slug`` 或裸 slug 读取指定技能；歧义必须显式失败。"""
        skills: list[dict[str, Any]] = []
        async with httpx.AsyncClient(timeout=30.0) as client:
            for locator in skill_ids:
                parts = [part for part in locator.strip('/').split('/') if part]
                if parts[:1] == ['clawhub']:
                    parts = parts[1:]
                owner_handle = parts[-2] if len(parts) >= 2 else None
                slug = parts[-1] if parts else ''
                if not slug:
                    raise ClawHubIdentityError(f'ClawHub 技能定位符无效: {locator!r}')
                detail = await self._fetch_skill_detail(
                    client,
                    slug=slug,
                    owner_handle=owner_handle,
                )
                resolved_owner = self._extract_owner_handle(detail)
                if not resolved_owner:
                    raise ClawHubIdentityError(f'ClawHub 技能详情缺少作者: {locator!r}')
                skill = dict(detail.get('skill') or {})
                skill['owner'] = detail.get('owner') or {'handle': resolved_owner}
                skill['ownerHandle'] = resolved_owner
                skill['latestVersion'] = detail.get('latestVersion') or {}
                skills.append(skill)
        return skills

    async def _get_skill_owner(self, slug: str) -> str:
        """读取唯一作者；禁止用 ``community`` 伪造失败结果。"""
        async with httpx.AsyncClient(timeout=30.0) as client:
            detail = await self._fetch_skill_detail(client, slug=slug)
        owner_handle = self._extract_owner_handle(detail)
        if not owner_handle:
            raise ClawHubIdentityError(f'ClawHub 技能 {slug!r} 缺少作者')
        return owner_handle

    @staticmethod
    def _extract_owner_handle(data: dict[str, Any]) -> str | None:
        owner = data.get('owner')
        if isinstance(owner, dict) and owner.get('handle'):
            return str(owner['handle'])
        skill = data.get('skill')
        if isinstance(skill, dict) and skill.get('ownerHandle'):
            return str(skill['ownerHandle'])
        return None

    async def _fetch_version_metadata(
        self,
        *,
        owner_handle: str,
        slug: str,
        version: str,
        client: httpx.AsyncClient | None = None,
    ) -> dict[str, Any]:
        """读取版本文件清单并派生内容指纹，不下载 ZIP。"""
        owns_client = client is None
        if client is None:
            client = httpx.AsyncClient(timeout=30.0)
        try:
            response = await self._get_clawhub_response(
                client,
                f'{self.clawhub_api_url}/skills/{slug}/versions/{version}',
                params={'ownerHandle': owner_handle},
                operation=f'读取技能版本 {owner_handle}/{slug}@{version}',
            )
            response.raise_for_status()
            payload = response.json()
        finally:
            if owns_client:
                await client.aclose()
        version_data = payload.get('version') if isinstance(payload, dict) else None
        if not isinstance(version_data, dict):
            raise ClawHubUpstreamError(f'ClawHub 版本响应缺少 version: {owner_handle}/{slug}')
        returned_version = str(version_data.get('version') or '')
        if returned_version != version:
            raise ClawHubUpstreamError(
                f'ClawHub 版本响应不一致: 请求 {version}，返回 {returned_version!r}'
            )
        security = version_data.get('security')
        if isinstance(security, dict) and security.get('verdict') == 'malicious':
            raise ClawHubUpstreamError(f'ClawHub 版本被标记为恶意: {owner_handle}/{slug}@{version}')
        files = _normalize_file_manifest(version_data.get('files'))
        return {
            **version_data,
            'files': files,
            'file_size': sum(item['size'] for item in files),
            'content_hash': _manifest_content_hash(files),
            'package_url': build_clawhub_download_url(
                self.clawhub_api_url,
                owner_handle=owner_handle,
                slug=slug,
                version=version,
            ),
        }

    async def _sync_skill(
        self,
        db: AsyncSession,
        clawhub_skill: dict[str, Any],
        *,
        existing: MarketplaceSkill | None = None,
        prepared: dict[str, Any] | None = None,
    ) -> None:
        """同步一个稳定 ``owner/slug`` 身份的目录与最新版本元数据。"""
        slug = str(clawhub_skill.get('slug') or '').strip()
        if not slug:
            raise ClawHubIdentityError('ClawHub 技能缺少 slug')
        owner_handle = _owner_from_skill(clawhub_skill)
        if not owner_handle and existing and existing.author_name not in {None, '', 'community'}:
            owner_handle = existing.author_name
        if not owner_handle:
            owner_handle = await self._get_skill_owner(slug)
        if owner_handle == 'community':
            raise ClawHubIdentityError(f'ClawHub 技能作者不能是伪造值 community: {slug}')

        latest_version = clawhub_skill.get('latestVersion')
        if not isinstance(latest_version, dict) or not latest_version.get('version'):
            raise ClawHubUpstreamError(f'ClawHub 技能缺少最新版本: {owner_handle}/{slug}')
        version = str(latest_version['version'])
        version_metadata = clawhub_skill.get('_distribution_version')
        if not isinstance(version_metadata, dict):
            version_metadata = await self._fetch_version_metadata(
                owner_handle=owner_handle,
                slug=slug,
                version=version,
            )

        skill_id = f'clawhub/{owner_handle}/{slug}'
        namespace = f'clawhub/{owner_handle}'
        if existing is not None and existing.skill_id != skill_id:
            if existing.author_name not in {None, '', 'community'}:
                raise ClawHubIdentityError(
                    f'ClawHub 存量身份与上游冲突: {existing.skill_id} -> {skill_id}'
                )
            target = await marketplace_skill_dao.get_by_id(db, skill_id)
            if target is not None:
                existing.status = 'unpublished'
                existing.visibility = 'private'
                existing.is_private = True
                existing.is_common = False
                existing = target
            else:
                await db.execute(
                    sa.update(MarketplaceSkillVersion)
                    .where(MarketplaceSkillVersion.skill_id == existing.skill_id)
                    .values(skill_id=skill_id)
                )

        name = _bounded_catalog_text(
            clawhub_skill.get('displayName') or slug,
            200,
        ) or slug
        description = str(clawhub_skill.get('summary') or '')
        translated = prepared or await translation_service.translate_skill_metadata(
            name=name,
            description=description,
            tag_hints=self._extract_tag_hints(clawhub_skill),
        )
        name_en, name_zh, description_en, description_zh = self._bilingual_metadata(
            translated,
            name=name,
            description=description,
        )
        name_en = _bounded_catalog_text(name_en, 200)
        name_zh = _bounded_catalog_text(name_zh, 200)
        tags_en = translation_service.normalize_tag_list(translated.get('tags_en'))
        tags_zh = translation_service.normalize_tag_list(translated.get('tags_zh'))
        tags = tags_en or tags_zh or self._extract_tag_hints(clawhub_skill) or [slug]
        stats = clawhub_skill.get('stats') or {}
        category = await self._classify_skill(db, name, description)
        source_page_query = urlencode({'ownerHandle': owner_handle})
        record = {
            'skill_id': skill_id,
            'namespace': namespace,
            'slug': slug,
            'status': 'published',
            'visibility': 'public',
            'name': name,
            'name_en': name_en,
            'name_zh': name_zh,
            'description_en': description_en,
            'description_zh': description_zh,
            'body_en': None,
            'body_zh': None,
            'files': json.dumps(version_metadata['files'], ensure_ascii=False),
            'source_language': translated.get('source_language') or 'en',
            'icon_url': None,
            'emoji': translated.get('emoji'),
            'author_name': owner_handle,
            'category': category,
            'tags': json.dumps(tags, ensure_ascii=False),
            'tags_en': json.dumps(tags_en or tags, ensure_ascii=False),
            'tags_zh': json.dumps(tags_zh or tags, ensure_ascii=False),
            'source_type': 'clawhub',
            'source_repo_url': f'https://clawhub.ai/skills/{slug}?{source_page_query}',
            'source_repo_path': None,
            'repo_path': None,
            'pricing_type': 'free',
            'price': 0,
            'is_private': False,
            'is_official': False,
            'is_common': False,
            'download_count': int(stats.get('downloads') or 0),
            'star_count': int(stats.get('stars') or 0),
            'git_commit_hash': None,
            'synced_at': timezone.now(),
            'translated_at': timezone.now(),
        }
        if existing:
            await marketplace_skill_dao.update(
                db,
                existing.id,
                UpdateMarketplaceSkillParam(**record),
            )
        else:
            await marketplace_skill_dao.create(
                db,
                CreateMarketplaceSkillParam(**record),
            )
            await db.flush()
            if await marketplace_skill_dao.get_by_id(db, skill_id) is None:
                raise errors.ServerError(msg=f'ClawHub 技能落库失败: {skill_id}')

        await self._sync_skill_version(
            db,
            skill_id,
            {
                **latest_version,
                **version_metadata,
            },
        )

    async def _sync_skill_version(
        self,
        db: AsyncSession,
        skill_id: str,
        version_data: dict[str, Any],
    ) -> None:
        """幂等写入最新版本，并保证同一技能只有一个 ``is_latest``。"""
        version = str(version_data.get('version') or '')
        if not version:
            raise ClawHubUpstreamError(f'ClawHub 技能版本为空: {skill_id}')
        existing_version = await marketplace_skill_version_dao.get_by_skill_and_version(
            db,
            skill_id,
            version,
        )
        created_at = version_data.get('createdAt')
        if isinstance(created_at, int | float):
            released_at = datetime.fromtimestamp(created_at / 1000, tz=timezone.now().tzinfo)
        else:
            released_at = timezone.now()
        record = {
            'skill_id': skill_id,
            'version': version,
            'changelog': version_data.get('changelog') or None,
            'package_url': version_data.get('package_url'),
            'file_hash': None,
            'content_hash': version_data.get('content_hash'),
            'file_size': version_data.get('file_size'),
            'is_latest': True,
            'published_at': released_at,
        }
        await db.execute(
            sa.update(MarketplaceSkillVersion)
            .where(MarketplaceSkillVersion.skill_id == skill_id)
            .values(is_latest=False)
        )
        if existing_version:
            await marketplace_skill_version_dao.update(
                db,
                existing_version.id,
                UpdateMarketplaceSkillVersionParam(**record),
            )
        else:
            await marketplace_skill_version_dao.create(
                db,
                CreateMarketplaceSkillVersionParam(**record),
            )

    @staticmethod
    def _bilingual_metadata(
        translated: dict[str, Any],
        *,
        name: str,
        description: str,
    ) -> tuple[str | None, str | None, str | None, str | None]:
        name_en = translated.get('name_en')
        name_zh = translated.get('name_zh')
        description_en = translated.get('description_en')
        description_zh = translated.get('description_zh')
        source_language = translated.get('source_language')
        if source_language == 'zh':
            name_zh, description_zh = name, description
        elif source_language == 'en':
            name_en, description_en = name, description
        return name_en, name_zh, description_en, description_zh

    async def _classify_skill(
        self,
        db: AsyncSession,
        name: str,
        description: str,
    ) -> str:
        categories = await marketplace_category_dao.get_all(db)
        category_slugs = [category.slug for category in categories]
        return normalize_category(
            self._map_category_from_text(f'{name} {description}', category_slugs)
        ) or DEFAULT_CATEGORY

    @staticmethod
    def _map_category_from_text(
        text: str,
        available_categories: list[str],
    ) -> str:
        text_lower = text.lower()
        keyword_map = {
            'finance': [
                'finance',
                'invest',
                'stock',
                'trading',
                'crypto',
                'payment',
                '理财',
                '金融',
                '股票',
            ],
            'health': ['health', 'medical', 'fitness', 'diet', 'wellness', '医疗', '健康', '健身'],
            'development': [
                'code',
                'programming',
                'development',
                'git',
                'debug',
                'api',
                'sdk',
                'compiler',
            ],
            'data-analysis': ['data', 'analysis', 'analytics', 'database', 'sql', 'statistics', '数据'],
            'media': [
                'video',
                'image',
                'photo',
                'picture',
                'audio',
                'music',
                'sound',
                'media',
                'multimedia',
                'film',
            ],
            'content-creation': [
                'content',
                'writing',
                'write',
                'blog',
                'article',
                'copywriting',
                '文案',
                '写作',
            ],
            'creativity': ['creative', 'design', 'art', 'draw', 'illustration', '设计', '创意'],
            'search': ['search', 'retrieval', 'lookup', 'index', '检索', '搜索'],
            'communication': [
                'chat',
                'communication',
                'message',
                'email',
                'meeting',
                'social',
                '沟通',
                '社交',
            ],
            'ai-assistant': ['llm', 'machine learning', 'assistant', 'chatbot', 'prompt', '助手'],
            'entertainment': ['game', 'entertainment', 'fun', 'play', '娱乐', '游戏'],
            'productivity': [
                'automation',
                'workflow',
                'task',
                'schedule',
                'productivity',
                'efficiency',
                'utility',
                'tool',
                'office',
                '效率',
                '自动化',
                '工具',
            ],
        }
        for category_slug, keywords in keyword_map.items():
            if category_slug not in available_categories:
                continue
            if any(keyword in text_lower for keyword in keywords):
                return category_slug
        if 'other' in available_categories:
            return 'other'
        return available_categories[0] if available_categories else 'other'

    @staticmethod
    def _downloads_of(skill: dict[str, Any]) -> int:
        return int((skill.get('stats') or {}).get('downloads') or 0)

    @staticmethod
    def _stars_of(skill: dict[str, Any]) -> int:
        return int((skill.get('stats') or {}).get('stars') or 0)

    def _filter_skills(
        self,
        skills: list[dict[str, Any]],
        limit: int | None = None,
        min_downloads: int = 0,
        require_engagement: bool = False,
    ) -> list[dict[str, Any]]:
        threshold = max(min_downloads or 0, 0)
        eligible = (
            [skill for skill in skills if self._downloads_of(skill) > threshold]
            if threshold > 0
            else list(skills)
        )
        if require_engagement:
            eligible = [
                skill
                for skill in eligible
                if self._downloads_of(skill) > 0 or self._stars_of(skill) > 0
            ]
        ranked = sorted(
            eligible,
            key=lambda skill: (
                self._downloads_of(skill),
                self._stars_of(skill),
                skill.get('updatedAt') or skill.get('createdAt') or 0,
            ),
            reverse=True,
        )
        return ranked if not limit or limit <= 0 else ranked[:limit]

    def _build_dry_run_report(
        self,
        total_fetched: int,
        filtered: list[dict[str, Any]],
        min_downloads: int,
        limit: int | None,
    ) -> dict[str, Any]:
        return {
            'success': True,
            'dry_run': True,
            'mode': 'metadata_only',
            'min_downloads': min_downloads,
            'limit': limit,
            'total_fetched': total_fetched,
            'matched': len(filtered),
            'estimated_detail_requests': sum(
                1 for skill in filtered if _owner_from_skill(skill) is None
            ),
            'estimated_version_requests': len(filtered),
            'estimated_package_download_bytes': 0,
            'estimated_server_disk_bytes': 0,
            'top_by_downloads': [
                {
                    'skill_id': (
                        f"clawhub/{_owner_from_skill(skill)}/{skill.get('slug')}"
                        if _owner_from_skill(skill)
                        else None
                    ),
                    'slug': skill.get('slug'),
                    'downloads': self._downloads_of(skill),
                }
                for skill in filtered[:10]
            ],
        }

    @staticmethod
    def _extract_tag_hints(clawhub_skill: dict[str, Any]) -> list[str]:
        tags = clawhub_skill.get('tags')
        values: Iterable[Any]
        if isinstance(tags, dict):
            values = tags.keys()
        elif isinstance(tags, list):
            values = tags
        elif isinstance(tags, str):
            values = tags.split(',')
        else:
            values = []
        return translation_service.normalize_tag_list(
            [str(tag).strip() for tag in values if str(tag).strip()]
        )


clawhub_sync_service = ClawHubSyncService()
