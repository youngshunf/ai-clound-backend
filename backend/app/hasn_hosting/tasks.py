"""无头 hasn-node 托管 · 订阅生命周期 sweep（设计 §7 生命周期表 / D-14）。

`cloud_node_service.mark_retention()` 在 H4 就写好了，但一直**没有调用方**——「订阅到期进 30 天
保留期、逾期销毁、续订恢复」这套语义等于没落地。本文件就是那个调用方，每日一轮四个动作：

1. **订阅到期 → 进保留期**：主人已失去云端节点资格 → 真停容器 → `stopped` + `retain_until=now+30d`
   → `stopped` 事件 → 通知主人（含数据保留截止日期）
2. **续订恢复**：`retain_until` 非空但资格已恢复 → 清 `retain_until` → 启容器 → `started` 事件
3. **逾期 → 销毁**：`retain_until` 已过且仍 `stopped` → 销毁前再通知一次 → 走
   `cloud_node_service.purge_node()` 的完整删除流程（**凭据先吊销**）
4. **到期前提醒**：订阅到期前 7/3/1 天各提醒一次，文案说清「云端节点会停止，数据保留 30 天」

**动作 2 排在动作 3 之前**（不是笔误）：主人在最后一天续订时，恢复必须先把 `retain_until` 清掉，
否则同一轮里销毁动作会把刚续费用户的数据卷删掉。动作 3 内部另有一道资格复核兜底。

**零 fake（D-13）**：hosting-agent 不可达时该节点本轮**整个跳过**并 `warn`，绝不把状态改成
「看起来正常」的值——库里写 stopped、容器还在跑，或者库里写 started、容器根本没起来，都是造假。
返回计数字典严格区分 `succeeded` / `skipped_agent_unreachable` / `failed`。

**幂等**：每个动作的判据都是它自己写完之后立刻不再成立的状态（`stopped` / `retain_until IS NULL`
/ `deleted`），同一轮跑两次第二次全为 0；单节点事务内还会用 `FOR UPDATE SKIP LOCKED` 复核判据，
多副本 beat 并发也不会重复动手。
"""

from __future__ import annotations

import math

from datetime import datetime, timedelta
from typing import TYPE_CHECKING, Any

from celery import shared_task
from sqlalchemy import select

from backend.app.hasn_hosting.constants import (
    EVENT_FAILED,
    EVENT_STARTED,
    EVENT_STOPPED,
    FAILURE_INTERNAL_ERROR,
    NODE_STATUS_DELETED,
    NODE_STATUS_DELETING,
    NODE_STATUS_FAILED,
    NODE_STATUS_STARTING,
    NODE_STATUS_STOPPED,
)
from backend.app.hasn_hosting.model import HasnCloudNodes
from backend.app.hasn_hosting.provider.agent_client import HostingAgentError, hosting_agent_provider
from backend.app.hasn_hosting.service.cloud_node_service import cloud_node_service
from backend.common.log import log
from backend.database.db import async_db_session
from backend.utils.timezone import timezone

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession

#: 订阅到期后的数据保留天数（D-14）
RETENTION_DAYS: int = 30

#: 到期前提醒阈值（天）：与 `billing/tasks.py` 的 `_EXPIRY_REMINDER_DAYS` 同形。
#: sweep 每日一轮、到期时间固定，剩余天数逐日递减 → 每个阈值恰好命中一次，不重不漏。
EXPIRY_REMINDER_DAYS: tuple[int, ...] = (7, 3, 1)

#: 通知深链：续订入口就是费用与账单中心（URI 规范 §3.1 已登记域，不新造）
_BILLING_CENTER_URI = 'hasn://billing/center'

#: 「宿主不可达」的错误码集合——这几类说明我们**根本没够到** agent，容器真实状态未知
_UNREACHABLE_CODES = ('service_unconfigured', 'upstream_timeout', 'upstream_error')
#: 网关级 HTTP 状态：agent 应答了但它自己够不到 docker，同样属于「容器真实状态未知」
_UNREACHABLE_STATUS = (502, 503, 504)


