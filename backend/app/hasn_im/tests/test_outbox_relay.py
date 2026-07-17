"""hasn_im.tests.test_outbox_relay · R1-07 relay 框架 §6.2 故障语义守卫

用内存 stub（``_InMemoryOutboxStore`` + ``_FakeGateway``）脱离 DB / 调度器直接驱动
``OutboxRelay.drain_once``，逐条钉死 16 号 §6.2 四种故障窗口：

1. 业务提交前失败 → 命令根本不存在（outbox 空）→ drain 无操作；
2. 业务提交后 relay 宕机 → outbox 仍 pending → 恢复后 drain 领取并投递成功；
3. IM 已提交但 relay 未收响应 → 同 idempotency_key 重试命中原消息（去重，不重复投递）；
4. 达最大重试 → 进 dead letter + on_dead_letter 告警钩子触发。

纯逻辑、零 DB / 零 mock 框架——store/gateway 是真实内存实现，行为可断言。
"""

from __future__ import annotations

from dataclasses import dataclass

import pytest

from backend.app.hasn_im.application.outbox_relay import OutboxRelay
from backend.app.hasn_im.ports.dto import (
    ActorKind,
    DeliveryState,
    SendMessageCommand,
    SendMessageResult,
    ServicePrincipal,
)
from backend.app.hasn_im.ports.outbox import OutboxRecord


# ---- 内存 stub：真实行为，非 mock ----------------------------------------------------


@dataclass
class _Row:
    """内存 outbox 行（模拟生产方 im_command_outbox 一行）。"""

    rec: OutboxRecord
    status: str = 'pending'
    next_attempt_at: int = 0
    last_error: str | None = None
    message_id: int | None = None


class _InMemoryOutboxStore:
    """内存 OutboxStore：claim 领取到期 pending 行，mark_* 就地改状态（幂等）。"""

    def __init__(self) -> None:
        self.rows: dict[str, _Row] = {}

    def enqueue(self, rec: OutboxRecord, *, next_attempt_at: int = 0) -> None:
        self.rows[rec.command_id] = _Row(rec=rec, next_attempt_at=next_attempt_at)

    async def claim_batch(self, *, limit: int, now: int) -> list[OutboxRecord]:
        picked: list[OutboxRecord] = []
        for row in self.rows.values():
            if row.status == 'pending' and row.next_attempt_at <= now:
                # claim 出的行按当前已发生失败次数回填 attempts（模拟 DB 列）
                picked.append(row.rec)
                if len(picked) >= limit:
                    break
        return picked

    async def mark_completed(self, command_id: str, *, message_id: int | None) -> None:
        row = self.rows[command_id]
        row.status = 'completed'
        row.message_id = message_id

    async def mark_retry(
        self, command_id: str, *, error: str, attempts: int, next_attempt_at: int
    ) -> None:
        row = self.rows[command_id]
        row.status = 'pending'
        row.last_error = error
        row.next_attempt_at = next_attempt_at
        # attempts 回写到 rec（下轮 claim 领出时框架据此续算退避 / dead letter）
        row.rec = _replace_attempts(row.rec, attempts)

    async def mark_dead_letter(self, command_id: str, *, error: str, attempts: int) -> None:
        row = self.rows[command_id]
        row.status = 'dead_letter'
        row.last_error = error
        row.rec = _replace_attempts(row.rec, attempts)


def _replace_attempts(rec: OutboxRecord, attempts: int) -> OutboxRecord:
    from dataclasses import replace

    return replace(rec, attempts=attempts)


