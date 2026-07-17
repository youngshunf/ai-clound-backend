"""hasn_im.consumers.framework · 消费者运行框架（lease + 分道语义·§7.2）

一份运行器驱动任意 ``EventConsumer``：领租约 → 从自身 cursor 顺序取事件 → 逐条按类别处理。
消费者只实现 ``handle``（业务副作用），失败/退避/dead letter/cursor 语义全由本框架统一。

**durable**（``sync_projector`` / ``audit_projector``）：
- 处理成功与 cursor 推进**同事务**提交；严格按 event_seq 顺序，**遇失败即 park**（不越过）；
- 失败退避重试（``record_retry``），达 ``max_attempts`` 进 dead letter（``record_dead_letter``），
  cursor 停在失败事件前，须显式 ``resolve_dead_letter`` 才能继续（§7.2「授权修复重放/确认跳过」）；
- 参与 retention 低水位。

**best-effort**（``realtime_notifier`` / ``push_notifier``）：
- **已尝试投递**即算处理（成败均推进 cursor），**不重试、不进 DLQ**，失败记 metric（warn）；
- 不参与 retention；丢失由 daemon 常驻 sync pull 兜底（§8.2）。严禁给它套 durable 语义。

唤醒：send 事务 commit 后 post-commit best-effort 发一次唤醒（复用 Redis wake）+ ≤1s 短轮询
兜底由外层 worker 调度；本框架只提供纯 ``tick``，便于故障注入测试脱离调度器直接驱动。
"""

from __future__ import annotations

import logging

from dataclasses import dataclass

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from backend.app.hasn_im.consumers import store
from backend.app.hasn_im.consumers.base import ConsumerClass, EventConsumer

log = logging.getLogger(__name__)

# §14-3 初值：durable 重试 5 次，指数退避 1s/5s/30s/2m/10m 封顶（对齐 outbox_relay）。
_DEFAULT_BACKOFF_SCHEDULE_S: tuple[int, ...] = (1, 5, 30, 120, 600)
_DEFAULT_MAX_ATTEMPTS = 5
_DEFAULT_LEASE_TTL_S = 30


def _backoff_seconds(attempts: int, schedule: tuple[int, ...]) -> int:
    """第 ``attempts`` 次失败后的退避秒数（``attempts`` 从 1 起；超出 schedule 取末位封顶）。"""
    if attempts <= 0:
        return schedule[0]
    return schedule[min(attempts, len(schedule)) - 1]


@dataclass
class ConsumerTickStats:
    """单轮 ``tick`` 统计（指标源·高基数 ID 不进 label）。"""

    lease_held: bool = False
    fetched: int = 0
    processed: int = 0
    retried: int = 0
    dead_lettered: int = 0
    skipped: int = 0
    best_effort_failed: int = 0
    parked: bool = False