def _is_agent_unreachable(exc: HostingAgentError) -> bool:
    """本次失败是否属于「没够到宿主」——是则本轮跳过，不是则说明 agent 明确拒绝了动作。"""
    return exc.code in _UNREACHABLE_CODES or exc.status_code in _UNREACHABLE_STATUS


def _days_until(when: datetime, now: datetime) -> int:
    """剩余到期天数（向上取整）：不足 1 天算 1 天，已过期返回 0。口径对齐 `billing/tasks.py`。"""
    seconds = (when - now).total_seconds()
    if seconds <= 0:
        return 0
    return math.ceil(seconds / 86400)


def _new_counters() -> dict[str, int]:
    """本轮计数器。三类口径分明：成功迁移 / 因宿主不可达跳过 / 真失败。"""
    return {
        'retention_marked': 0,
        'retention_skipped_agent_unreachable': 0,
        'retention_failed': 0,
        'restored': 0,
        'restore_failed': 0,
        'purged': 0,
        # `purged` 的子集：节点已退役但容器/卷销毁调用失败，残留待宿主巡检回收（如实暴露，不掩盖）
        'purge_agent_error': 0,
        'purge_skipped_agent_unreachable': 0,
        'purge_failed': 0,
        'reminded': 0,
        # 汇总（不含 reminded：提醒不改变任何节点状态）
        'succeeded': 0,
        'skipped_agent_unreachable': 0,
        'failed': 0,
    }


def _roll_up(counters: dict[str, int]) -> dict[str, int]:
    """把三个动作的分项汇总成 succeeded / skipped_agent_unreachable / failed 三类。"""
    counters['succeeded'] = counters['retention_marked'] + counters['restored'] + counters['purged']
    counters['skipped_agent_unreachable'] = (
        counters['retention_skipped_agent_unreachable'] + counters['purge_skipped_agent_unreachable']
    )
    counters['failed'] = counters['retention_failed'] + counters['restore_failed'] + counters['purge_failed']
    return counters


async def _lock_node(db: AsyncSession, node_id: str) -> HasnCloudNodes | None:
    """行级加锁重读托管行；被别的 beat 副本占着就返回 None（本轮让给它，不重复动手）。"""
    return (
        await db.execute(
            select(HasnCloudNodes).where(HasnCloudNodes.node_id == node_id).with_for_update(skip_locked=True)
        )
    ).scalar_one_or_none()


# ─── 通知（统一通知系统 emit·commerce 类·dedupe_key 去重） ───


async def _emit_hosting_notice(
    db: AsyncSession,
    *,
    recipient_id: str,
    node_id: str,
    notice_type: str,
    title: str,
    body: str,
    payload: dict[str, Any],
    dedupe_key: str,
) -> None:
    """发一条云端节点生命周期通知。延迟 import 避免任务模块与通知域的模块级环依赖。

    **best-effort（收在 savepoint 里）**：通知是「告知」，不是生命周期动作本身。主人身份缺失、
    卡片会话建不起来之类的通知侧故障，绝不能把已经发生的停机/销毁回滚掉——否则一个投递问题
    就能让节点永远停不下来、也永远销毁不掉。失败如实 `warn`，外层事务照常继续。
    """
    from backend.app.notification.service.notification_service import NotificationService

    try:
        async with db.begin_nested():
            await NotificationService.emit(
                db,
                recipient_id=recipient_id,
                source={'kind': 'system', 'id': 'hosting'},
                category='commerce',
                type=notice_type,
                title=title,
                body=body,
                payload={
                    'node_id': node_id,
                    'target': {'id': node_id},
                    'primary_action': {'uri': _BILLING_CENTER_URI},
                    **payload,
                },
                dedupe_key=dedupe_key,
            )
    except Exception as exc:
        log.warning(
            f'[hosting] 生命周期通知发送失败（不阻断本次动作）: '
            f'node_id={node_id}, type={notice_type}, owner={recipient_id}, err={exc}'
        )