class _FakeGateway:
    """内存 ImGateway：可配置前 N 次 send 抛异常；已提交的 idempotency_key 记账，重试同键去重返回。

    ``fail_first`` 模拟「响应丢失但 IM 内部已提交」——抛异常前先记账，下轮同键返回 deduped
    原消息；``fail_all`` 模拟永久失败（连响应都没提交）走 dead letter。
    """

    def __init__(self, *, fail_first: int = 0, fail_all: bool = False, commit_before_fail: bool = False) -> None:
        self._fail_first = fail_first
        self._fail_all = fail_all
        self._commit_before_fail = commit_before_fail
        self._calls = 0
        self._next_message_id = 1000
        self.committed: dict[str, int] = {}  # idempotency_key -> message_id（模拟已提交去重存储）
        self.send_history: list[tuple[str, bool]] = []  # (idempotency_key, deduped)

    async def send_message(
        self, command: SendMessageCommand, principal: ServicePrincipal
    ) -> SendMessageResult:
        self._calls += 1
        idem = command.idempotency_key
        assert idem, 'relay 必须传稳定 idempotency_key'
        # 同键已提交 → 去重返回原消息（幂等，§6.2 第三条的落点）
        if idem in self.committed:
            self.send_history.append((idem, True))
            return SendMessageResult(
                delivery_state=DeliveryState.ACCEPTED,
                conversation_id=command.conversation_id,
                message_id=self.committed[idem],
                deduped=True,
            )
        if self._fail_all:
            raise RuntimeError('IM 永久不可达')
        if self._calls <= self._fail_first:
            if self._commit_before_fail:
                # 模拟：IM 已提交入库，但响应在网络上丢失 → relay 收到异常
                self.committed[idem] = self._next_message_id
                self._next_message_id += 1
            raise RuntimeError('IM 响应丢失')
        # 正常提交
        mid = self._next_message_id
        self._next_message_id += 1
        self.committed[idem] = mid
        self.send_history.append((idem, False))
        return SendMessageResult(
            delivery_state=DeliveryState.ACCEPTED,
            conversation_id=command.conversation_id,
            message_id=mid,
            deduped=False,
        )


def _principal() -> ServicePrincipal:
    # producer relay：系统主体、无节点上下文（origin_node_id=None 哨兵，§5.1）
    return ServicePrincipal(canonical_sender='svc:notification', actor_kind=ActorKind.SYSTEM_SERVICE)


def _build_command(rec: OutboxRecord) -> tuple[SendMessageCommand, ServicePrincipal]:
    return (
        SendMessageCommand(
            conversation_id=rec.conversation_id,
            content=rec.payload,
            idempotency_key=rec.idempotency_key,
            msg_type='notification',
        ),
        _principal(),
    )


def _rec(command_id: str = 'cmd-1') -> OutboxRecord:
    return OutboxRecord(
        command_id=command_id,
        producer='notification',
        conversation_id='conv-1',
        command_type='deliver_card',
        payload={'text': 'hi'},
        idempotency_key=f'idem-{command_id}',
    )


# ---- §6.2 四种故障窗口 ---------------------------------------------------------------


@pytest.mark.asyncio
async def test_business_commit_before_failure_leaves_nothing() -> None:
    """§6.2①：业务提交前失败 → 命令不存在 → drain claimed=0，gateway 零调用。"""
    store = _InMemoryOutboxStore()  # 空——业务事务回滚，命令从未落库
    gw = _FakeGateway()
    relay = OutboxRelay(store=store, gateway=gw, build_command=_build_command, producer='notification')

    stats = await relay.drain_once(now=0)

    assert stats.claimed == 0
    assert stats.completed == 0
    assert gw.send_history == []


@pytest.mark.asyncio
async def test_relay_crash_after_business_commit_resumes() -> None:
    """§6.2②：业务提交后 relay 宕机 → outbox 仍 pending → 恢复后 drain 领取并投递成功。"""
    store = _InMemoryOutboxStore()
    store.enqueue(_rec())  # 业务事务已提交、留下 pending 命令；relay 此前未运行（模拟宕机）
    gw = _FakeGateway()
    relay = OutboxRelay(store=store, gateway=gw, build_command=_build_command, producer='notification')

    stats = await relay.drain_once(now=0)

    assert stats.claimed == 1
    assert stats.completed == 1
    assert store.rows['cmd-1'].status == 'completed'
    assert store.rows['cmd-1'].message_id is not None


