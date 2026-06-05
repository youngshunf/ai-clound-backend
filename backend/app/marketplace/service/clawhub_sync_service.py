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
        }

    async def sync_from_clawhub(
        self,
        db: AsyncSession,
        force: bool = False,
        skill_ids: list[str] | None = None,
        limit: int | None = None,
    ) -> dict[str, Any]:
        """
        Sync skills from ClawHub

        Args:
            db: Database session
            force: Force full sync (ignore last sync time)
            skill_ids: Specific skill IDs to sync (sync all if None)
            limit: Override the configured top-N cap for this run
                (None -> use settings default; 0 -> full sync, no cap)

        Returns:
            Sync result with statistics
        """
        effective_limit = self.sync_filters['limit'] if limit is None else limit
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

            # Filter skills
            filtered_skills = self._filter_skills(skills_data, effective_limit)

            # Sync to database
            synced_count = 0
            failed_count = 0
            errors = []

            for skill_data in filtered_skills:
                try:
                    await self._sync_skill(db, skill_data)
                    synced_count += 1
                except Exception as e:
                    failed_count += 1
                    errors.append(f"{skill_data.get('slug', 'unknown')}: {str(e)}")
                    log.error(f"Failed to sync skill {skill_data.get('slug')}: {e}")

            # Update sync log
            await marketplace_sync_log_dao.update(db, sync_log_id, UpdateMarketplaceSyncLogParam(
                status='success' if failed_count == 0 else 'partial',
                items_synced=synced_count,
                items_failed=failed_count,
                error_message='\n'.join(errors) if errors else None,
                completed_at=datetime.now()
            ))

            # Commit transaction
            await db.commit()

            return {
                'success': True,
                'synced': synced_count,
                'failed': failed_count,
                'errors': errors
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

    async def _fetch_all_skills(self) -> list[dict[str, Any]]:
        """
        Fetch all skills from ClawHub API

        Returns:
            List of skill data
        """
        skills = []
        page = 1
        page_size = 50

        async with httpx.AsyncClient(timeout=30.0) as client:
            while True:
                try:
                    response = await client.get(
                        f"{self.clawhub_api_url}/skills",
                        params={
                            'page': page,
                            'pageSize': page_size
                        }
                    )
                    response.raise_for_status()
                    data = response.json()

                    items = data.get('items', [])
                    if not items:
                        break

                    skills.extend(items)

                    # Check if there are more pages
                    if len(items) < page_size:
                        break

                    page += 1

                except Exception as e:
                    log.error(f"Failed to fetch skills from ClawHub (page {page}): {e}")
                    break

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

    def _filter_skills(
        self,
        skills: list[dict[str, Any]],
        limit: int | None = None,
    ) -> list[dict[str, Any]]:
        """
        Filter skills based on sync criteria

        Args:
            skills: List of skill data
            limit: Top-N cap after sorting (falsy / <=0 -> full sync, no cap)

        Returns:
            Filtered list of skills
        """
        ranked = sorted(
            skills,
            key=lambda skill: (
                (skill.get('stats') or {}).get('stars') or 0,
                (skill.get('stats') or {}).get('downloads') or 0,
                skill.get('updatedAt') or skill.get('createdAt') or 0,
            ),
            reverse=True,
        )
        if not limit or limit <= 0:
            return ranked
        return ranked[:limit]

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
        return self._map_category_from_text(name + ' ' + description, category_slugs)

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

        # Category mapping rules (keyword -> category slug)
        keyword_map = {
            'creativity': ['video', 'image', 'generation', 'creative', 'art', 'design', 'nsfw', 'media'],
            'development': ['code', 'programming', 'development', 'git', 'debug', 'api'],
            'data': ['data', 'analysis', 'database', 'sql', 'analytics'],
            'data-analysis': ['data', 'analysis', 'analytics', 'statistics'],
            'productivity': ['automation', 'workflow', 'task', 'schedule', 'productivity', 'efficiency'],
            'communication': ['chat', 'communication', 'message', 'email', 'meeting'],
            'ai-assistant': ['ai', 'llm', 'machine learning', 'model', 'assistant'],
            'automation': ['automation', 'workflow', 'task', 'schedule'],
            'content-creation': ['content', 'creation', 'writing', 'blog'],
            'entertainment': ['game', 'entertainment', 'fun', 'play'],
            'writing': ['writing', 'write', 'text', 'document'],
            'video': ['video', 'movie', 'film'],
            'image': ['image', 'photo', 'picture'],
            'audio': ['audio', 'music', 'sound'],
            'media': ['media', 'multimedia']
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