async def _notify_retained(db: AsyncSession, row: HasnCloudNodes, retain_until: datetime) -> None:
    """动作 1 的通知：节点已停 + 数据保留到哪天 + 续订即可恢复。"""
    # 先归一到服务配置时区再取日期：直接对 UTC 值取 %Y-%m-%d 会把 +08 用户的截止日期少报一天
    deadline = timezone.from_datetime(retain_until).strftime('%Y-%m-%d')
    await _emit_hosting_notice(
        db,
        recipient_id=row.owner_hasn_id,
        node_id=row.node_id,
        notice_type='cloud_node_retained',
        title='订阅到期，云端节点已停止',
        body=(
            f'你的云端节点已停止运行，数据保留至 {deadline}。'
            f'在此之前续订即可恢复，逾期数据将被销毁且不可找回。'
        ),
        payload={'retain_until': retain_until.isoformat(), 'retention_days': RETENTION_DAYS},
        dedupe_key=f'cloud_node_retained:{row.node_id}',
    )


async def _notify_purging(db: AsyncSession, row: HasnCloudNodes) -> None:
    """动作 3 的通知：销毁前最后一次告知（发在销毁动作之前）。"""
    await _emit_hosting_notice(
        db,
        recipient_id=row.owner_hasn_id,
        node_id=row.node_id,
        notice_type='cloud_node_purged',
        title='云端节点数据保留期已到，正在销毁',
        body=(
            f'你的云端节点已停止超过 {RETENTION_DAYS} 天，节点与数据卷将被销毁，此操作不可恢复。'
            f'如需继续使用云端节点，请续订后重新创建。'
        ),
        payload={'retention_days': RETENTION_DAYS},
        dedupe_key=f'cloud_node_purged:{row.node_id}',
    )


async def _notify_expiring(db: AsyncSession, row: HasnCloudNodes, *, days_left: int, expires_at: datetime) -> None:
    """动作 4 的通知：到期前 7/3/1 天提醒，必须说清节点会停 + 数据保留 30 天。"""
    await _emit_hosting_notice(
        db,
        recipient_id=row.owner_hasn_id,
        node_id=row.node_id,
        notice_type='cloud_node_expiry',
        title=f'订阅将在 {days_left} 天后到期，云端节点会停止',
        body=(
            f'订阅到期后你的云端节点会停止运行，数据保留 {RETENTION_DAYS} 天；'
            f'保留期内续订即可恢复，逾期将被销毁且不可找回。'
        ),
        payload={'days_left': days_left, 'expires_at': expires_at.isoformat()},
        dedupe_key=f'cloud_node_expiry:{row.node_id}:{days_left}',
    )


# ─── 动作 1：订阅到期 → 进保留期 ───


async def _collect_unentitled_running_nodes() -> list[str]:
    """候选：状态不在 {stopped, deleting, deleted} 且主人已失去云端节点资格的节点。"""
    async with async_db_session() as db:
        rows = (
            await db.execute(
                select(HasnCloudNodes).where(
                    HasnCloudNodes.status.notin_([NODE_STATUS_STOPPED, NODE_STATUS_DELETING, NODE_STATUS_DELETED])
                )
            )
        ).scalars().all()
        candidates = []
        for row in rows:
            entitled = await cloud_node_service.is_entitled(
                db, user_id=row.user_id, owner_hasn_id=row.owner_hasn_id
            )
            if not entitled:
                candidates.append(row.node_id)
        return candidates