class ConsumerRunner:
    """单消费者运行框架（一个 consumer_name 一个 runner；由 worker 周期调 ``tick``）。"""

    def __init__(
        self,
        *,
        consumer: EventConsumer,
        sessionmaker: async_sessionmaker[AsyncSession],
        instance_id: str,
        shard_key: int = 0,
        max_attempts: int | None = None,
        backoff_schedule: tuple[int, ...] | None = None,
        lease_ttl_seconds: int | None = None,
    ) -> None:
        self._consumer = consumer
        self._sm = sessionmaker
        self._instance_id = instance_id
        self._shard_key = shard_key
        self._max_attempts = max_attempts or _DEFAULT_MAX_ATTEMPTS
        self._backoff = backoff_schedule or _DEFAULT_BACKOFF_SCHEDULE_S
        self._lease_ttl = lease_ttl_seconds or _DEFAULT_LEASE_TTL_S

    @property
    def name(self) -> str:
        return self._consumer.name

    async def tick(self, *, batch_limit: int = 50) -> ConsumerTickStats:
        """领租约 → 取一批事件 → 逐条按类别处理，返回本轮统计。"""
        stats = ConsumerTickStats()

        # 1) 领/续租约（独立事务）。抢不到 = 另一实例活跃 → 本 tick 让路。
        async with self._sm() as db:
            held = await store.try_acquire_lease(
                db, consumer_name=self.name, owner=self._instance_id, ttl_seconds=self._lease_ttl
            )
            await db.commit()
        if not held:
            return stats
        stats.lease_held = True

        # 2) 读 cursor + 取一批（独立事务读）。
        async with self._sm() as db:
            cursor = await store.get_cursor(db, self.name)
            events = await store.fetch_after(
                db, after_seq=cursor, shard_key=self._shard_key, limit=batch_limit
            )
        stats.fetched = len(events)
        if not events:
            return stats

        # 3) 逐条按类别处理。
        if self._consumer.consumer_class is ConsumerClass.DURABLE:
            await self._run_durable(events, stats)
        else:
            await self._run_best_effort(events, stats)
        return stats

    # ---------- durable：同事务 + 顺序 park ----------

    async def _run_durable(self, events, stats: ConsumerTickStats) -> None:
        for event in events:
            failure = None
            async with self._sm() as db:
                failure = await store.get_failure(
                    db, consumer_name=self.name, event_seq=event.event_seq
                )
            if failure and failure.dead_lettered:
                # 未决 dead letter → park（须显式 resolve 才能推进·§7.2）
                if failure.resolution is None:
                    stats.parked = True
                    return
                # 确认跳过：直接推进 cursor 越过本事件、不 handle（§7.2 授权跳过）
                if failure.resolution == 'skipped':
                    async with self._sm() as db:
                        await store.advance_cursor(
                            db, consumer_name=self.name, to_seq=event.event_seq
                        )
                        await db.commit()
                    stats.skipped += 1
                    continue
                # resolution == 'replayed'：修复后重放——当作一次全新重试落到下面 handle 分支
            # 退避未到 → 本 tick 让路（顺序消费，后续事件不越过）
            elif failure and not failure.retry_due:
                stats.parked = True
                return

            prev_attempts = failure.attempts if failure else 0
            try:
                # 处理 + cursor 推进同事务提交（§7.2）
                async with self._sm() as db:
                    await self._consumer.handle(event, db)
                    await store.advance_cursor(db, consumer_name=self.name, to_seq=event.event_seq)
                    await db.commit()
                stats.processed += 1
            except Exception as exc:  # noqa: BLE001 消费者任意异常都进退避/dead letter，绝不静默跳过
                parked = await self._record_durable_failure(event.event_seq, prev_attempts, exc, stats)
                # durable 顺序消费：本事件未成功、cursor 未推进 → 停止本 tick（park）
                stats.parked = True
                _ = parked
                return

    async def _record_durable_failure(
        self, event_seq: int, prev_attempts: int, exc: Exception, stats: ConsumerTickStats
    ) -> bool:
        """记 durable 失败（退避 or dead letter），返回是否进 dead letter。独立事务落库。"""
        attempts = prev_attempts + 1
        async with self._sm() as db:
            if attempts >= self._max_attempts:
                await store.record_dead_letter(
                    db, consumer_name=self.name, event_seq=event_seq, attempts=attempts, error=repr(exc)
                )
                await db.commit()
                stats.dead_lettered += 1
                # dead letter = 永久卡住须人介入 → error（warn/error 铁律：终局不可恢复）
                log.error(
                    'IM 消费者事件进 dead letter（consumer=%s event_seq=%d attempts=%d）：%r',
                    self.name, event_seq, attempts, exc,
                )
                return True
            backoff = _backoff_seconds(attempts, self._backoff)
            await store.record_retry(
                db, consumer_name=self.name, event_seq=event_seq,
                attempts=attempts, backoff_seconds=backoff, error=repr(exc),
            )
            await db.commit()
            stats.retried += 1
            # 会退避重试 / 自愈 → warn（warn/error 铁律：可恢复不提级）
            log.warning(
                'IM 消费者事件处理失败将退避重试（consumer=%s event_seq=%d attempts=%d next=+%ds）：%r',
                self.name, event_seq, attempts, backoff, exc,
            )
            return False

    # ---------- best-effort：已尝试即推进，不重试不 DLQ ----------

    async def _run_best_effort(self, events, stats: ConsumerTickStats) -> None:
        for event in events:
            try:
                async with self._sm() as db:
                    await self._consumer.handle(event, db)
                    await db.commit()
            except Exception as exc:  # noqa: BLE001 best-effort：已尝试即算，记 metric 后仍推进
                stats.best_effort_failed += 1
                # 不重试、不进 DLQ、不阻塞 retention（§7.2）→ warn（预期可丢失、sync pull 兜底）
                log.warning(
                    'IM best-effort 消费者投递失败（consumer=%s event_seq=%d，不重试·sync pull 兜底）：%r',
                    self.name, event.event_seq, exc,
                )
            # 成败均推进 cursor（独立事务，与 handle 解耦）
            async with self._sm() as db:
                await store.advance_cursor(db, consumer_name=self.name, to_seq=event.event_seq)
                await db.commit()
            stats.processed += 1
