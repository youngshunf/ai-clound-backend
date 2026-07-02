"""技能市场定时任务

ClawHub 定时同步：每 3 天增量把 ClawHub 市场的热门技能同步到唤星技能市场。
- 本地/测试：默认抓取 Top 100（settings.MARKETPLACE_CLAWHUB_SYNC_LIMIT）
- 生产环境：在 .env 把 MARKETPLACE_CLAWHUB_SYNC_LIMIT 设为 0 表示全量同步
- 磁盘上限：MARKETPLACE_CLAWHUB_MAX_DISK_GB（默认 50GB），clawhub 目录占用达上限即暂停下载。

共享技能目录 reconcile：每 20 分钟 + common_skills revision bump 时触发（doc11 §6 B3），
把公共技能物化到云端 hermes 共享目录（内容寻址增量 + 下架 prune）。

调度入口在 backend/app/task/tasks/beat.py 的 LOCAL_BEAT_SCHEDULE，
任务名由 celery autodiscover 注册（见 task/celery.py）。
"""

from pathlib import Path

from backend.app.marketplace.service.clawhub_sync_service import clawhub_sync_service
from backend.app.task.celery import celery_app
from backend.common.log import log
from backend.core.conf import settings
from backend.database.db import async_db_session


@celery_app.task(name='marketplace_sync_clawhub')
async def marketplace_sync_clawhub() -> str:
    """ClawHub 定时同步任务（每 3 天执行一次）

    **增量**：不传 force —— 上游 latestVersion 未变的技能只刷人气计数（零下载、零翻译），
    仅新增 / 版本变更的技能才走元数据门控翻译 + 下载 + 正文翻译。没有新技能时整轮几乎零
    LLM/零带宽（避免周期同步反复全量重译烧 token）。force 全量重建仅留给 admin 手动端点。

    限额由 settings.MARKETPLACE_CLAWHUB_SYNC_LIMIT 决定（0 = 全量枚举）。
    sync_from_clawhub 内部自管事务（自带 db.commit），因此这里用普通会话而非 begin()。
    """
    async with async_db_session() as db:
        result = await clawhub_sync_service.sync_from_clawhub(db)

    if result.get('success'):
        synced = result.get('synced', 0)
        failed = result.get('failed', 0)
        unchanged = result.get('skipped_unchanged', 0)
        msg = f'ClawHub 定时同步完成: 成功 {synced} 个, 失败 {failed} 个, 版本未变跳过 {unchanged} 个'
        log.info(f'[ClawHubSync] {msg}')
        return msg

    error = result.get('error', 'unknown error')
    log.error(f'[ClawHubSync] ClawHub 定时同步失败: {error}')
    return f'ClawHub 定时同步失败: {error}'


@celery_app.task(name='marketplace_shared_skills_reconcile')
async def marketplace_shared_skills_reconcile() -> str:
    """公共技能 → 云端 hermes 共享目录 reconcile（doc11 §5.3.5 / §6 B3）。

    触发：celery beat 每 20 分钟兜底 + ``common_skills`` revision bump 即时 ``.delay()``。
    未配置 HERMES_SHARED_SKILLS_ROOT（该机器无 hermes sidecar）→ no-op。
    内容寻址增量：指纹一致的技能 kept 零下载，整轮无变更时≈零成本。
    """
    from backend.app.marketplace.service.common_skills_materialize_service import (
        reconcile_shared_common_skills,
    )

    root = settings.HERMES_SHARED_SKILLS_ROOT
    if not root:
        log.debug('[SharedSkills] HERMES_SHARED_SKILLS_ROOT 未配置（本机无 hermes sidecar），跳过')
        return 'skipped: HERMES_SHARED_SKILLS_ROOT not configured'

    async with async_db_session() as db:
        stats = await reconcile_shared_common_skills(db, Path(root))

    return (
        f"shared skills reconciled: revision={stats['revision']} "
        f"materialized={len(stats['materialized'])} kept={len(stats['kept'])} "
        f"pruned={len(stats['pruned'])} failed={len(stats['failed'])} "
        f"bytes={stats['bytes_downloaded']} duration_ms={stats['duration_ms']}"
    )
