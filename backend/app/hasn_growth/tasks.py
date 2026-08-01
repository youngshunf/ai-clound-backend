from __future__ import annotations

from datetime import timedelta
from typing import Any

import sqlalchemy as sa

from backend.app.hasn_growth.model.lead_collection_job import LeadCollectionJob
from backend.app.hasn_growth.service.dispatch_service import growth_dispatch_service
from backend.app.hasn_growth.service.growth_project_provision_service import (
    growth_project_provision_service,
)
from backend.app.hasn_growth.service.pipeline_service import lead_automation_pipeline_service
from backend.app.task.celery import celery_app
from backend.common.log import log
from backend.database.db import async_db_session
from backend.utils.timezone import timezone


def _collection_job_for_update(job_id: int) -> sa.Select[tuple[LeadCollectionJob]]:
    """按主键锁定采集作业，串行化重复投递。"""
    return sa.select(LeadCollectionJob).where(LeadCollectionJob.id == job_id).with_for_update()


async def _run_collection_job_impl(job_id: int) -> dict[str, Any]:
    """采集 job 执行核心（可被测试直接调用，不经 Celery）。

    幂等守卫：仅处理仍 ``pending`` 的 job——重复投递（Celery at-least-once）/ owner 已手动
    跑过 / 已终态的不重复采集（避免重复消耗 firecrawl 额度、重复写 raw_record）。run_job 内部
    把 status 置 running→终态，失败状态如实落库（零 fake）。worker 以无 user_id 上下文运行
    （job 在 collect.start / owner 建时已鉴权），run_job 的 owner 权限检查对 user_id=None 短路跳过。
    """
    async with async_db_session.begin() as db:
        job = (await db.execute(_collection_job_for_update(job_id))).scalar_one_or_none()
        if job is None:
            log.warning(f'[GrowthCollect] 采集 job 不存在，跳过: job_id={job_id}')
            return {'job_id': job_id, 'skipped': 'not_found'}
        if job.status != 'pending':
            log.info(f'[GrowthCollect] 采集 job 非 pending（{job.status}），跳过重复执行: job_id={job_id}')
            return {'job_id': job_id, 'skipped': job.status}
        return await lead_automation_pipeline_service.run_job(db, job_id)


async def _archive_expired() -> int:
    async with async_db_session.begin() as db:
        return await lead_automation_pipeline_service.archive_expired(db)


@celery_app.task(
    name='lead_automation_run_job',
    bind=True,
    acks_late=False,
    reject_on_worker_lost=False,
)
async def lead_automation_run_job(self: Any, job_id: int) -> dict[str, Any]:
    """采集执行 worker — ``lead_automation_run_job.delay(job_id)`` 触发（设计 07 §6.2 / 方案A）。

    分身调 ``hasn.growth.collect.start`` 建 pending job 后，由 handler 的 after_commit 钩子入队
    本任务异步执行采集（firecrawl→清洗→去重→入库），不阻塞 MCP 工具调用。任务名
    ``lead_automation_run_job`` 由 celery autodiscover（``find_task_packages`` 扫 tasks.py）注册。
    """
    return await _run_collection_job_impl(job_id)


@celery_app.task(name='lead_automation_reconcile_pending')
async def lead_automation_reconcile_pending() -> dict[str, int]:
    """重投滞留的 pending 采集作业，弥补 early ACK 后的 worker 退出窗口。"""
    cutoff = timezone.now() - timedelta(minutes=2)
    async with async_db_session() as db:
        job_ids = list(
            (
                await db.execute(
                    sa
                    .select(LeadCollectionJob.id)
                    .where(
                        LeadCollectionJob.status == 'pending',
                        LeadCollectionJob.created_time <= cutoff,
                    )
                    .order_by(LeadCollectionJob.created_time.asc())
                    .limit(100)
                )
            ).scalars()
        )

    enqueued = 0
    for job_id in job_ids:
        try:
            lead_automation_run_job.delay(job_id)
        except Exception as exc:  # noqa: PERF203 — 每个作业必须独立记录入队失败
            log.warning(
                f'[GrowthCollect] pending 作业恢复入队失败: job_id={job_id} error_type={exc.__class__.__name__}'
            )
        else:
            enqueued += 1
    return {'scanned': len(job_ids), 'enqueued': enqueued}


@celery_app.task(name='lead_automation_archive_expired', bind=True)
async def lead_automation_archive_expired(self: Any) -> dict[str, int]:
    """保留期归档 worker（设计 05 / 07 §5.0）——归档过期线索为匿名化（PIPL/GDPR）。

    可由 beat 定时调度（任务名 ``lead_automation_archive_expired``）；当前留任务入口，beat 接入
    与采集计费节奏对齐时再加 schedule。
    """
    archived = await _archive_expired()
    return {'archived_count': archived}


@celery_app.task(
    name='growth_dispatch_approved_outreach',
    acks_late=False,
    reject_on_worker_lost=False,
)
async def growth_dispatch_approved_outreach() -> str:
    """M6 发送 worker：扫一批 approved 触达按渠道闸门分发（设计 §8.3）。

    quiet hours / 微信 J1 / manual_assist 闸门 + 渠道不可达诚实降级，状态如实回写 sent/failed。
    任务名 'growth_dispatch_approved_outreach' 由 celery autodiscover 注册；调度入口在 beat.py。
    """
    async with async_db_session.begin() as db:
        stat = await growth_dispatch_service.dispatch_approved_batch(db)
    log.info(f'[GrowthDispatch] 发送 worker 完成: {stat}')
    return f'扫 {stat["scanned"]}，发出 {stat["sent"]}，失败 {stat["failed"]}，挂起(静默) {stat["queued_quiet_hours"]}'


@celery_app.task(name='growth_project_provision')
async def growth_project_provision(growth_project_id: str) -> dict[str, Any]:
    """从可靠步骤表继续开通；失败按真实错误进入退避重试。"""
    result = await growth_project_provision_service.run(growth_project_id)
    retry_seconds = result.get('retry_in_seconds')
    if isinstance(retry_seconds, int) and retry_seconds > 0:
        try:
            growth_project_provision.apply_async(
                args=(growth_project_id,),
                countdown=retry_seconds,
            )
        except Exception as exc:
            log.warning(
                f'[GrowthProvision] 自动重试入队失败，等待 reconcile: '
                f'growth_project_id={growth_project_id} error_type={exc.__class__.__name__}'
            )
    return result


@celery_app.task(name='growth_project_provision_reconcile')
async def growth_project_provision_reconcile() -> dict[str, int]:
    """兜底重投到期失败与超时 running，修复 worker 崩溃留下的中间态。"""

    def enqueue_one(growth_project_id: str) -> bool:
        try:
            growth_project_provision.delay(growth_project_id)
        except Exception as exc:
            log.warning(
                f'[GrowthProvision] reconcile 入队失败: '
                f'growth_project_id={growth_project_id} error_type={exc.__class__.__name__}'
            )
            return False
        return True

    project_ids = await growth_project_provision_service.due_for_reconcile()
    enqueued = sum(enqueue_one(project_id) for project_id in project_ids)
    return {'scanned': len(project_ids), 'enqueued': enqueued}
