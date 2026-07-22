"""hasn_im.application.outbox_relay · 生产方 outbox relay 公共框架（R1-07·§6.1/§6.2）

一份框架供所有生产方（notification / 社区卡 / 任务 / 完成卡）复用：领取命令 → 以**稳定
Idempotency-Key** 调 ``ImGateway.send_message`` → 成功确认 / 失败退避重试 / 达上限 dead
letter。生产方只提供 ``OutboxStore``（表操作）+ ``build_command`` 回调（还原发送命令），
**零 relay 逻辑**（§6.1 禁止各业务各抄一套）。

§6.2 故障语义由本框架 + ``OutboxStore`` 事务性共同保证：

| 故障点 | 结果 |
|---|---|
| 业务事务提交前失败 | 业务与命令都不存在（生产方本地事务原子性，本框架不涉及） |
| 业务提交后 relay 未执行即宕机 | outbox 仍 pending，下轮 ``drain_once`` 领取继续 |
| IM 已提交但 relay 未收响应 | 同 ``idempotency_key`` 重试命中原消息（去重返回，不重复投递） |
| 达最大重试 | ``mark_dead_letter`` + ``on_dead_letter`` 告警钩子，提供人工重放 |

依赖方向（§0.1）：本模块属 ``hasn_im.application``，依赖 ``ports``（``ImGateway`` /
``outbox`` 契约），不依赖任何生产方业务模块——生产方反向注入 store + 回调。
"""

from __future__ import annotations

import inspect
import logging
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, replace

from backend.app.hasn_im.ports.dto import SendMessageCommand, ServicePrincipal
from backend.app.hasn_im.ports.im_gateway import ImGateway
from backend.app.hasn_im.ports.outbox import OutboxRecord, OutboxStore

log = logging.getLogger(__name__)

# §14-3 初值：durable 重试 5 次，指数退避 1s/5s/30s/2m/10m 封顶（超出 schedule 取末位）。
_DEFAULT_BACKOFF_SCHEDULE_S: tuple[int, ...] = (1, 5, 30, 120, 600)
_DEFAULT_MAX_ATTEMPTS = 5

# build_command 回调：把一条 outbox 记录还原为发送命令 + 服务主体（生产方私有 payload 结构在此解读）。
BuildCommand = Callable[[OutboxRecord], tuple[SendMessageCommand, ServicePrincipal]]
# dead letter 告警钩子（Runbook 集成点）：命令达最大重试进 DLQ 时调用，best-effort。
DeadLetterHook = Callable[[OutboxRecord, str], Awaitable[None] | None]


@dataclass
class RelayStats:
    """单轮 ``drain_once`` 统计（指标源，§12.2 高基数 ID 不进 label）。"""

    claimed: int = 0
    completed: int = 0
    retried: int = 0
    dead_lettered: int = 0
    deduped: int = 0


def _backoff_seconds(attempts: int, schedule: tuple[int, ...]) -> int:
    """第 ``attempts`` 次失败后的退避秒数（``attempts`` 从 1 起；超出 schedule 取末位封顶）。"""
    if attempts <= 0:
        return schedule[0]
    idx = min(attempts, len(schedule)) - 1
    return schedule[idx]