async def _stop_container_for_retention(node_id: str) -> tuple[bool, str | None, str | None]:
    """为进保留期而停容器。返回 `(是否可继续, 宿主备注, 失败原因码)`。

    - 停成功 → `(True, None, None)`
    - 容器已不存在（404）→ `(True, 'container_not_found', None)`：「已停止」本就是事实，可继续
    - 宿主不可达 → `(False, 'agent_unreachable', None)`：容器真实状态未知，本轮跳过
    - agent 明确拒绝 → `(False, 'agent_rejected', <failure_reason>)`：真失败，如实留痕
    """
    try:
        await hosting_agent_provider.stop_node(node_id)
    except HostingAgentError as exc:
        if exc.status_code == 404:
            log.warning(f'[hosting] 停容器时容器已不存在，按已停止继续进保留期: node_id={node_id}')
            return True, 'container_not_found', None
        if _is_agent_unreachable(exc):
            # 可重试、明日再来 → warn；绝不在此把库里改成 stopped（容器可能还在跑）
            log.warning(f'[hosting] 宿主不可达，本轮跳过进保留期: node_id={node_id}, err={exc}')
            return False, 'agent_unreachable', None
        log.error(f'[hosting] 停容器失败，无法进保留期: node_id={node_id}, err={exc}')
        return False, 'agent_rejected', exc.failure_reason or FAILURE_INTERNAL_ERROR
    return True, None, None


async def _retain_one(node_id: str, counters: dict[str, int]) -> None:
    """单节点独立事务：真停容器 → 落保留期 → 事件 → 通知。"""
    async with async_db_session.begin() as db:
        row = await _lock_node(db, node_id)
        if row is None or row.status in (NODE_STATUS_STOPPED, NODE_STATUS_DELETING, NODE_STATUS_DELETED):
            return  # 幂等：已被上一轮或并发副本处理
        if await cloud_node_service.is_entitled(db, user_id=row.user_id, owner_hasn_id=row.owner_hasn_id):
            return  # 读候选到加锁之间续订了 → 本轮不动它

        ok, note, failure_reason = await _stop_container_for_retention(node_id)
        if not ok:
            if note == 'agent_unreachable':
                counters['retention_skipped_agent_unreachable'] += 1
                return
            counters['retention_failed'] += 1
            row.failure_reason = failure_reason
            row.failure_detail = '订阅到期需停止节点，但托管宿主拒绝了停止动作'
            await cloud_node_service.record_event(
                db,
                cloud_node=row,
                event_type=EVENT_FAILED,
                detail={'stage': 'retention_stop', 'reason': 'subscription_expired'},
            )
            return

        retain_until = await cloud_node_service.mark_retention(db, node_id=node_id, days=RETENTION_DAYS)
        if retain_until is None:
            counters['retention_failed'] += 1
            log.error(f'[hosting] 落保留期时托管行消失: node_id={node_id}')
            return
        # 刻意**不**写 failure_reason：`stopped` 是正常状态，写失败原因会让 UI 误渲染成故障；
        # 「为什么停」的证据落在事件 detail 与通知里（`retain_until` 非空即「保留期中」）。
        await cloud_node_service.record_event(
            db,
            cloud_node=row,
            event_type=EVENT_STOPPED,
            detail={
                'reason': 'subscription_expired',
                'retention_days': RETENTION_DAYS,
                'retain_until': retain_until.isoformat(),
                'agent_note': note,
            },
        )
        await _notify_retained(db, row, retain_until)
        counters['retention_marked'] += 1
        log.info(f'[hosting] 订阅到期进保留期: node_id={node_id}, retain_until={retain_until.isoformat()}')


# ─── 动作 2：续订恢复 ───


async def _collect_retained_nodes() -> list[str]:
    """候选：处在保留期（`retain_until` 非空）且尚未销毁的节点。"""
    async with async_db_session() as db:
        return list(
            (
                await db.execute(
                    select(HasnCloudNodes.node_id).where(
                        HasnCloudNodes.retain_until.is_not(None),
                        HasnCloudNodes.status.notin_([NODE_STATUS_DELETING, NODE_STATUS_DELETED]),
                    )
                )
            ).scalars().all()
        )


