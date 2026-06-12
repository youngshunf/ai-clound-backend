from __future__ import annotations

import asyncio

from backend.app.hasn_growth.service.dispatch_service import growth_dispatch_service
from backend.app.hasn_growth.service.pipeline_service import lead_automation_pipeline_service
from backend.app.task.celery import celery_app
from backend.common.log import log
from backend.database.db import async_db_session


async def _run_job(job_id: int) -> dict:
    async with async_db_session.begin() as db:
        return await lead_automation_pipeline_service.run_job(db, job_id)


async def _archive_expired() -> int:
    async with async_db_session.begin() as db:
        return await lead_automation_pipeline_service.archive_expired(db)


def lead_automation_run_job(job_id: int) -> dict:
    """Run a lead automation collection job."""

    return asyncio.run(_run_job(job_id))


def lead_automation_archive_expired() -> dict[str, int]:
    """Archive expired lead contacts."""

    return {'archived_count': asyncio.run(_archive_expired())}


@celery_app.task(name='growth_dispatch_approved_outreach')
async def growth_dispatch_approved_outreach() -> str:
    """M6 发送 worker：扫一批 approved 触达按渠道闸门分发（设计 §8.3）。

    quiet hours / 微信 J1 / manual_assist 闸门 + 渠道不可达诚实降级，状态如实回写 sent/failed。
    任务名 'growth_dispatch_approved_outreach' 由 celery autodiscover 注册；调度入口在 beat.py。
    """
    async with async_db_session.begin() as db:
        stat = await growth_dispatch_service.dispatch_approved_batch(db)
    log.info(f'[GrowthDispatch] 发送 worker 完成: {stat}')
    return f'扫 {stat["scanned"]}，发出 {stat["sent"]}，失败 {stat["failed"]}，挂起(静默) {stat["queued_quiet_hours"]}'