@pytest.mark.asyncio
async def test_im_committed_but_response_lost_dedupes_on_retry() -> None:
    """§6.2③：IM 已提交但响应丢失 → 同 idempotency_key 重试命中原消息（去重，不重复投递）。"""
    store = _InMemoryOutboxStore()
    store.enqueue(_rec())
    # 第一次 send：IM 内部已提交、但响应丢失 → relay 收到异常
    gw = _FakeGateway(fail_first=1, commit_before_fail=True)
    relay = OutboxRelay(store=store, gateway=gw, build_command=_build_command, producer='notification')

    # 第一轮：失败 → 退避重试（不进 dead letter）
    stats1 = await relay.drain_once(now=0)
    assert stats1.retried == 1
    assert stats1.completed == 0
    row = store.rows['cmd-1']
    assert row.status == 'pending'
    assert row.next_attempt_at > 0  # 退避到未来

    # 第二轮（快进过退避）：同键命中已提交原消息 → deduped 完成，绝不重复投递
    stats2 = await relay.drain_once(now=row.next_attempt_at)
    assert stats2.completed == 1
    assert stats2.deduped == 1
    assert store.rows['cmd-1'].status == 'completed'
    # 只提交了一条消息（去重存储里只有一个 message_id）
    assert len(gw.committed) == 1


@pytest.mark.asyncio
async def test_dead_letter_after_max_attempts() -> None:
    """§6.2④：达最大重试 → 进 dead letter + on_dead_letter 告警钩子触发。"""
    store = _InMemoryOutboxStore()
    store.enqueue(_rec())
    gw = _FakeGateway(fail_all=True)  # 永久不可达
    alerted: list[tuple[str, str]] = []

    async def _on_dead_letter(rec: OutboxRecord, error: str) -> None:
        alerted.append((rec.command_id, error))

    relay = OutboxRelay(
        store=store,
        gateway=gw,
        build_command=_build_command,
        producer='notification',
        max_attempts=5,
        on_dead_letter=_on_dead_letter,
    )

    # 前 4 轮退避重试，第 5 轮达上限进 dead letter
    now = 0
    for expected_round in range(1, 6):
        stats = await relay.drain_once(now=now)
        row = store.rows['cmd-1']
        if expected_round < 5:
            assert stats.retried == 1, f'第 {expected_round} 轮应退避重试'
            assert row.status == 'pending'
            now = row.next_attempt_at
        else:
            assert stats.dead_lettered == 1, '第 5 轮应进 dead letter'
            assert row.status == 'dead_letter'

    assert len(alerted) == 1  # 告警钩子恰触发一次
    assert alerted[0][0] == 'cmd-1'


@pytest.mark.asyncio
async def test_missing_idempotency_key_does_not_deliver() -> None:
    """§6.1 守卫：outbox 命令缺稳定幂等键（回调与记录均无）→ 不投递，走退避（防重复投递根因）。"""
    store = _InMemoryOutboxStore()
    from dataclasses import replace

    bad = replace(_rec(), idempotency_key='')
    store.rows['cmd-1'] = _Row(rec=bad)
    gw = _FakeGateway()

    def _build_no_idem(rec: OutboxRecord) -> tuple[SendMessageCommand, ServicePrincipal]:
        return (
            SendMessageCommand(conversation_id=rec.conversation_id, content=rec.payload, idempotency_key=None),
            _principal(),
        )

    relay = OutboxRelay(store=store, gateway=gw, build_command=_build_no_idem, producer='notification')
    stats = await relay.drain_once(now=0)

    assert stats.completed == 0
    assert stats.retried == 1  # 缺幂等键 → 当作失败退避，绝不裸投递
    assert gw.send_history == []
