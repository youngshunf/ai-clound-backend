"""Owner 记忆后台任务：pending 合并兜底重试 sweeper（MEMFIX-3）。

同步内联合并（contribute 热路径）若因 LLM 网关挂/余额不足失败，贡献留 pending。本 sweeper
周期性重跑合并，直到网关恢复（配合 failover 模型链，单模型挂掉沿链自动切换）。决策「保持同步
内联，只加重试兜底」——不改主路径，只补这条周期兜底，杜绝「采访完 coverage 永不更新」。

任务由 backend.app.task.celery.find_task_packages 自动发现（本文件名 tasks.py），
beat 调度见 backend/app/task/tasks/beat.py「Owner 记忆 pending 合并兜底重试」。
"""

from celery import shared_task

from backend.app.hasn_memory.service.memory_extraction_service import memory_extraction_service
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


@shared_task(name='memory_extraction_sweep')
async def memory_extraction_sweep() -> str:
    """单一云端记忆提取 worker（doc16 Phase C2）：扫有未提取消息的 owner，逐户提取写云端权威记忆。

    触发于消息上行（Phase A 已落 hasn_messages）+ 会话摘要（Phase B summary_checkpoint）。
    输入只取 owner 输入 + 任务结果/摘要，跳过 agent verbose；平台廉价模型、平台吸收成本；
    candidate schema + PolicyGate + confidence gate → semantic_fact。每户独立水位、增量幂等。
    """
    summary = await memory_extraction_service.sweep_extractions()
    msg = (
        f'云端记忆提取完成: 候选 {summary["candidate_owners"]} 户, '
        f'处理 {summary["processed"]} / 写入事实 {summary["written"]} / 失败 {summary["failed"]}'
    )
    log.info(f'[MemoryExtractionSweep] {msg}')
    return msg


@shared_task(name='peer_portrait_sweep')
async def peer_portrait_sweep() -> str:
    """Peer 画像合成 worker（doc17 PEERSYN-P4）：扫「有新 peer 事实但画像未追上」的 (owner, peer) 对，
    跨该 owner 全部分身聚合事实 → LLM 合成/基线演进一份画像 → 发 memory.peer_portrait.upserted 下行。

    紧跟提取管线之后跑：记忆提取写入 peer 事实（subject_kind='peer'）→ 本 sweep 方案B 脏判定发现
    「事实晚于上次合成」→（重）合成。逐对独立事务，单对 LLM 失败不拖垮其余、留下轮重试（零 fake）。
    同主人分身、无事实的对由 synthesize 内部跳过。
    """
    summary = await peer_portrait_service.sweep_peer_portraits()
    msg = (
        f'peer 画像合成完成: 候选 {summary["candidates"]} 对, '
        f'合成 {summary["synthesized"]} / 跳过 {summary["skipped"]} / 失败 {summary["failed"]}'
    )
    log.info(f'[PeerPortraitSweep] {msg}')
    return msg
