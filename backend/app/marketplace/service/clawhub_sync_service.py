"""
ClawHub Sync Service

Syncs skills from ClawHub marketplace to Huanxing marketplace.
"""
import asyncio
import json
import shutil
import zipfile

from datetime import datetime
from pathlib import Path
from typing import Any

import httpx

from sqlalchemy import or_, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import load_only

from backend.app.marketplace.crud.crud_marketplace_skill import marketplace_skill_dao
from backend.app.marketplace.crud.crud_marketplace_skill_version import marketplace_skill_version_dao
from backend.app.marketplace.crud.crud_marketplace_sync_log import marketplace_sync_log_dao
from backend.app.marketplace.schema.marketplace_sync_log import (
    CreateMarketplaceSyncLogParam,
    UpdateMarketplaceSyncLogParam,
)
from backend.app.marketplace.service.category_taxonomy import normalize_category

# 复用 github_sync 已验证的元数据变更门控（同构、零行为差异），避免每轮全量重译元数据。
from backend.app.marketplace.service.github_sync_service import (
    metadata_unchanged,
    translation_from_existing,
)
from backend.app.marketplace.service.skill_content_extractor import (
    extract_skill_body,
    list_skill_files,
    raw_bilingual_body,
    resolve_bilingual_body,
)
from backend.app.marketplace.service.translation_service import translation_service
from backend.common.log import log
from backend.core.conf import settings