class OutboxRelay:
    """生产方 outbox relay 框架（一份实现，生产方只注入 ``store`` + ``build_command``）。

    无状态、可复用：一个生产方一个 ``OutboxRelay`` 实例；由后台 worker 周期调 ``drain_once``
    （post-commit best-effort 唤醒 + 短轮询兜底在 R2-05 消费者框架统一，本卡只提供纯 relay
    循环，便于故障注入测试脱离调度器直接驱动）。
    """

    def __init__(
        self,
        *,
        store: OutboxStore,
        gateway: ImGateway,
        build_command: BuildCommand,
        producer: str,
        max_attempts: int | None = None,
        backoff_schedule: tuple[int, ...] | None = None,
        on_dead_letter: DeadLetterHook | None = None,
    ) -> None:
        self._store = store
        self._gateway = gateway
        self._build_command = build_command
        self._producer = producer
        self._max_attempts = max_attempts or _DEFAULT_MAX_ATTEMPTS
        self._backoff_schedule = backoff_schedule or _DEFAULT_BACKOFF_SCHEDULE_S
        self._on_dead_letter = on_dead_letter

    async def drain_once(self, *, now: int, batch_limit: int = 50) -> RelayStats:
        """领取一批到期命令并逐条投递，返回本轮统计。

        每条命令独立成败：一条失败进退避 / dead letter，不阻塞同批其余命令。
        """
        stats = RelayStats()
        records = await self._store.claim_batch(limit=batch_limit, now=now)
        stats.claimed = len(records)
        for rec in records:
            await self._deliver_one(rec, now=now, stats=stats)
        return stats

    async def _deliver_one(self, rec: OutboxRecord, *, now: int, stats: RelayStats) -> None:
        """投递单条命令：还原 → 稳定幂等键 → send → 成功确认 / 失败退避。"""
        try:
            command, principal = self._build_command(rec)
        except Exception as exc:  # payload 损坏等——同样走退避 / dead letter，绝不静默丢弃命令
            await self._handle_failure(rec, f'build_command 失败：{exc!r}', now=now, stats=stats)
            return

        # §6.1 强约束：outbox 命令必须带稳定幂等键，否则同键重试无法命中原消息（会重复投递）。
        idem = command.idempotency_key or rec.idempotency_key
        if not idem:
            await self._handle_failure(
                rec, 'outbox 命令缺 idempotency_key（§6.1 违约）', now=now, stats=stats
            )
            return
        if command.idempotency_key != idem:
            command = replace(command, idempotency_key=idem)

        try:
            result = await self._gateway.send_message(command, principal)
        except Exception as exc:  # 含「IM 已提交但响应丢失」——下轮同键重试幂等命中原消息
            await self._handle_failure(rec, f'send_message 失败：{exc!r}', now=now, stats=stats)
            return

        await self._store.mark_completed(rec.command_id, message_id=result.message_id)
        stats.completed += 1
        if result.deduped:
            stats.deduped += 1

    async def _handle_failure(
        self, rec: OutboxRecord, error: str, *, now: int, stats: RelayStats
    ) -> None:
        """一次失败：未达上限退避重试（warn），达上限进 dead letter（error·终局须人介入）。"""
        attempts = rec.attempts + 1
        if attempts >= self._max_attempts:
            await self._store.mark_dead_letter(rec.command_id, error=error, attempts=attempts)
            stats.dead_lettered += 1
            # dead letter = 命令永久卡住、须人工重放 → error（warn/error 铁律：终局不可恢复）
            log.error(
                'outbox 命令进 dead letter（producer=%s command=%s attempts=%d）：%s',
                self._producer,
                rec.command_id,
                attempts,
                error,
            )
            await self._fire_dead_letter(rec, error)
        else:
            next_at = now + _backoff_seconds(attempts, self._backoff_schedule)
            await self._store.mark_retry(
                rec.command_id, error=error, attempts=attempts, next_attempt_at=next_at
            )
            stats.retried += 1
            # 会退避重试 / 自愈 → warn（warn/error 铁律：可恢复不提级，避免 error 洪水）
            log.warning(
                'outbox 命令投递失败将退避重试（producer=%s command=%s attempts=%d next=+%ds）：%s',
                self._producer,
                rec.command_id,
                attempts,
                next_at - now,
                error,
            )

    async def _fire_dead_letter(self, rec: OutboxRecord, error: str) -> None:
        """触发 dead letter 告警钩子（best-effort：钩子失败只 warn，不影响已落库的 DLQ 标记）。"""
        if self._on_dead_letter is None:
            return
        try:
            res = self._on_dead_letter(rec, error)
            if inspect.isawaitable(res):
                await res
        except Exception as exc:
            log.warning('dead letter 告警钩子失败（command=%s）：%r', rec.command_id, exc)
