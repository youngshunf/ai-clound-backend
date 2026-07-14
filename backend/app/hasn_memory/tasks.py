"""记忆子系统后台 sweeper：owner 记忆 pending 合并兜底重试（MEMFIX-3）+ peer 画像合成（doc17）。

- `owner_memory_retry_pending_merges`：同步内联合并（contribute 热路径）若因 LLM 网关挂/余额不足
  失败，贡献留 pending，本 sweeper 周期重跑直到网关恢复。不改主路径，只补周期兜底。
- `peer_portrait_sweep`：扫「有新 peer 事实但画像未追上」的 (owner, peer) 对，跨该 owner 全部分身
  聚合事实 → 合成一份画像 → 下行。

> **doc18 退役**：原 `memory_extraction_sweep`（doc16 C2 独立 LLM 自动提取管线）已整条退役——
> 记忆写入收敛为「分身现场主动 hasn.memory.save + 主人事后管理」单一来源，不再有自动提取 worker。

任务由 backend.app.task.celery.find_task_packages 自动发现（本文件名 tasks.py），beat 调度见
backend/app/task/tasks/beat.py。
"""

from celery import shared_task

from backend.app.hasn_memory.service.owner_memory_service import owner_memory_service
from backend.app.hasn_memory.service.peer_portrait_service import peer_portrait_service
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


@shared_task(name='peer_portrait_sweep')
async def peer_portrait_sweep() -> str:
    """Peer 画像合成 worker（doc17 PEERSYN-P4）：扫「有新 peer 事实但画像未追上」的 (owner, peer) 对，
    跨该 owner 全部分身聚合事实 → LLM 合成/基线演进一份画像 → 发 memory.peer_portrait.upserted 下行。

    peer 事实上游来源为分身主动 hasn.memory.save（subject_kind='peer'，doc18 退役自动提取后单一来源）→
    本 sweep 方案B 脏判定发现「事实晚于上次合成」→（重）合成。逐对独立事务，单对 LLM 失败不拖垮其余、
    留下轮重试（零 fake）。同主人分身、无事实的对由 synthesize 内部跳过。
    """
    summary = await peer_portrait_service.sweep_peer_portraits()
    msg = (
        f'peer 画像合成完成: 候选 {summary["candidates"]} 对, '
        f'合成 {summary["synthesized"]} / 跳过 {summary["skipped"]} / 失败 {summary["failed"]}'
    )
    log.info(f'[PeerPortraitSweep] {msg}')
    return msg
