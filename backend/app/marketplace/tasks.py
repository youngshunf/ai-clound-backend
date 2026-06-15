"""技能市场定时任务

ClawHub 定时同步：每 8 小时把 ClawHub 市场的热门技能同步到唤星技能市场。
- 本地/测试：默认抓取 Top 100（settings.MARKETPLACE_CLAWHUB_SYNC_LIMIT）
- 生产环境：在 .env 把 MARKETPLACE_CLAWHUB_SYNC_LIMIT 设为 0 表示全量同步

调度入口在 backend/app/task/tasks/beat.py 的 LOCAL_BEAT_SCHEDULE，
任务名 'marketplace_sync_clawhub' 由 celery autodiscover 注册（见 task/celery.py）。
"""

from backend.app.marketplace.service.clawhub_sync_service import clawhub_sync_service
from backend.app.task.celery import celery_app
from backend.common.log import log
from backend.database.db import async_db_session


@celery_app.task(name='marketplace_sync_clawhub')
async def marketplace_sync_clawhub() -> str:
    """ClawHub 定时同步任务（每 8 小时执行一次）

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
