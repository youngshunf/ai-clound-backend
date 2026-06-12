"""
ClawHub Sync Service

Syncs skills from ClawHub marketplace to Huanxing marketplace.
"""
import json
import os
import shutil
import zipfile
from datetime import datetime
from pathlib import Path
from typing import Any

import httpx
from sqlalchemy.ext.asyncio import AsyncSession

from backend.app.marketplace.crud.crud_marketplace_skill import marketplace_skill_dao
from backend.app.marketplace.crud.crud_marketplace_skill_version import marketplace_skill_version_dao
from backend.app.marketplace.crud.crud_marketplace_sync_log import marketplace_sync_log_dao
from backend.app.marketplace.schema.marketplace_sync_log import (
    CreateMarketplaceSyncLogParam,
    UpdateMarketplaceSyncLogParam
)
from backend.app.marketplace.service.category_taxonomy import normalize_category
from backend.app.marketplace.service.skill_content_extractor import (
    extract_skill_body,
    list_skill_files,
    resolve_bilingual_body,
)
from backend.app.marketplace.service.translation_service import translation_service
from backend.common.log import log
from backend.core.conf import settings


class ClawHubSyncService:
    """ClawHub sync service for marketplace skills"""

    def __init__(self):
        self.clawhub_api_url = getattr(settings, 'CLAWHUB_API_URL', 'https://clawhub.ai/api/v1')
        self.hub_local_path = Path(getattr(settings, 'HUANXING_HUB_LOCAL_PATH', '/tmp/huanxing-hub'))
        self.sync_filters = {
            'official_only': False,  # Sync all skills (ClawHub doesn't have official flag)
            # 每次同步抓取的技能数量上限。本地/测试默认 100；生产环境在 .env 把
            # MARKETPLACE_CLAWHUB_SYNC_LIMIT 设为 0 表示全量同步（不截断）。
            'limit': getattr(settings, 'MARKETPLACE_CLAWHUB_SYNC_LIMIT', 100),
            # downloads 阈值：只同步 stats.downloads 严格大于该值的技能。0 = 不设阈值（全收）；
            # 生产把 MARKETPLACE_CLAWHUB_MIN_DOWNLOADS 设为 100 即"下载量超过 100 才同步"。
            'min_downloads': getattr(settings, 'MARKETPLACE_CLAWHUB_MIN_DOWNLOADS', 0),
        }
        # clawhub 技能解压目录累计占用硬上限(字节)：达到即暂停后续下载（安全兜底，防止全量
        # 同步把磁盘打满）。默认 50GB，生产用 MARKETPLACE_CLAWHUB_MAX_DISK_GB 调。
        self.max_disk_bytes = int(
            float(getattr(settings, 'MARKETPLACE_CLAWHUB_MAX_DISK_GB', 50.0)) * 1024 ** 3
        )

    async def sync_from_clawhub(
        self,
        db: AsyncSession,
        force: bool = False,
        skill_ids: list[str] | None = None,
        limit: int | None = None,
        min_downloads: int | None = None,
        dry_run: bool = False,
    ) -> dict[str, Any]:
        """
        Sync skills from ClawHub

        Args:
            db: Database session
            force: Force full sync (ignore last sync time)
            skill_ids: Specific skill IDs to sync (sync all if None)
            limit: Override the configured top-N cap for this run
                (None -> use settings default; 0 -> full sync, no cap)
            min_downloads: Only sync skills whose stats.downloads is strictly
                greater than this threshold (None -> use settings default;
                0 -> no threshold). 例如 100 = "下载量超过 100 才同步"。
            dry_run: 只评估不落库：枚举 + 按阈值过滤后，统计命中数量与磁盘占用预估，
                不下载、不翻译、不写库、不建同步日志。用于先确认规模再全量。

        Returns:
            Sync result with statistics
        """
        effective_limit = self.sync_filters['limit'] if limit is None else limit
        effective_min_downloads = (
            self.sync_filters['min_downloads'] if min_downloads is None else min_downloads
        )

        # dry-run：先评估规模与占用，不做任何写入
        if dry_run:
            if skill_ids:
                skills_data = await self._fetch_specific_skills(skill_ids)
            else:
                skills_data = await self._fetch_all_skills()
            filtered_skills = self._filter_skills(skills_data, effective_limit, effective_min_downloads)
            return self._build_dry_run_report(
                total_fetched=len(skills_data),
                filtered=filtered_skills,
                min_downloads=effective_min_downloads,
                limit=effective_limit,
            )

        sync_log_id = None
        try:
            # Create sync log
            await marketplace_sync_log_dao.create(db, CreateMarketplaceSyncLogParam(
                sync_type='clawhub',
                status='in_progress',
                started_at=datetime.now()
            ))
            # Flush to get the ID
            await db.flush()
            # Query the newly created log (get the latest one)
            from sqlalchemy import select, desc
            from backend.app.marketplace.model import MarketplaceSyncLog
            stmt = select(MarketplaceSyncLog).order_by(desc(MarketplaceSyncLog.id)).limit(1)
            result = await db.execute(stmt)
            sync_log = result.scalar_one_or_none()
            if not sync_log:
                raise Exception("Failed to create sync log")
            sync_log_id = sync_log.id

            # Fetch skills from ClawHub
            if skill_ids:
                skills_data = await self._fetch_specific_skills(skill_ids)
            else:
                skills_data = await self._fetch_all_skills()

            # Filter skills (downloads 阈值 + 排序 + top-N 截断)
            filtered_skills = self._filter_skills(skills_data, effective_limit, effective_min_downloads)

            # Sync to database（逐个落库 + 磁盘硬闸；提取成 helper 降复杂度）
            outcome = await self._sync_filtered_skills(db, filtered_skills)
            paused_reason = outcome['paused_reason']

            # 状态：磁盘暂停或有失败 -> partial；否则 success。
            status = 'success' if (outcome['failed'] == 0 and paused_reason is None) else 'partial'
            error_lines = list(outcome['errors'])
            if paused_reason:
                error_lines.insert(0, paused_reason)

            # Update sync log
            await marketplace_sync_log_dao.update(db, sync_log_id, UpdateMarketplaceSyncLogParam(
                status=status,
                items_synced=outcome['synced'],
                items_failed=outcome['failed'],
                error_message='\n'.join(error_lines) if error_lines else None,
                completed_at=datetime.now()
            ))

            # Commit transaction
            await db.commit()

            return {
                'success': True,
                'synced': outcome['synced'],
                'failed': outcome['failed'],
                'skipped_disk': outcome['skipped_disk'],
                'paused': paused_reason is not None,
                'paused_reason': paused_reason,
                'errors': outcome['errors']
            }

        except Exception as e:
            log.error(f"ClawHub sync failed: {e}")

            # Update sync log
            if sync_log_id:
                await marketplace_sync_log_dao.update(db, sync_log_id, UpdateMarketplaceSyncLogParam(
                    status='failed',
                    error_message=str(e),
                    completed_at=datetime.now()
                ))

            return {
                'success': False,
                'error': str(e)
            }

    async def _sync_filtered_skills(
        self,
        db: AsyncSession,
        filtered_skills: list[dict[str, Any]],
    ) -> dict[str, Any]:
        """逐个落库 filtered_skills；clawhub 目录占用达上限即暂停后续下载（安全兜底）。

        按 downloads 降序处理（先收高价值），单个失败不中断整体。返回计数 + 暂停原因。
        """
        synced_count = 0
        failed_count = 0
        skipped_disk = 0
        errors: list[str] = []
        paused_reason: str | None = None
        total = len(filtered_skills)
        clawhub_root = self.hub_local_path / 'clawhub'

        for index, skill_data in enumerate(filtered_skills):
            used = self._dir_size_bytes(clawhub_root)
            if used >= self.max_disk_bytes:
                skipped_disk = total - index
                paused_reason = (
                    f"DISK_CAP_REACHED: clawhub 占用 {used / 1024 ** 3:.2f}GB "
                    f">= 上限 {self.max_disk_bytes / 1024 ** 3:.0f}GB，"
                    f"在第 {index}/{total} 个技能处暂停，剩余 {skipped_disk} 个未同步"
                )
                log.warning(paused_reason)
                break
            try:
                await self._sync_skill(db, skill_data)
                synced_count += 1
            except Exception as e:
                failed_count += 1
                errors.append(f"{skill_data.get('slug', 'unknown')}: {str(e)}")
                log.error(f"Failed to sync skill {skill_data.get('slug')}: {e}")

        return {
            'synced': synced_count,
            'failed': failed_count,
            'skipped_disk': skipped_disk,
            'errors': errors,
            'paused_reason': paused_reason,
        }

    async def _fetch_all_skills(self) -> list[dict[str, Any]]:
        """
        Fetch all skills from ClawHub API（游标分页）

        ClawHub `/skills` 列表用 **cursor 游标分页**：响应返回 ``items`` + ``nextCursor``，
        下一页把上一页的 ``nextCursor`` 作为 ``cursor`` 传回。**不是** page/pageSize 翻页
        （旧实现传 page/pageSize 会被 API 忽略 → 永远停在第一页、枚举不全）。游标耗尽
        （``nextCursor`` 为空）或返回空 items 即结束；``seen_cursors`` 防御游标异常重复
        导致死循环，``max_pages`` 是兜底上限。

        Returns:
            List of skill data
        """
        skills: list[dict[str, Any]] = []
        cursor: str | None = None
        page_size = 100
        seen_cursors: set[str] = set()
        max_pages = 2000  # 安全兜底：单次最多翻 2000 页（20 万技能）

        async with httpx.AsyncClient(timeout=30.0) as client:
            for _ in range(max_pages):
                params: dict[str, Any] = {'pageSize': page_size}
                if cursor:
                    params['cursor'] = cursor
                try:
                    response = await client.get(f"{self.clawhub_api_url}/skills", params=params)
                    response.raise_for_status()
                    data = response.json()
                except Exception as e:
                    log.error(f"Failed to fetch skills from ClawHub (cursor={cursor!r}): {e}")
                    break

                items = data.get('items', [])
                if not items:
                    break
                skills.extend(items)

                cursor = data.get('nextCursor')
                if not cursor or cursor in seen_cursors:
                    break
                seen_cursors.add(cursor)

        return skills

    async def _fetch_specific_skills(self, skill_ids: list[str]) -> list[dict[str, Any]]:
        """
        Fetch specific skills from ClawHub API

        Args:
            skill_ids: List of skill IDs (slugs)

        Returns:
            List of skill data (merged with owner and latestVersion)
        """
        skills = []

        async with httpx.AsyncClient(timeout=30.0) as client:
            for skill_id in skill_ids:
                try:
                    response = await client.get(f"{self.clawhub_api_url}/skills/{skill_id}")
                    response.raise_for_status()
                    data = response.json()

                    # Merge skill, owner, and latestVersion into one object
                    skill_data = data.get('skill', {})
                    skill_data['owner'] = data.get('owner', {})
                    skill_data['latestVersion'] = data.get('latestVersion', {})

                    skills.append(skill_data)
                except Exception as e:
                    log.error(f"Failed to fetch skill {skill_id} from ClawHub: {e}")

        return skills

    async def _get_skill_owner(self, slug: str) -> str:
        """
        Get skill owner handle from ClawHub API

        Args:
            slug: Skill slug

        Returns:
            Owner handle (defaults to 'community' if not found)
        """
        try:
            async with httpx.AsyncClient(timeout=30.0) as client:
                response = await client.get(f"{self.clawhub_api_url}/skills/{slug}")
                response.raise_for_status()
                data = response.json()
                return self._extract_owner_handle(data) or 'community'
        except Exception as e:
            log.warning(f"Failed to get owner for skill {slug}: {e}")
            return 'community'

    @staticmethod
    def _extract_owner_handle(data: dict[str, Any]) -> str | None:
        owner = data.get('owner')
        if isinstance(owner, dict) and owner.get('handle'):
            return str(owner['handle'])

        skill = data.get('skill')
        if isinstance(skill, dict) and skill.get('ownerHandle'):
            return str(skill['ownerHandle'])

        value = data.get('value')
        if isinstance(value, dict):
            page = value.get('page')
            if isinstance(page, list) and page:
                first = page[0]
                if isinstance(first, dict) and first.get('ownerHandle'):
                    return str(first['ownerHandle'])
        return None

    @staticmethod
    def _downloads_of(skill: dict[str, Any]) -> int:
        return (skill.get('stats') or {}).get('downloads') or 0

    def _filter_skills(
        self,
        skills: list[dict[str, Any]],
        limit: int | None = None,
        min_downloads: int = 0,
    ) -> list[dict[str, Any]]:
        """
        Filter skills based on sync criteria

        Args:
            skills: List of skill data
            limit: Top-N cap after sorting (falsy / <=0 -> full sync, no cap)
            min_downloads: 下载量阈值，只保留 stats.downloads **严格大于** 该值的技能
                （<=0 -> 不设阈值，全收，向后兼容旧默认同步）。例如 100 = "下载量超过 100 才同步"。

        Returns:
            Filtered list of skills（按 downloads 降序，再 stars、再更新时间）
        """
        threshold = max(min_downloads or 0, 0)
        eligible = (
            [s for s in skills if self._downloads_of(s) > threshold]
            if threshold > 0
            else list(skills)
        )
        ranked = sorted(
            eligible,
            key=lambda skill: (
                self._downloads_of(skill),
                (skill.get('stats') or {}).get('stars') or 0,
                skill.get('updatedAt') or skill.get('createdAt') or 0,
            ),
            reverse=True,
        )
        if not limit or limit <= 0:
            return ranked
        return ranked[:limit]

    @staticmethod
    def _dir_size_bytes(path: Path) -> int:
        """递归统计目录下所有文件的累计字节数（不存在 -> 0）。用于磁盘硬闸度量。"""
        if not path.exists():
            return 0
        total = 0
        for child in path.rglob('*'):
            try:
                if child.is_file():
                    total += child.stat().st_size
            except OSError:
                continue
        return total

    def _build_dry_run_report(
        self,
        total_fetched: int,
        filtered: list[dict[str, Any]],
        min_downloads: int,
        limit: int | None,
    ) -> dict[str, Any]:
        """dry-run 评估报告：命中数量 + 磁盘占用现状/预估 + 余量，不做任何写入。

        预估占用 = 命中数 × 单技能平均占用；平均值取「现有已同步 clawhub 目录的实测均值」，
        没有历史则用保守默认（50KB/个，clawhub 技能多为 markdown 小文件）。
        """
        clawhub_root = self.hub_local_path / 'clawhub'
        current_used = self._dir_size_bytes(clawhub_root)
        existing_count = sum(1 for _ in clawhub_root.glob('*/*')) if clawhub_root.exists() else 0
        default_avg = 50 * 1024  # 50KB 保守默认
        avg_per_skill = (current_used // existing_count) if existing_count > 0 else default_avg
        matched = len(filtered)
        estimated_bytes = matched * avg_per_skill

        free_bytes = 0
        try:
            free_bytes = shutil.disk_usage(str(self.hub_local_path)).free
        except OSError:
            pass

        gb = 1024 ** 3
        top = [{'slug': s.get('slug'), 'downloads': self._downloads_of(s)} for s in filtered[:10]]
        return {
            'success': True,
            'dry_run': True,
            'min_downloads': min_downloads,
            'limit': limit,
            'total_fetched': total_fetched,
            'matched': matched,
            'avg_bytes_per_skill': avg_per_skill,
            'estimated_disk_bytes': estimated_bytes,
            'estimated_disk_gb': round(estimated_bytes / gb, 3),
            'current_used_bytes': current_used,
            'current_used_gb': round(current_used / gb, 3),
            'max_disk_gb': round(self.max_disk_bytes / gb, 1),
            'partition_free_gb': round(free_bytes / gb, 1),
            'would_exceed_cap': (current_used + estimated_bytes) >= self.max_disk_bytes,
            'top_by_downloads': top,
        }

    async def _sync_skill(self, db: AsyncSession, clawhub_skill: dict[str, Any]):
        """
        Sync a single skill from ClawHub to database

        Args:
            db: Database session
            clawhub_skill: ClawHub skill data
        """
        # Get owner handle from detail API
        slug = clawhub_skill['slug']

        # Fetch full skill details to get owner info
        owner_handle = await self._get_skill_owner(slug)

        # Convert ClawHub format to Huanxing format
        # Format: clawhub/{owner_handle}/{slug}
        skill_id = f"clawhub/{owner_handle}/{slug}"
        namespace = f"clawhub/{owner_handle}"

        # Check if skill exists
        existing_skill = await marketplace_skill_dao.get_by_id(db, skill_id)

        # Get stats
        stats = clawhub_skill.get('stats', {})

        # Translate name and description
        name = clawhub_skill.get('displayName') or slug
        description = clawhub_skill.get('summary', '')

        translated = await translation_service.translate_skill_metadata(
            name=name,
            description=description,
            tag_hints=self._extract_tag_hints(clawhub_skill),
        )
        tags_en = translation_service.normalize_tag_list(translated.get('tags_en'))
        tags_zh = translation_service.normalize_tag_list(translated.get('tags_zh'))
        tags = tags_en or tags_zh or [slug]

        # Map category based on slug or summary using LLM
        category = await self._classify_skill(db, name, description)

        # Prepare skill record
        from backend.app.marketplace.schema.marketplace_skill import (
            CreateMarketplaceSkillParam,
            UpdateMarketplaceSkillParam
        )

        now = datetime.now()
        skill_data = {
            'skill_id': skill_id,
            'namespace': namespace,
            'slug': slug,
            'name': name,
            'name_en': translated['name_en'],
            'name_zh': translated['name_zh'],
            'description_en': translated['description_en'],
            'description_zh': translated['description_zh'],
            'source_language': translated['source_language'],
            'icon_url': None,
            'emoji': translated.get('emoji'),
            'author_name': owner_handle,
            'category': category,
            'tags': json.dumps(tags, ensure_ascii=False),
            'tags_en': json.dumps(tags_en or tags, ensure_ascii=False),
            'tags_zh': json.dumps(tags_zh or tags, ensure_ascii=False),
            'pricing_type': 'free',
            'price': 0,
            'is_official': False,
            'is_private': False,
            'source_type': 'clawhub',
            'source_repo_url': f"https://clawhub.ai/skills/{slug}",
            'download_count': stats.get('downloads', 0),
            'star_count': stats.get('stars', 0),
            'synced_at': now,
            'translated_at': now,
        }

        if existing_skill:
            # Update existing skill
            update_param = UpdateMarketplaceSkillParam(**skill_data)
            await marketplace_skill_dao.update(db, existing_skill.id, update_param)
            skill_id_for_version = skill_id
        else:
            # Create new skill
            create_param = CreateMarketplaceSkillParam(**skill_data)
            await marketplace_skill_dao.create(db, create_param)
            # Flush to get the ID
            await db.flush()
            # Query the newly created skill
            created_skill = await marketplace_skill_dao.get_by_id(db, skill_id)
            if not created_skill:
                raise Exception(f"Failed to create skill: {skill_id}")
            skill_id_for_version = skill_id

        # Sync latest version
        latest_version = clawhub_skill.get('latestVersion')
        if latest_version:
            version_str = latest_version.get('version', '1.0.0')

            # Download skill file
            repo_path = await self._download_skill_file(
                skill_id=skill_id,
                owner_handle=owner_handle,
                slug=slug,
                version=version_str
            )

            # Update skill with repo_path
            if repo_path:
                # Get the current skill
                current_skill = await marketplace_skill_dao.get_by_id(db, skill_id)

                if current_skill:
                    # Create update param with repo_path (本地 huanxing-hub 路径)
                    now = datetime.now()
                    # 解压后的技能目录里有 SKILL.md 与全部文件 —— 与 github_sync 同源，
                    # 提取正文(双语)+文件清单(仅名称+大小)供详情页展示。
                    skill_dir = self.hub_local_path / 'clawhub' / owner_handle / slug
                    body_en, body_zh, files_json = await self._extract_body_and_files(
                        current_skill, translated['source_language'], skill_dir,
                    )
                    update_data = {
                        'skill_id': skill_id,
                        'namespace': namespace,
                        'slug': slug,
                        'name': name,
                        'name_en': translated['name_en'],
                        'name_zh': translated['name_zh'],
                        'description_en': translated['description_en'],
                        'description_zh': translated['description_zh'],
                        'body_en': body_en,
                        'body_zh': body_zh,
                        'files': files_json,
                        'source_language': translated['source_language'],
                        'icon_url': None,
                        'emoji': translated.get('emoji'),
                        'author_name': owner_handle,
                        'category': category,
                        'tags': json.dumps(tags, ensure_ascii=False),
                        'tags_en': json.dumps(tags_en or tags, ensure_ascii=False),
                        'tags_zh': json.dumps(tags_zh or tags, ensure_ascii=False),
                        'pricing_type': 'free',
                        'price': 0,
                        'is_official': False,
                        'is_private': False,
                        'source_type': 'clawhub',
                        'source_repo_url': f"https://clawhub.ai/skills/{slug}",
                        'repo_path': repo_path,  # 本地 huanxing-hub 路径
                        'download_count': stats.get('downloads', 0),
                        'star_count': stats.get('stars', 0),
                        'synced_at': now,
                        'translated_at': now,
                    }
                    update_param = UpdateMarketplaceSkillParam(**update_data)
                    await marketplace_skill_dao.update(db, current_skill.id, update_param)

            # Sync version metadata
            await self._sync_skill_version(db, skill_id_for_version, latest_version)

    async def _sync_skill_version(
        self,
        db: AsyncSession,
        skill_id: str,
        version_data: dict[str, Any]
    ):
        """
        Sync a skill version

        Args:
            db: Database session
            skill_id: Skill ID (e.g., "clawhub/my-skill")
            version_data: Version data from ClawHub
        """
        version = version_data.get('version', '1.0.0')

        # Check if version exists
        existing_version = await marketplace_skill_version_dao.get_by_skill_and_version(
            db, skill_id, version
        )

        # Translate changelog
        changelog = version_data.get('changelog', '')
        if changelog:
            translated_changelog = await translation_service.translate_skill_metadata(
                description=changelog
            )
            changelog_en = translated_changelog['description_en']
            changelog_zh = translated_changelog['description_zh']
        else:
            changelog_en = None
            changelog_zh = None

        # Convert timestamp to datetime
        created_at = version_data.get('createdAt')
        if created_at:
            released_at = datetime.fromtimestamp(created_at / 1000)  # ClawHub uses milliseconds
        else:
            released_at = datetime.now()

        # Prepare version record
        from backend.app.marketplace.schema.marketplace_skill_version import (
            CreateMarketplaceSkillVersionParam,
            UpdateMarketplaceSkillVersionParam
        )

        version_data_dict = {
            'skill_id': skill_id,
            'version': version,
            'changelog': changelog_en,  # Use English changelog as default
            'package_url': f"https://clawhub.ai/api/v1/skills/{skill_id.split('/')[-1]}/versions/{version}/download",
            'file_hash': None,
            'file_size': None,
            'is_latest': True,  # Mark as latest (we only sync latest version)
            'published_at': released_at
        }

        if existing_version:
            # Update existing version
            update_param = UpdateMarketplaceSkillVersionParam(**version_data_dict)
            await marketplace_skill_version_dao.update(db, existing_version.id, update_param)
        else:
            # Create new version
            create_param = CreateMarketplaceSkillVersionParam(**version_data_dict)
            await marketplace_skill_version_dao.create(db, create_param)

    async def _classify_skill(self, db: AsyncSession, name: str, description: str) -> str:
        """
        Classify skill using keyword matching

        Args:
            db: Database session
            name: Skill name
            description: Skill description

        Returns:
            Category slug
        """
        from backend.app.marketplace.crud.crud_marketplace_category import marketplace_category_dao

        # Get available categories from database
        categories = await marketplace_category_dao.get_all(db)
        category_slugs = [cat.slug for cat in categories]

        # Use keyword matching (LLM classification disabled due to API issues)
        slug = self._map_category_from_text(name + ' ' + description, category_slugs)
        # 归一兜底：把命中结果收口到权威 12 领域 slug（防止旧 slug 漏网）。
        return normalize_category(slug)

    def _map_category_from_text(self, text: str, available_categories: list[str]) -> str:
        """
        Map category from text content using keyword matching

        Args:
            text: Text to analyze (name + description)
            available_categories: List of available category slugs

        Returns:
            Category slug
        """
        text_lower = text.lower()

        # 关键词 → 权威 12 领域 slug。按"具体垂直域优先、通用工具兜底"排序，
        # 命中越靠前优先级越高（finance/health/development/media 等先于 productivity/communication）。
        keyword_map = {
            'finance': ['finance', 'invest', 'stock', 'trading', 'crypto', 'payment', '理财', '金融', '股票'],
            'health': ['health', 'medical', 'fitness', 'diet', 'wellness', '医疗', '健康', '健身'],
            'development': ['code', 'programming', 'development', 'git', 'debug', 'api', 'sdk', 'compiler'],
            'data-analysis': ['data', 'analysis', 'analytics', 'database', 'sql', 'statistics', '数据'],
            'media': ['video', 'image', 'photo', 'picture', 'audio', 'music', 'sound', 'media', 'multimedia', 'film'],
            'content-creation': ['content', 'writing', 'write', 'blog', 'article', 'copywriting', '文案', '写作'],
            'creativity': ['creative', 'design', 'art', 'draw', 'illustration', 'nsfw', '设计', '创意'],
            'search': ['search', 'retrieval', 'lookup', 'index', '检索', '搜索'],
            'communication': ['chat', 'communication', 'message', 'email', 'meeting', 'social', '沟通', '社交'],
            'ai-assistant': ['llm', 'machine learning', 'assistant', 'chatbot', 'prompt', '助手'],
            'entertainment': ['game', 'entertainment', 'fun', 'play', '娱乐', '游戏'],
            'productivity': ['automation', 'workflow', 'task', 'schedule', 'productivity', 'efficiency',
                             'utility', 'tool', 'office', '效率', '自动化', '工具'],
        }

        # Find matching category
        for category_slug, keywords in keyword_map.items():
            # Only consider categories that exist in database
            if category_slug not in available_categories:
                continue

            for keyword in keywords:
                if keyword in text_lower:
                    return category_slug

        # Default to 'other' if available, otherwise first category
        if 'other' in available_categories:
            return 'other'
        elif available_categories:
            return available_categories[0]
        else:
            return 'other'

    async def _download_skill_file(
        self,
        skill_id: str,
        owner_handle: str,
        slug: str,
        version: str
    ) -> str | None:
        """
        Download skill file from ClawHub and save to local hub

        Args:
            skill_id: Full skill ID (e.g., "clawhub/owner/slug")
            owner_handle: Owner handle
            slug: Skill slug
            version: Version to download

        Returns:
            Local repo path if successful, None otherwise
        """
        try:
            # Create directory structure: huanxing-hub/clawhub/{owner}/{slug}
            skill_dir = self.hub_local_path / 'clawhub' / owner_handle / slug
            skill_dir.mkdir(parents=True, exist_ok=True)

            # Download URL - ClawHub uses query parameters for skills
            download_url = f"{self.clawhub_api_url}/download"
            params = {
                'slug': slug,
                'version': version
            }

            log.info(f"Downloading skill {slug} version {version} from {download_url}")

            # Download the skill file
            async with httpx.AsyncClient(timeout=60.0, follow_redirects=True) as client:
                response = await client.get(download_url, params=params)
                response.raise_for_status()

                # Save to file
                zip_file = skill_dir / f"{slug}-{version}.zip"
                zip_file.write_bytes(response.content)

                log.info(f"Downloaded skill file to {zip_file} ({len(response.content)} bytes)")

                # Extract the zip file
                if zipfile.is_zipfile(zip_file):
                    log.info(f"Extracting skill file...")
                    with zipfile.ZipFile(zip_file, 'r') as zip_ref:
                        zip_ref.extractall(skill_dir)

                    # Remove the zip file after extraction
                    zip_file.unlink()
                    log.info(f"Extracted and removed zip file")
                else:
                    log.warning(f"Downloaded file is not a zip file, keeping as-is")

                # Return the relative path from hub root
                return f"clawhub/{owner_handle}/{slug}"

        except Exception as e:
            log.error(f"Failed to download skill {slug}: {e}")
            return None

    async def _extract_body_and_files(
        self,
        existing_skill: Any,
        source_language: str | None,
        skill_dir: Path,
    ) -> tuple[str | None, str | None, str]:
        """从解压后的技能目录提取正文(双语)+文件清单 JSON。

        与 github_sync 同源（共享 skill_content_extractor）。SKILL.md 缺失或目录
        不存在时正文留空、文件清单为 ``[]``（零 fake，如实反映“没有正文”）。文件清单
        仅含名称+大小，不含内容。
        """
        skill_md = skill_dir / 'SKILL.md'
        body = ''
        if skill_md.exists():  # noqa: ASYNC240
            try:
                body = extract_skill_body(skill_md.read_text(encoding='utf-8'))  # noqa: ASYNC240
            except OSError as exc:
                log.warning(f"Failed to read SKILL.md at {skill_md}: {exc}")
        files = list_skill_files(skill_dir) if skill_dir.exists() else []  # noqa: ASYNC240
        body_en, body_zh = await resolve_bilingual_body(existing_skill, source_language, body)
        return body_en, body_zh, json.dumps(files, ensure_ascii=False)

    @staticmethod
    def _extract_tag_hints(clawhub_skill: dict[str, Any]) -> list[str]:
        tags = clawhub_skill.get('tags')
        if isinstance(tags, list):
            normalized = [str(tag).strip() for tag in tags if str(tag).strip()]
        elif isinstance(tags, dict):
            normalized = [str(tag).strip() for tag in tags if str(tag).strip()]
        elif isinstance(tags, str):
            normalized = [tag.strip() for tag in tags.split(',') if tag.strip()]
        else:
            normalized = []

        return translation_service.normalize_tag_list(normalized)


# Singleton instance
clawhub_sync_service = ClawHubSyncService()
