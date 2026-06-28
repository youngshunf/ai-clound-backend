"""Owner 记忆后台任务：pending 合并兜底重试 sweeper（MEMFIX-3）。

同步内联合并（contribute 热路径）若因 LLM 网关挂/余额不足失败，贡献留 pending。本 sweeper
周期性重跑合并，直到网关恢复（配合 failover 模型链，单模型挂掉沿链自动切换）。决策「保持同步
内联，只加重试兜底」——不改主路径，只补这条周期兜底，杜绝「采访完 coverage 永不更新」。

任务由 backend.app.task.celery.find_task_packages 自动发现（本文件名 tasks.py），
beat 调度见 backend/app/task/tasks/beat.py「Owner 记忆 pending 合并兜底重试」。
"""

from celery import shared_task

from backend.app.hasn_memory.service.owner_memory_service import owner_memory_service
from backend.common.log import log


@shared_task(name='owner_memory_retry_pending_merges')
async def owner_memory_retry_pending_merges() -> str:
    """周期扫描滞留 pending 的 owner 记忆贡献并重跑合并（同步内联失败的兜底重试）。"""
    summary = await owner_memory_service.sweep_pending_merges()
    msg = (
        f'owner 记忆 pending 合并兜底重试完成: 候选 {summary["candidates"]} 户, '
        f'合并 {summary["merged"]} / 无 pending(竞态) {summary["no_pending"]} / 失败 {summary["failed"]}'
    )
    log.info(f'[OwnerMemorySweep] {msg}')
    return msg