async def _restore_one(node_id: str, counters: dict[str, int]) -> None:
    """单节点独立事务：资格已恢复 → 清保留期 → 起容器 → `started`；起不来就如实落 `failed`。"""
    async with async_db_session.begin() as db:
        row = await _lock_node(db, node_id)
        if row is None or row.retain_until is None or row.status in (NODE_STATUS_DELETING, NODE_STATUS_DELETED):
            return
        if not await cloud_node_service.is_entitled(db, user_id=row.user_id, owner_hasn_id=row.owner_hasn_id):
            return  # 仍未续订 → 留在保留期里等逾期销毁

        # 先清保留期：资格已恢复，这份数据就不该再挂着销毁倒计时（哪怕容器一时起不来）
        row.retain_until = None
        try:
            await hosting_agent_provider.start_node(node_id)
        except HostingAgentError as exc:
            # 起不来就是起不来——落 failed + 真实原因码，绝不假装恢复成功（零 fake）
            row.status = NODE_STATUS_FAILED
            row.failure_reason = exc.failure_reason or FAILURE_INTERNAL_ERROR
            row.failure_detail = str(exc)
            await cloud_node_service.record_event(
                db,
                cloud_node=row,
                event_type=EVENT_FAILED,
                detail={
                    'stage': 'retention_restore',
                    'error': exc.code,
                    'status_code': exc.status_code,
                    'message': str(exc),
                },
            )
            counters['restore_failed'] += 1
            log.error(f'[hosting] 续订后恢复启动失败: node_id={node_id}, err={exc}')
            return

        row.status = NODE_STATUS_STARTING
        row.failure_reason = None
        row.failure_detail = None
        await cloud_node_service.record_event(
            db, cloud_node=row, event_type=EVENT_STARTED, detail={'reason': 'subscription_renewed'}
        )
        counters['restored'] += 1
        log.info(f'[hosting] 续订恢复云端节点: node_id={node_id}')


# ─── 动作 3：逾期 → 销毁 ───


async def _collect_overdue_nodes(now: datetime) -> list[str]:
    """候选：保留期已过且仍停在 `stopped` 的节点。"""
    async with async_db_session() as db:
        return list(
            (
                await db.execute(
                    select(HasnCloudNodes.node_id).where(
                        HasnCloudNodes.retain_until.is_not(None),
                        HasnCloudNodes.retain_until < now,
                        HasnCloudNodes.status == NODE_STATUS_STOPPED,
                    )
                )
            ).scalars().all()
        )


async def _purge_one(node_id: str, now: datetime, counters: dict[str, int]) -> None:
    """单节点独立事务：销毁前通知 → 走 `purge_node` 的完整删除流程（凭据先吊销）。"""
    async with async_db_session.begin() as db:
        row = await _lock_node(db, node_id)
        if row is None or row.status != NODE_STATUS_STOPPED or row.retain_until is None or row.retain_until >= now:
            return  # 幂等：已销毁 / 已恢复 / 保留期被顺延
        if await cloud_node_service.is_entitled(db, user_id=row.user_id, owner_hasn_id=row.owner_hasn_id):
            # 兜底：读候选到加锁之间续订了。销毁不可逆，宁可放过一轮也不能删掉刚付费用户的卷。
            log.warning(f'[hosting] 逾期节点在销毁前检出订阅已恢复，跳过销毁: node_id={node_id}')
            return

        await _notify_purging(db, row)
        error = await cloud_node_service._purge_node_isolated(db, cloud_node=row, reason='retention_expired')
        if error is not None:
            counters['purge_failed'] += 1
            return
        counters['purged'] += 1
        log.info(f'[hosting] 保留期逾期销毁云端节点: node_id={node_id}')


async def _purge_overdue(now: datetime, counters: dict[str, int]) -> None:
    """逾期销毁阶段：先探活宿主，不通就整批跳过（销毁的第一步是不可逆的凭据吊销）。"""
    candidates = await _collect_overdue_nodes(now)
    if not candidates:
        return
    health = await hosting_agent_provider.health()
    if not health.get('ok'):
        # 探不通就一个都不动：先吊销凭据再销毁的顺序不可颠倒，宁可明天再来
        counters['purge_skipped_agent_unreachable'] += len(candidates)
        log.warning(f'[hosting] 宿主探活不通，本轮跳过 {len(candidates)} 个逾期节点的销毁: {health.get("error")}')
        return
    for node_id in candidates:
        await _purge_one(node_id, now, counters)
    # `purged` 中容器销毁调用失败、只完成退役的条数（残留待宿主巡检回收）
    counters['purge_agent_error'] = await _count_purge_agent_errors(candidates)


