"""
GitHub Sync Service for Marketplace Skills

Syncs skills from huanxing-hub GitHub repository to database.
"""
import json
import os

from pathlib import Path
from typing import Any

import sqlalchemy as sa
import yaml

from git import Repo
from sqlalchemy.ext.asyncio import AsyncSession

from backend.app.marketplace.crud.crud_marketplace_category import marketplace_category_dao
from backend.app.marketplace.crud.crud_marketplace_skill import marketplace_skill_dao
from backend.app.marketplace.model.marketplace_skill import MarketplaceSkill
from backend.app.marketplace.crud.crud_marketplace_skill_version import marketplace_skill_version_dao
from backend.app.marketplace.crud.crud_marketplace_sync_log import marketplace_sync_log_dao
from backend.app.marketplace.storage.s3_storage import marketplace_storage_service
from backend.app.marketplace.schema.marketplace_skill import CreateMarketplaceSkillParam, UpdateMarketplaceSkillParam
from backend.app.marketplace.schema.marketplace_skill_version import (
    CreateMarketplaceSkillVersionParam,
    UpdateMarketplaceSkillVersionParam,
)
from backend.app.marketplace.service.skill_content_extractor import (
    extract_skill_body,
    list_skill_files,
    resolve_bilingual_body,
)
from backend.app.marketplace.schema.marketplace_sync_log import (
    CreateMarketplaceSyncLogParam,
    UpdateMarketplaceSyncLogParam,
)
from backend.app.marketplace.service.category_taxonomy import normalize_category
from backend.app.marketplace.service.package_validation import normalize_tags
from backend.app.marketplace.service.translation_service import translation_service
from backend.app.marketplace.storage.s3_storage import marketplace_storage_service
from backend.common.log import log
from backend.core.conf import settings
from backend.utils.timezone import timezone

# huanxing-skills 目录名 → 权威分类 slug 的归一统一收口到 `category_taxonomy.normalize_category`
# （旧的 HUANXING_CATEGORY_ALIASES 已并入该表：developer→development、creative→creativity、
# data→data-analysis、utility→productivity、social→communication、official→other 等）。