class ClawHubSyncService:
    """ClawHub sync service for marketplace skills"""

    def __init__(self) -> None:
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
        translate_body: bool = True,
        batch_commit_size: int = 50,
        resume: bool = False,
        require_engagement: bool = False,
    ) -> dict[str, Any]:
        """
        Sync skills from ClawHub

        Args:
            db: Database session
            force: 全量重建（admin 手动）。True 时**不做增量分区**——所有命中技能都重新
                下载 + 重新元数据翻译（绕过"上游版本未变就跳过"与元数据变更门控）。
                周期同步**不要** force（默认 False 即增量：版本未变只刷人气计数、零下载零翻译）。
            skill_ids: Specific skill IDs to sync (sync all if None)
            limit: Override the configured top-N cap for this run
                (None -> use settings default; 0 -> full sync, no cap)
            min_downloads: Only sync skills whose stats.downloads is strictly
                greater than this threshold (None -> use settings default;
                0 -> no threshold). 例如 100 = "下载量超过 100 才同步"。
            dry_run: 只评估不落库：枚举 + 按阈值过滤后，统计命中数量与磁盘占用预估，
                不下载、不翻译、不写库、不建同步日志。用于先确认规模再全量。
            translate_body: True（默认）逐技能翻译 SKILL.md 正文（双语，按 3500 字符
                分块，长文档多次 LLM）；False 时正文**原文填充**（detected 源语言侧存原文、
                另一侧 null，序列化器回退显示原文），**零正文 LLM**——大批量灌库时把每技能
                的 LLM 调用从「1 元数据 + N 正文块」降到「1 元数据」，名称/描述照常翻译。
            batch_commit_size: 每处理 N 个技能 commit 一次（崩溃只丢最近一批、进度可见、
                可断点续传）。
            resume: True 时跳过库中已存在的 clawhub slug（重跑只补未同步的，不重复翻译/下载）。
            require_engagement: True 时只同步"有真实人气"的技能（downloads>0 或 stars>0），
                丢弃 downloads=0 且 stars=0 的占位/冷门技能。取前 N 个真实人气技能时用它过滤噪音，
                避免 top-N 用更新时间凑数补满 0/0 技能。

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
            from sqlalchemy import desc, select

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
            filtered_skills = self._filter_skills(
                skills_data,
                effective_limit,
                effective_min_downloads,
                require_engagement=require_engagement,
            )

            # Sync to database（增量分区 + 逐个落库 + 磁盘硬闸 + 分批提交/断点续传）
            outcome = await self._sync_filtered_skills(
                db,
                filtered_skills,
                force=force,
                translate_body=translate_body,
                batch_commit_size=batch_commit_size,
                resume=resume,
            )
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
                'skipped_existing': outcome.get('skipped_existing', 0),
                'skipped_unchanged': outcome.get('skipped_unchanged', 0),
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
        *,
        force: bool = False,
        translate_body: bool = True,
        batch_commit_size: int = 50,
        resume: bool = False,
    ) -> dict[str, Any]:
        """逐个落库 filtered_skills；**增量分区** + 分批提交 + 断点续传 + 磁盘硬闸。

        按 downloads 降序处理（先收高价值）。增量（核心省 token/带宽）：先取库内 clawhub
        现有行，按「上游 latestVersion 已存在 + 已有正文/文件」把技能分成两组：
        - **跳过组**（version 未变）：只廉价刷新 download/star 人气计数，**零下载、零翻译**。
        - **处理组**（新增 / 版本变更 / ``force``）：才走元数据变更门控批量翻译 + 下载 + 正文翻译。
        ``force=True`` 时不分区，全部进处理组（admin 手动全量重建）。

        其余健壮性同前：
        - resume=True 先跳过库中已存在的 clawhub slug（重跑只补未同步的）。
        - 名称/描述/标签**批量预翻译**（变更门控，未变复用缓存零 LLM），正文按
          ``translate_body`` 决定翻不翻（原文填充时正文零 LLM）。
        - 每个技能在 SAVEPOINT 内落库：单个失败只回滚自己、不污染整批事务。
        - 每 ``batch_commit_size`` 个 ``commit`` 一次：崩溃只丢最近一批、进度可见、可续传。
        - 磁盘硬闸每批检查一次（避免每技能 rglob 的 O(n²) 开销）。
        """
        synced_count = 0
        failed_count = 0
        skipped_disk = 0
        skipped_existing = 0
        skipped_unchanged = 0
        errors: list[str] = []
        paused_reason: str | None = None
        clawhub_root = self.hub_local_path / 'clawhub'

        # 库内现有 clawhub 行（slug -> 元数据行，load_only 不取正文内容）+
        # 有正文的 skill_id 集合 + 各 skill_id 的库内最新版本号（增量判定用）。
        existing_by_slug = await self._existing_clawhub_skills_by_slug(db)
        body_skill_ids = await self._clawhub_skill_ids_with_body(db)
        latest_versions = await self._latest_versions_by_skill_id(
            db, [row.skill_id for row in existing_by_slug.values()]
        )

        # 断点续传：剔除库中已存在的 slug（不重复翻译/下载）。
        if resume:
            before = len(filtered_skills)
            filtered_skills = [s for s in filtered_skills if s.get('slug') not in existing_by_slug]
            skipped_existing = before - len(filtered_skills)
            if skipped_existing:
                log.info(
                    f"[clawhub] resume：跳过 {skipped_existing} 个已同步技能，"
                    f"待同步 {len(filtered_skills)} 个"
                )

        # 增量分区：上游版本未变 + 已有正文/文件 → 跳过组（仅刷计数）。force 时全部处理。
        process: list[dict[str, Any]] = []
        unchanged: list[tuple[dict[str, Any], Any]] = []
        if force:
            process = list(filtered_skills)
        else:
            for skill_data in filtered_skills:
                existing = existing_by_slug.get(skill_data.get('slug'))
                if existing is not None and self._is_version_unchanged(
                    existing, skill_data, latest_versions, body_skill_ids
                ):
                    unchanged.append((skill_data, existing))
                else:
                    process.append(skill_data)

        now = datetime.now()
        # 跳过组：廉价刷新人气计数（零 LLM / 零下载），让 download/star 不至于冻结。
        for skill_data, existing in unchanged:
            try:
                async with db.begin_nested():
                    await self._refresh_engagement(db, existing, skill_data, now)
                skipped_unchanged += 1
            except Exception as e:
                failed_count += 1
                errors.append(f"{skill_data.get('slug') or 'unknown'}(refresh): {e!s}")
                log.error(f"Failed to refresh engagement {skill_data.get('slug')}: {e}")
        if skipped_unchanged:
            await db.commit()  # 落地计数刷新，避免与处理组堆在一个大事务里
            log.info(
                f"[clawhub] 增量：{skipped_unchanged} 个版本未变只刷计数，"
                f"{len(process)} 个需处理"
            )

        total = len(process)
        commit_size = max(1, batch_commit_size)

        # 处理组：名称/描述/标签批量预翻译（变更门控，未变复用缓存零 LLM；缺失回退逐条）。
        prepared_map = await self._batch_prepare_metadata(process, existing_by_slug, force=force)

        for index, skill_data in enumerate(process):
            # 磁盘硬闸：每批起始检查一次（rglob 昂贵，不每技能跑）。
            if index % commit_size == 0:
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
            slug = skill_data.get('slug')
            try:
                # SAVEPOINT：单技能失败只回滚自己，不污染整批事务（async session
                # 一旦 flush 出错会进入 PendingRollback，savepoint 隔离避免连累后续）。
                async with db.begin_nested():
                    await self._sync_skill(
                        db,
                        skill_data,
                        existing=existing_by_slug.get(slug),
                        prepared=prepared_map.get(slug),
                        translate_body=translate_body,
                    )
                synced_count += 1
            except Exception as e:
                failed_count += 1
                errors.append(f"{slug or 'unknown'}: {e!s}")
                log.error(f"Failed to sync skill {slug}: {e}")

            # 分批提交：每 commit_size 个落盘一次。
            if (index + 1) % commit_size == 0:
                try:
                    await db.commit()
                    log.info(
                        f"[clawhub] 进度 {index + 1}/{total} 已提交"
                        f"（synced={synced_count} failed={failed_count}）"
                    )
                except Exception as e:
                    await db.rollback()
                    errors.append(f"batch_commit@{index + 1}: {e!s}")
                    log.error(f"[clawhub] 分批提交失败 @ {index + 1}: {e}")

        return {
            'synced': synced_count,
            'failed': failed_count,
            'skipped_disk': skipped_disk,
            'skipped_existing': skipped_existing,
            'skipped_unchanged': skipped_unchanged,
            'errors': errors,
            'paused_reason': paused_reason,
        }

    async def _existing_clawhub_skills_by_slug(self, db: AsyncSession) -> dict[str, Any]:
        """库中现有 clawhub 技能：slug -> 行（load_only 只取增量判定/门控所需列，不取正文）。

        正文 body_en/body_zh 不在这里加载（可能很大）——是否有正文改用
        ``_clawhub_skill_ids_with_body`` 的轻量集合判断，避免把全部技能正文读进内存。
        """
        from backend.app.marketplace.model import MarketplaceSkill

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
                    MarketplaceSkill.source_language,
                    MarketplaceSkill.tags_en,
                    MarketplaceSkill.tags_zh,
                    MarketplaceSkill.emoji,
                    MarketplaceSkill.category,
                    MarketplaceSkill.author_name,
                    MarketplaceSkill.repo_path,
                )
            )
            .where(MarketplaceSkill.source_type == 'clawhub')
        )
        result = await db.execute(stmt)
        return {row.slug: row for row in result.scalars().all() if row.slug}

    async def _clawhub_skill_ids_with_body(self, db: AsyncSession) -> set[str]:
        """有正文（body_en 或 body_zh 非空）的 clawhub skill_id 集合（只判存在，不取内容）。"""
        from backend.app.marketplace.model import MarketplaceSkill

        stmt = select(MarketplaceSkill.skill_id).where(
            MarketplaceSkill.source_type == 'clawhub',
            or_(MarketplaceSkill.body_en.isnot(None), MarketplaceSkill.body_zh.isnot(None)),
        )
        result = await db.execute(stmt)
        return {row for row in result.scalars().all() if row}

    async def _latest_versions_by_skill_id(
        self, db: AsyncSession, skill_ids: list[str]
    ) -> dict[str, str]:
        """库内各 clawhub 技能的最新版本号：skill_id -> version（is_latest=True）。"""
        if not skill_ids:
            return {}
        from backend.app.marketplace.model import MarketplaceSkillVersion

        stmt = select(MarketplaceSkillVersion.skill_id, MarketplaceSkillVersion.version).where(
            MarketplaceSkillVersion.skill_id.in_(skill_ids),
            MarketplaceSkillVersion.is_latest.is_(True),
        )
        result = await db.execute(stmt)
        return {skill_id: version for skill_id, version in result.all() if skill_id}

    @staticmethod
    def _is_version_unchanged(
        existing: Any,
        skill_data: dict[str, Any],
        latest_versions: dict[str, str],
        body_skill_ids: set[str],
    ) -> bool:
        """上游 latestVersion 与库内最新版本一致、且已有正文+本地路径 → 可整条跳过。

        三者缺一不可：没有上游版本号无从比对；没有正文/repo_path 说明上次没下全，
        必须重新下载补齐（不能只刷计数）。命中即「内容未变」，本轮零下载零翻译。
        """
        upstream_version = (skill_data.get('latestVersion') or {}).get('version')
        if not upstream_version:
            return False
        if existing.skill_id not in body_skill_ids:
            return False
        if not getattr(existing, 'repo_path', None):
            return False
        return latest_versions.get(existing.skill_id) == upstream_version

    async def _refresh_engagement(
        self, db: AsyncSession, existing: Any, skill_data: dict[str, Any], now: datetime
    ) -> None:
        """版本未变时只刷新人气计数（download/star）+ synced_at（部分 UPDATE，零 LLM）。"""
        stats = skill_data.get('stats', {})
        await marketplace_skill_dao.update_model(
            db,
            existing.id,
            {
                'download_count': stats.get('downloads', 0),
                'star_count': stats.get('stars', 0),
                'synced_at': now,
            },
        )

    @staticmethod
    def _bilingual_metadata(
        translated: dict[str, Any], *, name: str, description: str
    ) -> tuple[str | None, str | None, str | None, str | None]:
        """name/description 源语言侧存**原文逐字**、另一侧存 LLM 译文。

        与正文门控 ``resolve_bilingual_body`` / github_sync 同构。否则源侧存 LLM 重排
        文本（改标点/去换行），下次同步源文逐字比对永不命中 → 元数据变更门控失效、每轮全量重译。
        """
        name_en = translated.get('name_en')
        name_zh = translated.get('name_zh')
        description_en = translated.get('description_en')
        description_zh = translated.get('description_zh')
        src = translated.get('source_language')
        if src == 'zh':
            name_zh, description_zh = name, description
        elif src == 'en':
            name_en, description_en = name, description
        return name_en, name_zh, description_en, description_zh

    async def _batch_prepare_metadata(
        self,
        skills: list[dict[str, Any]],
        existing_by_slug: dict[str, Any],
        *,
        force: bool = False,
    ) -> dict[str, dict[str, Any]]:
        """批量预翻译 name/description/tags（多技能拼一次 LLM），返回 slug -> 译文 dict。

        **变更门控**（核心省 token，与 github_sync 同构）：源文（name/description）与库内
        现有译文源文一致的技能直接复用缓存译文（``translation_from_existing``，零 LLM），
        只把真改动 / 新增 / ``force`` 的技能送 LLM（``batch_translate_skill_metadata``，
        默认 10 个/批）。整体异常时回退逐条（``_sync_skill`` 兜底 ``translate_skill_metadata``）。
        """
        if not skills:
            return {}
        prepared: dict[str, dict[str, Any]] = {}
        pending_items: list[dict[str, Any]] = []
        pending_slugs: list[str | None] = []
        for s in skills:
            slug = s.get('slug')
            existing = existing_by_slug.get(slug)
            scanned = {
                'name': s.get('displayName') or slug,
                'description': s.get('summary', '') or '',
            }
            if (not force) and existing is not None and metadata_unchanged(scanned, existing):
                prepared[slug] = translation_from_existing(existing)
            else:
                pending_items.append(
                    {
                        'name': scanned['name'],
                        'description': scanned['description'],
                        'tag_hints': self._extract_tag_hints(s),
                        'source_lang': None,
                    }
                )
                pending_slugs.append(slug)

        if pending_items:
            batch_size = int(getattr(settings, 'TRANSLATION_BATCH_SIZE', 10) or 10)
            concurrency = int(getattr(settings, 'MARKETPLACE_CLAWHUB_TRANSLATE_CONCURRENCY', 3) or 3)
            try:
                results = await translation_service.batch_translate_skill_metadata(
                    pending_items,
                    batch_size=batch_size,
                    concurrency=concurrency,
                )
            except Exception as e:
                log.warning(f"[clawhub] 批量元数据翻译失败，回退逐条翻译：{e}")
                results = [None] * len(pending_items)
            prepared.update({slug: result for slug, result in zip(pending_slugs, results) if slug and result})

        log.info(
            f"[clawhub] 元数据：{len(pending_items)} 需译 / "
            f"{len(skills) - len(pending_items)} 复用缓存"
        )
        return prepared

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

        # 关 keep-alive（每页用新连接）：观测到 clawhub 偶发把某条连接停在「已建立但
        # 不回数据」的半开态，此时复用该连接会一直阻塞，且 httpx 自身的 read timeout
        # 不一定触发 → 整段枚举僵死。每页新连接 + 下方 asyncio.wait_for 硬超时双保险。
        limits = httpx.Limits(max_keepalive_connections=0)
        timeout = httpx.Timeout(30.0, connect=10.0)
        async with httpx.AsyncClient(timeout=timeout, limits=limits) as client:
            for page_idx in range(max_pages):
                params: dict[str, Any] = {'pageSize': page_size}
                if cursor:
                    params['cursor'] = cursor

                # 每页最多 3 次尝试；asyncio.wait_for 是兜底硬超时——即便 httpx 的 read
                # timeout 因半开连接没触发，wait_for 也会在 25s 强制取消该请求并换新连接重试。
                data: dict[str, Any] | None = None
                for attempt in range(3):
                    try:
                        response = await asyncio.wait_for(
                            client.get(f"{self.clawhub_api_url}/skills", params=params),
                            timeout=25.0,
                        )
                        response.raise_for_status()
                        data = response.json()
                        break
                    except Exception as e:
                        log.warning(
                            f"[clawhub] 枚举第 {page_idx + 1} 页失败"
                            f"（尝试 {attempt + 1}/3, cursor={cursor!r}）：{type(e).__name__}: {e}"
                        )
                        if attempt < 2:
                            await asyncio.sleep(2.0 * (attempt + 1))

                if data is None:
                    log.error(
                        f"[clawhub] 枚举第 {page_idx + 1} 页连续 3 次失败，终止枚举（已累计 {len(skills)} 个）"
                    )
                    break

                items = data.get('items', [])
                if not items:
                    break
                skills.extend(items)
                log.info(f"[clawhub] 枚举进度：第 {page_idx + 1} 页 +{len(items)} 个，累计 {len(skills)}")

                cursor = data.get('nextCursor')
                if not cursor or cursor in seen_cursors:
                    break
                seen_cursors.add(cursor)

        log.info(f"[clawhub] 枚举完成：共 {len(skills)} 个技能")
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

    @staticmethod
    def _stars_of(skill: dict[str, Any]) -> int:
        return (skill.get('stats') or {}).get('stars') or 0

    def _filter_skills(
        self,
        skills: list[dict[str, Any]],
        limit: int | None = None,
        min_downloads: int = 0,
        require_engagement: bool = False,
    ) -> list[dict[str, Any]]:
        """
        Filter skills based on sync criteria

        Args:
            skills: List of skill data
            limit: Top-N cap after sorting (falsy / <=0 -> full sync, no cap)
            min_downloads: 下载量阈值，只保留 stats.downloads **严格大于** 该值的技能
                （<=0 -> 不设阈值，全收，向后兼容旧默认同步）。例如 100 = "下载量超过 100 才同步"。
            require_engagement: 只保留"有真实人气"的技能，即 downloads>0 **或** stars>0
                （丢弃 downloads=0 且 stars=0 的占位/冷门技能）。与 min_downloads 正交叠加：
                取前 N 个真实人气技能时用它过滤噪音，避免按更新时间凑数补满 0/0 技能。

        Returns:
            Filtered list of skills（按 downloads 降序，再 stars、再更新时间）
        """
        threshold = max(min_downloads or 0, 0)
        eligible = (
            [s for s in skills if self._downloads_of(s) > threshold]
            if threshold > 0
            else list(skills)
        )
        if require_engagement:
            eligible = [
                s
                for s in eligible
                if self._downloads_of(s) > 0 or self._stars_of(s) > 0
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

    async def _sync_skill(
        self,
        db: AsyncSession,
        clawhub_skill: dict[str, Any],
        *,
        existing: Any | None = None,
        prepared: dict[str, Any] | None = None,
        translate_body: bool = True,
    ) -> None:
        """
        Sync a single skill from ClawHub to database

        Args:
            db: Database session
            clawhub_skill: ClawHub skill data
            existing: 库内现有行（按 slug 预加载，load_only）。命中时复用其 ``author_name``
                作 owner（省掉每技能 detail API 调用）+ ``skill_id``；None 时按新技能处理
                （查 detail API 取 owner）。
            prepared: 预翻译好的元数据（name_en/zh、description_en/zh、source_language、
                tags_en/zh、emoji）。批量同步先 ``_batch_prepare_metadata`` 拼一次 LLM，
                这里直接复用；None 时回退逐条 ``translate_skill_metadata``（单技能/webhook 路径）。
            translate_body: True 翻译 SKILL.md 正文（双语）；False 正文原文填充（零正文 LLM）。
        """
        slug = clawhub_skill['slug']

        # owner：已存技能复用库内 author_name（省掉每技能 detail API 调用）；新技能才查详情。
        if existing is not None:
            owner_handle = existing.author_name or 'community'
            skill_id = existing.skill_id
            namespace = existing.namespace or f"clawhub/{owner_handle}"
        else:
            owner_handle = await self._get_skill_owner(slug)
            skill_id = f"clawhub/{owner_handle}/{slug}"
            namespace = f"clawhub/{owner_handle}"
        existing_skill = existing

        # Get stats
        stats = clawhub_skill.get('stats', {})

        # Translate name and description
        name = clawhub_skill.get('displayName') or slug
        description = clawhub_skill.get('summary', '') or ''

        # 批量预翻译命中则复用（省 LLM）；否则逐条翻译兜底。
        translated = prepared or await translation_service.translate_skill_metadata(
            name=name,
            description=description,
            tag_hints=self._extract_tag_hints(clawhub_skill),
        )
        tags_en = translation_service.normalize_tag_list(translated.get('tags_en'))
        tags_zh = translation_service.normalize_tag_list(translated.get('tags_zh'))
        tags = tags_en or tags_zh or [slug]
        # 源语言侧存原文逐字（让下次同步的元数据变更门控能命中）。
        name_en, name_zh, description_en, description_zh = self._bilingual_metadata(
            translated, name=name, description=description,
        )

        # Map category based on slug or summary (keyword matching, no LLM)
        category = await self._classify_skill(db, name, description)

        # Prepare skill record
        from backend.app.marketplace.schema.marketplace_skill import (
            CreateMarketplaceSkillParam,
            UpdateMarketplaceSkillParam,
        )

        now = datetime.now()
        skill_data = {
            'skill_id': skill_id,
            'namespace': namespace,
            'slug': slug,
            'name': name,
            'name_en': name_en,
            'name_zh': name_zh,
            'description_en': description_en,
            'description_zh': description_zh,
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
                        translate_body=translate_body,
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
            await self._sync_skill_version(
                db, skill_id_for_version, latest_version, translate_body=translate_body,
            )

    async def _sync_skill_version(
        self,
        db: AsyncSession,
        skill_id: str,
        version_data: dict[str, Any],
        *,
        translate_body: bool = True,
    ) -> None:
        """
        Sync a skill version

        Args:
            db: Database session
            skill_id: Skill ID (e.g., "clawhub/my-skill")
            version_data: Version data from ClawHub
            translate_body: False 时 changelog 也算「内容」，原文填充不翻译（零 LLM）。
        """
        version = version_data.get('version', '1.0.0')

        # Check if version exists
        existing_version = await marketplace_skill_version_dao.get_by_skill_and_version(
            db, skill_id, version
        )

        # changelog 变更门控：该版本已存在 → 复用库内 changelog（**零 LLM**，避免每轮重译）；
        # 仅真正新版本且 translate_body 才送 LLM；translate_body=False 时原文填充。
        changelog = version_data.get('changelog', '')
        if existing_version is not None:
            changelog_en = existing_version.changelog
        elif changelog and translate_body:
            translated_changelog = await translation_service.translate_skill_metadata(
                description=changelog
            )
            changelog_en = translated_changelog['description_en']
        elif changelog:
            changelog_en = changelog  # 原文填充（changelog_en 作默认展示）
        else:
            changelog_en = None

        # Convert timestamp to datetime
        created_at = version_data.get('createdAt')
        if created_at:
            released_at = datetime.fromtimestamp(created_at / 1000)  # ClawHub uses milliseconds
        else:
            released_at = datetime.now()

        # Prepare version record
        from backend.app.marketplace.schema.marketplace_skill_version import (
            CreateMarketplaceSkillVersionParam,
            UpdateMarketplaceSkillVersionParam,
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
        if available_categories:
            return available_categories[0]
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
                    log.info("Extracting skill file...")
                    with zipfile.ZipFile(zip_file, 'r') as zip_ref:
                        zip_ref.extractall(skill_dir)

                    # Remove the zip file after extraction
                    zip_file.unlink()
                    log.info("Extracted and removed zip file")
                else:
                    log.warning("Downloaded file is not a zip file, keeping as-is")

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
        *,
        translate_body: bool = True,
    ) -> tuple[str | None, str | None, str]:
        """从解压后的技能目录提取正文(双语)+文件清单 JSON。

        与 github_sync 同源（共享 skill_content_extractor）。SKILL.md 缺失或目录
        不存在时正文留空、文件清单为 ``[]``（零 fake，如实反映“没有正文”）。文件清单
        仅含名称+大小，不含内容。

        translate_body=True 时调 ``resolve_bilingual_body`` 翻译正文（双语，分块多次
        LLM）；False 时调 ``raw_bilingual_body`` 原文填充（源语言侧存原文、另一侧 null，
        序列化器回退显示原文）——大批量灌库省掉正文逐块翻译的几万次 LLM。
        """
        skill_md = skill_dir / 'SKILL.md'
        body = ''
        if skill_md.exists():
            try:
                body = extract_skill_body(skill_md.read_text(encoding='utf-8'))
            except OSError as exc:
                log.warning(f"Failed to read SKILL.md at {skill_md}: {exc}")
        files = list_skill_files(skill_dir) if skill_dir.exists() else []  # noqa: ASYNC240
        if translate_body:
            body_en, body_zh = await resolve_bilingual_body(existing_skill, source_language, body)
        else:
            body_en, body_zh = raw_bilingual_body(source_language, body)
        return body_en, body_zh, json.dumps(files, ensure_ascii=False)

    @staticmethod
    def _extract_tag_hints(clawhub_skill: dict[str, Any]) -> list[str]:
        tags = clawhub_skill.get('tags')
        if isinstance(tags, (list, dict)):
            normalized = [str(tag).strip() for tag in tags if str(tag).strip()]
        elif isinstance(tags, str):
            normalized = [tag.strip() for tag in tags.split(',') if tag.strip()]
        else:
            normalized = []

        return translation_service.normalize_tag_list(normalized)


# Singleton instance
clawhub_sync_service = ClawHubSyncService()