async def _count_purge_agent_errors(node_ids: list[str]) -> int:
    """统计本轮销毁里「已退役但容器/卷没删掉」的条数——残留必须可见，不许被 `purged` 掩盖。"""
    if not node_ids:
        return 0
    async with async_db_session() as db:
        rows = (
            await db.execute(
                select(HasnCloudNodes.failure_detail).where(
                    HasnCloudNodes.node_id.in_(node_ids),
                    HasnCloudNodes.status == NODE_STATUS_DELETED,
                    HasnCloudNodes.failure_detail.is_not(None),
                )
            )
        ).scalars().all()
        return len(rows)


# ─── 动作 4：到期前 7/3/1 天提醒 ───


async def _emit_due_reminders(now: datetime, counters: dict[str, int]) -> None:
    """给持有云端节点的主人发到期提醒：文案必须点明「节点会停 + 数据保留 30 天」。"""
    horizon = now + timedelta(days=max(EXPIRY_REMINDER_DAYS))
    async with async_db_session.begin() as db:
        rows = (
            await db.execute(
                select(HasnCloudNodes).where(
                    HasnCloudNodes.status.notin_([NODE_STATUS_DELETING, NODE_STATUS_DELETED]),
                    HasnCloudNodes.retain_until.is_(None),
                )
            )
        ).scalars().all()
        for row in rows:
            expires_at = await cloud_node_service.active_subscription_end(db, user_id=row.user_id)
            if expires_at is None or expires_at <= now or expires_at > horizon:
                continue
            days_left = _days_until(expires_at, now)
            if days_left in EXPIRY_REMINDER_DAYS:
                await _notify_expiring(db, row, days_left=days_left, expires_at=expires_at)
                counters['reminded'] += 1


# ─── 编排 ───


async def run_cloud_node_retention_sweep() -> dict[str, int]:
    """生命周期 sweep 核心（可直接被 pytest 调用，不经 celery）。

    动作顺序：进保留期 → 续订恢复 → 逾期销毁 → 到期提醒。恢复必须排在销毁之前，
    否则「最后一天续订」的主人会在同一轮里被删掉数据卷。
    """
    now = timezone.now()
    counters = _new_counters()

    for node_id in await _collect_unentitled_running_nodes():
        await _retain_one(node_id, counters)

    for node_id in await _collect_retained_nodes():
        await _restore_one(node_id, counters)

    await _purge_overdue(now, counters)
    await _emit_due_reminders(now, counters)

    return _roll_up(counters)


@shared_task(name='cloud_node_retention_sweep')
async def cloud_node_retention_sweep() -> str:
    """celery 任务包装（beat 每日 04:20 一轮）：跑核心 sweep + 落日志。"""
    r = await run_cloud_node_retention_sweep()
    result_msg = (
        f'云端节点生命周期 sweep 完成: 进保留期 {r["retention_marked"]} 个, '
        f'续订恢复 {r["restored"]} 个, 逾期销毁 {r["purged"]} 个'
        f'（其中容器残留 {r["purge_agent_error"]} 个）, 到期提醒 {r["reminded"]} 条; '
        f'宿主不可达跳过 {r["skipped_agent_unreachable"]} 个, 失败 {r["failed"]} 个'
    )
    if r['failed']:
        log.error(f'[HostingRetentionSweep] {result_msg}')
    elif r['skipped_agent_unreachable']:
        log.warning(f'[HostingRetentionSweep] {result_msg}')
    else:
        log.info(f'[HostingRetentionSweep] {result_msg}')
    return result_msg