class GitHubSyncService:
    """GitHub sync service for marketplace skills"""

    def __init__(self) -> None:
        self.repo_url = getattr(settings, 'HUANXING_HUB_REPO_URL', 'https://github.com/youngshunf/huanxing-hub.git')
        self.local_path = getattr(settings, 'HUANXING_HUB_LOCAL_PATH', '/tmp/huanxing-hub')
        self.repo: Repo | None = None

    async def sync_from_github(self, db: AsyncSession, force: bool = False) -> dict[str, Any]:  # noqa: FBT001, FBT002
        """
        Sync skills from GitHub repository

        Args:
            db: Database session
            force: Force full sync (ignore last sync time)

        Returns:
            Sync result with statistics
        """
        sync_log_id = None
        try:
            # Create sync log
            sync_log = await marketplace_sync_log_dao.create(
                db,
                CreateMarketplaceSyncLogParam(
                    sync_type='github',
                    status='in_progress',
                    started_at=timezone.now(),
                ),
            )
            await db.flush()
            sync_log_id = sync_log.id if sync_log else None

            # Clone or pull repository
            await self._update_repository()

            # Scan for skills
            skills_data = await self._scan_skills()

            # 公共技能标记（doc12 §3.2）：从 hub common-skills.yaml 读公共集合，
            # 命中的技能打 is_common；下架/移除的在同步末尾统一清除。
            common_ids = self._load_common_skill_ids()
            for skill_data in skills_data:
                skill_data['is_common'] = skill_data.get('skill_id') in common_ids

            # Translate all scanned skills in one batched pass (10 per LLM request)
            # instead of one request per skill — language detection, bilingual
            # name/description, bilingual tags, emoji AND category all come back
            # together (LLM picks the category from our taxonomy in the same call).
            categories = await self._load_categories(db)
            translations = await self._batch_translate(skills_data, categories)

            # Sync to database
            synced_count = 0
            failed_count = 0
            errors = []

            for skill_data, translated in zip(skills_data, translations):
                try:
                    await self._sync_skill(db, skill_data, translated)
                    synced_count += 1
                except Exception as e:  # noqa: PERF203
                    failed_count += 1
                    errors.append(f"{skill_data.get('skill_id', 'unknown')}: {e!s}")
                    log.error(f"Failed to sync skill {skill_data.get('skill_id')}: {e}")

            # 清除已移出 common-skills.yaml 的公共标记，保持 is_common 集合与 hub 一致。
            # common_ids 为空 ⇒ 清除全部公共标记（无公共技能）。
            clear_common = sa.update(MarketplaceSkill).where(MarketplaceSkill.is_common.is_(True))
            if common_ids:
                clear_common = clear_common.where(MarketplaceSkill.skill_id.notin_(list(common_ids)))
            await db.execute(clear_common.values(is_common=False))

            # 技能包同步（实施/91 B3.3）：扫描 hub bundles/*/bundle.yaml → 落库
            # marketplace_template(skill_pack)。公共包集合由 common-bundles.yaml 决定，
            # 每个包按 is_common 显式落标（无单独 reconcile：重扫即覆盖）。
            bundle_synced, bundle_failed, bundle_errors = await self._sync_bundles(db)
            synced_count += bundle_synced
            failed_count += bundle_failed
            errors.extend(bundle_errors)

            # Update sync log
            if sync_log_id:
                await marketplace_sync_log_dao.update(
                    db,
                    sync_log_id,
                    UpdateMarketplaceSyncLogParam(
                        status='success' if failed_count == 0 else 'partial',
                        items_synced=synced_count,
                        items_failed=failed_count,
                        error_message='\n'.join(errors) if errors else None,
                        completed_at=timezone.now(),
                    ),
                )
                await db.commit()

            return {  # noqa: TRY300
                'success': True,
                'synced': synced_count,
                'failed': failed_count,
                'errors': errors,
            }

        except Exception as e:
            log.error(f"GitHub sync failed: {e}")

            # Update sync log
            if sync_log_id:
                await marketplace_sync_log_dao.update(
                    db,
                    sync_log_id,
                    UpdateMarketplaceSyncLogParam(
                        status='failed',
                        error_message=str(e),
                        completed_at=timezone.now(),
                    ),
                )
                await db.commit()

            return {
                'success': False,
                'error': str(e),
            }

    async def _update_repository(self) -> None:
        """Clone or pull the GitHub repository"""
        if os.path.exists(self.local_path):  # noqa: ASYNC240
            # Pull latest changes
            log.info(f"Pulling latest changes from {self.repo_url}")
            self.repo = Repo(self.local_path)
            origin = self.repo.remotes.origin
            origin.pull()
        else:
            # Clone repository
            log.info(f"Cloning repository from {self.repo_url}")
            self.repo = Repo.clone_from(self.repo_url, self.local_path, multi_options=['--recurse-submodules'])

        self.repo.git.submodule('sync', '--recursive')
        with self.repo.git.custom_environment(GIT_HTTP_VERSION='HTTP/1.1'):
            self.repo.git.submodule('update', '--init', '--recursive', '--remote')

    def _load_common_skill_ids(self) -> set[str]:
        """从 hub `common-skills.yaml` 读取公共技能 skill_id 集合（doc12 §3.2）。

        文件缺失/格式异常 ⇒ 返回空集（零 fake：不臆造公共技能，宁可不标记）。
        值形如 `huanxing/search/newsnow`，与 marketplace skill_id 同构。
        """
        path = Path(self.local_path) / 'common-skills.yaml'
        if not path.exists():
            log.warning(f'common-skills.yaml not found under {self.local_path}; no common skills marked')
            return set()
        try:
            data = yaml.safe_load(path.read_text(encoding='utf-8')) or {}
        except (OSError, yaml.YAMLError) as exc:
            log.error(f'failed to parse common-skills.yaml: {exc}')
            return set()
        raw = data.get('skills') if isinstance(data, dict) else None
        if not isinstance(raw, list):
            return set()
        return {str(item).strip() for item in raw if isinstance(item, str) and item.strip()}

    def _load_common_bundle_slugs(self) -> set[str]:
        """从 hub `common-bundles.yaml` 读取公共技能包 slug 集合（实施/91 B3.3）。

        与 common-skills.yaml 对称：文件缺失/格式异常 ⇒ 返回空集（零 fake：不臆造公共包）。
        值形如 `backend-dev`（bundle 目录名，归一化后），云端据此打 marketplace_template.is_common。
        bundle.yaml 本身不写 is_common（hermes 原生格式纯净，见 B3.4），由此清单决定。
        """
        from backend.app.marketplace.service import skill_pack_service

        path = Path(self.local_path) / 'common-bundles.yaml'
        if not path.exists():
            log.info(f'common-bundles.yaml not found under {self.local_path}; no common bundles marked')
            return set()
        try:
            data = yaml.safe_load(path.read_text(encoding='utf-8')) or {}
        except (OSError, yaml.YAMLError) as exc:
            log.error(f'failed to parse common-bundles.yaml: {exc}')
            return set()
        raw = data.get('bundles') if isinstance(data, dict) else None
        if not isinstance(raw, list):
            return set()
        return {
            skill_pack_service.normalize_slug(str(item))
            for item in raw
            if isinstance(item, str) and item.strip()
        }

    def _scan_bundles(self) -> list[dict[str, Any]]:
        """扫描 hub `bundles/*/bundle.yaml`，产出可落库的 skill_pack 记录（实施/91 B3.3）。

        非法 bundle（解析失败/skills 空/slug 不自洽）在 _parse_bundle_yaml 内 log.error 跳过，
        不进结果集（同步期容错，安装期才硬报错）。
        """
        out: list[dict[str, Any]] = []
        root = Path(self.local_path) / 'bundles'
        if not root.exists():
            return out
        for bundle_yaml in sorted(root.glob('*/bundle.yaml')):
            record = self._parse_bundle_yaml(bundle_yaml)
            if record:
                out.append(record)
        return out

    def _parse_bundle_yaml(self, path: Path) -> dict[str, Any] | None:
        """解析单个 bundle.yaml（hermes 原生格式）→ 落库记录；非法返回 None（log.error 跳过）。

        四档校验：safe_load 成 dict / skills 非空 list[str] / 目录名归一化 == name 归一化（slug 自洽）。
        产出：bundle_slug=目录名归一化、template_id='huanxing/'+slug、command_key='/'+slug、
        hermes_yaml=safe_dump 规范化（剔除 marketplace 维度键 is_common/is_official/version）。
        """
        from backend.app.marketplace.service import skill_pack_service

        dir_slug = skill_pack_service.normalize_slug(path.parent.name)
        if not dir_slug:
            log.error(f'bundle dir name normalizes to empty: {path}')
            return None
        try:
            spec = yaml.safe_load(path.read_text(encoding='utf-8'))
        except (OSError, yaml.YAMLError) as exc:
            log.error(f'failed to parse bundle.yaml {path}: {exc}')
            return None
        if not isinstance(spec, dict):
            log.error(f'bundle.yaml top-level must be a mapping: {path}')
            return None
        skills = spec.get('skills')
        if not isinstance(skills, list) or not skills or not all(isinstance(s, str) and s.strip() for s in skills):
            log.error(f'bundle.yaml skills must be a non-empty list of strings: {path}')
            return None
        name_slug = skill_pack_service.normalize_slug(str(spec.get('name') or dir_slug))
        if name_slug != dir_slug:
            log.error(f'bundle.yaml name({name_slug}) inconsistent with dir slug({dir_slug}): {path}')
            return None

        # marketplace 维度键不属 hermes 原生格式（B3.4），落库前剔除，保证 hermes_yaml 纯净可被上游消费。
        # display_name（中文展示名）同属 marketplace 维度：hermes 用 name(=slug) 做命令匹配，故一并剔除。
        hermes_spec = {k: v for k, v in spec.items() if k not in ('is_common', 'is_official', 'version', 'display_name')}
        hermes_yaml = yaml.safe_dump(hermes_spec, allow_unicode=True, sort_keys=False).strip() + '\n'
        return {
            'bundle_slug': dir_slug,
            'template_id': f'huanxing/{dir_slug}',
            'command_key': f'/{dir_slug}',
            # 落库 name 取中文 display_name 供卡片展示，缺省回退 slug；slug 标识仍存 bundle_slug/command_key。
            'name': str(spec.get('display_name') or spec.get('name') or dir_slug),
            'description': spec.get('description'),
            'icon_path': self._find_bundle_icon(path.parent),
            'hermes_yaml': hermes_yaml,
            'hermes_bundle_json': hermes_spec,
            'version': '1.0.0',
        }

    @staticmethod
    def _find_bundle_icon(bundle_dir: Path) -> Path | None:
        """技能包目录下的可选图标（icon.svg 优先）；缺失返回 None（卡片回退 emoji/占位）。"""
        for icon_name in ('icon.svg', 'icon.png', 'icon.jpg', 'icon.jpeg'):
            icon_path = bundle_dir / icon_name
            if icon_path.exists():
                return icon_path
        return None

    async def _sync_bundle(self, db: AsyncSession, record: dict[str, Any], *, is_common: bool) -> dict[str, Any]:
        """把单个 bundle 记录落库 marketplace_template(skill_pack) + version（实施/91 B3.3）。

        复用 B2.4 的 skill_pack_service.upsert_skill_pack（含 B2.2 校验+规范化）。官方 hub 包
        author_id=None（系统出品，非个人作者），is_official=True，is_common 由调用方按 common-bundles 决定。
        """
        from backend.app.marketplace.schema.skill_pack import SkillPackCreateRequest
        from backend.app.marketplace.service import skill_pack_service

        # 本地有 icon.svg → 上传到公共桶（marketplace_storage_service 已对图标默认落 access=public 桶，
        # URL 浏览器可直取），icon_url 回填进 payload；缺图标则保持 None（不覆盖现值）。
        icon_url: str | None = None
        icon_path = record.get('icon_path')
        if icon_path:
            icon_url = await marketplace_storage_service.upload_icon(
                db=db,
                item_type='template',
                item_id=record['template_id'],
                content=Path(icon_path).read_bytes(),  # noqa: ASYNC240
                filename=Path(icon_path).name,
            )

        payload = SkillPackCreateRequest(
            template_id=record['template_id'],
            namespace='huanxing',
            name=record['name'],
            description=record.get('description'),
            icon_url=icon_url,
            bundle_slug=record['bundle_slug'],
            command_key=record['command_key'],
            version=record['version'],
            hermes_bundle_json=record['hermes_bundle_json'],
            hermes_yaml=record['hermes_yaml'],
            is_private=False,
            is_official=True,
        )
        return await skill_pack_service.upsert_skill_pack(db, payload, author_id=None, is_common=is_common)

    async def _sync_bundles(self, db: AsyncSession) -> tuple[int, int, list[str]]:
        """扫描 + 落库全部 hub 技能包，返回 (synced, failed, errors)（实施/91 B3.3）。

        每个 bundle 按 common-bundles.yaml 显式落 is_common（重扫即覆盖，无单独 reconcile）。
        单个 bundle 落库失败计入 failed，不阻断其余（零 fake：如实报错，不假装成功）。
        """
        records = self._scan_bundles()
        if not records:
            return 0, 0, []
        common_slugs = self._load_common_bundle_slugs()
        synced = failed = 0
        errors: list[str] = []
        for record in records:
            try:
                await self._sync_bundle(db, record, is_common=record['bundle_slug'] in common_slugs)
                synced += 1
            except Exception as exc:  # noqa: PERF203, BLE001
                failed += 1
                errors.append(f"bundle {record.get('bundle_slug', 'unknown')}: {exc!s}")
                log.error(f"Failed to sync bundle {record.get('bundle_slug')}: {exc}")
        return synced, failed, errors

    async def _scan_skills(self) -> list[dict[str, Any]]:
        """
        Scan repository for skills

        Returns:
            List of skill metadata
        """
        skills = []
        repo_root = Path(self.local_path)
        for skill_md in self._iter_skill_markdown(repo_root):
            try:
                skills.append(await self._parse_skill_markdown(skill_md))
            except Exception as exc:  # noqa: PERF203
                log.error(f"Failed to parse {skill_md}: {exc}")

        if not skills:
            log.warning(f"No SKILL.md skills found under {repo_root}")

        return skills

    def _iter_skill_markdown(self, repo_root: Path) -> list[Path]:
        skill_files: list[Path] = []
        huanxing_root = repo_root / 'huanxing-skills'
        if huanxing_root.exists():
            skill_files.extend(huanxing_root.glob('*/*/SKILL.md'))

        github_root = repo_root / 'github'
        if github_root.exists():
            skill_files.extend(
                skill_md for skill_md in github_root.glob('*/*/*/SKILL.md')
                if skill_md.parent.parent.name == 'skills'
            )

        return skill_files

    async def _parse_skill_markdown(self, skill_md_path: Path) -> dict[str, Any]:
        relative = skill_md_path.parent.relative_to(Path(self.local_path))
        parts = relative.parts
        root_name = parts[0] if parts else ''
        if root_name == 'huanxing-skills' and len(parts) == 3:
            _, owner_or_category, slug = parts
            namespace = f'huanxing/{owner_or_category}'
            repo_path = f'huanxing-skills/{owner_or_category}/{slug}'
            category = normalize_category(owner_or_category)
            is_official = True
            source_type = 'huanxing'
        elif root_name == 'github' and len(parts) >= 3:
            owner_or_category = parts[1]
            slug = parts[-1]
            namespace = f'github/{owner_or_category}'
            repo_path = '/'.join(parts)
            category = None
            is_official = False
            source_type = 'github'
        else:
            raise ValueError(f"Unsupported skill path: {relative}")

        text = skill_md_path.read_text(encoding='utf-8')  # noqa: ASYNC240
        metadata = self._extract_skill_frontmatter(text)
        for field in ('name', 'description'):
            if not metadata.get(field):
                raise ValueError(f"SKILL.md frontmatter missing {field}: {skill_md_path}")

        tag_hints = normalize_tags(metadata.get('tags'))
        icon_path = self._find_local_icon(skill_md_path.parent)
        skill_id = f'{namespace}/{slug}'
        # SKILL.md 正文（frontmatter 之后的 Markdown）+ 技能目录文件清单（仅名称+大小）。
        body = self._extract_skill_body(text)
        files = self._list_skill_files(skill_md_path.parent)
        return {
            'skill_id': skill_id,
            'namespace': namespace,
            'slug': slug,
            # frontmatter category 归一到权威 slug；huanxing 目录名已归一；github 无自带分类
            # 时保留 None，交由下游 LLM 分类（_sync_skill 落库时再归一兜底）。
            'category': normalize_category(metadata.get('category'), default=None) or category,
            'repo_path': repo_path,
            'git_commit_hash': self.repo.head.commit.hexsha if self.repo else None,
            'icon_url': metadata.get('icon_url') or metadata.get('icon_s3_url') or metadata.get('icon_cdn_url'),
            'icon_path': icon_path,
            'emoji': metadata.get('emoji'),
            'author_name': metadata.get('author') or metadata.get('author_name'),
            'body': body,
            'files': files,
            'tag_hints': tag_hints,
            'pricing_type': metadata.get('pricing_type', 'free'),
            'price': metadata.get('price', 0),
            'is_official': is_official,
            'is_private': False,
            'source_type': source_type,
            'source_language': 'zh' if any(ord(c) > 127 for c in str(metadata.get('name'))) else 'en',
            'name': metadata.get('name'),
            'description': metadata.get('description'),
            'version': str(metadata.get('version') or '1.0.0'),
            'changelog': metadata.get('changelog') or f"Version {metadata.get('version') or '1.0.0'}",
            'versions': [{
                'version': str(metadata.get('version') or '1.0.0'),
                'changelog': metadata.get('changelog') or f"Version {metadata.get('version') or '1.0.0'}",
                'package_url': metadata.get('package_url'),
                'file_hash': metadata.get('file_hash'),
                'file_size': metadata.get('file_size'),
                'is_latest': True,
            }],
        }

    @staticmethod
    def _extract_skill_frontmatter(markdown: str) -> dict[str, Any]:
        import re

        match = re.match(r'\A---\s*\n(.*?)\n---\s*(?:\n|\Z)', markdown, re.DOTALL)
        if not match:
            raise ValueError('SKILL.md missing YAML frontmatter')
        data = yaml.safe_load(match.group(1)) or {}
        if not isinstance(data, dict):
            raise TypeError('SKILL.md frontmatter must be a mapping')
        return data

    # 正文/文件清单提取与双语解析的实现已抽到 `skill_content_extractor`，github 与
    # clawhub 两条同步链路共用；下列方法保留为薄委托以维持既有调用点与测试桩。
    @staticmethod
    def _extract_skill_body(markdown: str) -> str:
        return extract_skill_body(markdown)

    @staticmethod
    def _list_skill_files(skill_dir: Path) -> list[dict[str, Any]]:
        return list_skill_files(skill_dir)

    @staticmethod
    def _find_local_icon(skill_dir: Path) -> Path | None:
        for icon_name in ('icon.svg', 'icon.png', 'icon.jpg', 'icon.jpeg'):
            icon_path = skill_dir / icon_name
            if icon_path.exists():
                return icon_path
        return None

    async def _batch_translate(
        self,
        skills_data: list[dict[str, Any]],
        categories: list[dict[str, Any]] | None = None,
    ) -> list[dict[str, Any]]:
        """Translate all scanned skills in batched LLM requests (10 per request).

        When ``categories`` is provided the LLM also returns a ``category`` slug
        per skill (picked from our taxonomy) in the same call.
        """
        if not skills_data:
            return []
        items = [
            {
                'name': skill_data.get('name', ''),
                'description': skill_data.get('description', ''),
                'tag_hints': skill_data.get('tag_hints'),
                'source_lang': skill_data.get('source_language'),
            }
            for skill_data in skills_data
        ]
        return await translation_service.batch_translate_skill_metadata(
            items, concurrency=4, categories=categories,
        )

    async def _load_categories(self, db: AsyncSession) -> list[dict[str, Any]]:
        """Load marketplace categories as {slug, name} for LLM classification."""
        try:
            categories = await marketplace_category_dao.get_all(db)
        except Exception as exc:
            log.error(f"Failed to load marketplace categories for classification: {exc}")
            return []
        return [
            {'slug': cat.slug, 'name': getattr(cat, 'name', None)}
            for cat in categories
            if getattr(cat, 'slug', None)
        ]

    async def _sync_skill(
        self,
        db: AsyncSession,
        skill_data: dict[str, Any],
        translated: dict[str, Any],
    ) -> None:
        """
        Sync a single skill to database

        Args:
            db: Database session
            skill_data: Skill metadata
            translated: Pre-computed bilingual translation for this skill
        """
        skill_id = skill_data['skill_id']

        name = skill_data.get('name', '')

        tags_en = translation_service.normalize_tag_list(translated.get('tags_en'))
        tags_zh = translation_service.normalize_tag_list(translated.get('tags_zh'))
        tags = tags_en or tags_zh or translation_service.normalize_tag_list(skill_data.get('tag_hints'))

        # Check if skill exists
        existing_skill = await marketplace_skill_dao.get_by_id(db, skill_id)

        source_language = translated.get('source_language', skill_data.get('source_language'))
        # 正文双语：原文存源语言侧，另一侧翻译（变更门控，未变不重译）。
        body_en, body_zh = await self._resolve_bilingual_body(
            existing_skill, source_language, skill_data.get('body') or '',
        )
        # 文件清单：仅名称+大小，不含内容。
        files_json = json.dumps(skill_data.get('files') or [], ensure_ascii=False)

        # Prepare skill record
        skill_record = {
            'skill_id': skill_id,
            'namespace': skill_data.get('namespace'),
            'slug': skill_data.get('slug'),
            'name': name,
            'name_en': translated.get('name_en'),
            'name_zh': translated.get('name_zh'),
            'description_en': translated.get('description_en'),
            'description_zh': translated.get('description_zh'),
            'body_en': body_en,
            'body_zh': body_zh,
            'files': files_json,
            'source_language': source_language,
            'icon_url': await self._resolve_icon_url(db, skill_data),
            'emoji': skill_data.get('emoji') or translated.get('emoji'),
            'author_name': skill_data.get('author_name'),
            # 优先用技能自带 category（huanxing 目录名/frontmatter），否则用 LLM 分类；
            # 统一归一到权威 12 领域，未命中兜底 'other'（不再产生 NULL 分类）。
            'category': normalize_category(skill_data.get('category') or translated.get('category')),
            'tags': json.dumps(tags, ensure_ascii=False),
            'tags_en': json.dumps(tags_en or tags, ensure_ascii=False),
            'tags_zh': json.dumps(tags_zh or tags, ensure_ascii=False),
            'pricing_type': skill_data.get('pricing_type'),
            'price': skill_data.get('price'),
            'is_official': skill_data.get('is_official'),
            'is_common': skill_data.get('is_common', False),
            'is_private': skill_data.get('is_private', False),
            'status': 'published',
            'visibility': 'public',
            'source_type': skill_data.get('source_type'),
            'download_count': skill_data.get('download_count', 0),
            'repo_path': skill_data.get('repo_path'),
            'git_commit_hash': skill_data.get('git_commit_hash'),
            'synced_at': timezone.now(),
            'translated_at': timezone.now(),
        }

        if existing_skill:
            # Update existing skill
            await marketplace_skill_dao.update(db, existing_skill.id, UpdateMarketplaceSkillParam(**skill_record))
            db_skill_id = existing_skill.id
        else:
            # Create new skill
            await marketplace_skill_dao.create(db, CreateMarketplaceSkillParam(**skill_record))
            await db.flush()
            # Re-query to get the created skill
            new_skill = await marketplace_skill_dao.get_by_id(db, skill_id)
            db_skill_id = new_skill.id if new_skill else None

        # Sync versions
        versions = skill_data.get('versions', [])
        for version_data in versions:
            await self._sync_skill_version(db, db_skill_id, skill_id, version_data)

    async def _resolve_bilingual_body(
        self,
        existing_skill: MarketplaceSkill | None,
        source_language: str | None,
        body: str,
    ) -> tuple[str | None, str | None]:
        return await resolve_bilingual_body(existing_skill, source_language, body)

    async def _resolve_icon_url(self, db: AsyncSession, skill_data: dict[str, Any]) -> str | None:
        icon_url = skill_data.get('icon_url')
        icon_path = skill_data.get('icon_path')
        if icon_url or not icon_path:
            return icon_url
        try:
            return await marketplace_storage_service.upload_icon(
                db=db,
                item_type='skill',
                item_id=skill_data['skill_id'],
                content=Path(icon_path).read_bytes(),  # noqa: ASYNC240
                filename=Path(icon_path).name,
            )
        except Exception as exc:
            log.error(f"Failed to upload skill icon {icon_path}: {exc}")
            return None

    async def _sync_skill_version(
        self,
        db: AsyncSession,
        db_skill_id: int,
        skill_id: str,
        version_data: dict[str, Any],
    ) -> None:
        """
        Sync a skill version

        Args:
            db: Database session
            db_skill_id: Database skill ID
            skill_id: Skill ID
            version_data: Version metadata
        """
        version = version_data.get('version', '1.0.0')

        # Check if version exists
        existing_version = await marketplace_skill_version_dao.get_by_skill_and_version(
            db, skill_id, version
        )

        # Prepare version record
        version_record = {
            'skill_id': skill_id,
            'version': version,
            'changelog': version_data.get('changelog'),
            'package_url': version_data.get('package_url'),
            'file_hash': version_data.get('file_hash'),
            'file_size': version_data.get('file_size'),
            'is_latest': version_data.get('is_latest', True),
            'published_at': version_data.get('published_at') or timezone.now(),
        }

        if existing_version:
            # Update existing version
            await marketplace_skill_version_dao.update(
                db,
                existing_version.id,
                UpdateMarketplaceSkillVersionParam(**version_record),
            )
        else:
            # Create new version
            await marketplace_skill_version_dao.create(db, CreateMarketplaceSkillVersionParam(**version_record))


# Singleton instance
github_sync_service = GitHubSyncService()
