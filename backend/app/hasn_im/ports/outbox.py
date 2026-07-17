"""hasn_im.ports.outbox · 生产方 transactional outbox relay 契约（§6.1/§6.2）

R1-07：跨业务「状态变化必产消息」经生产方**自有** outbox（各生产方拥有自己 schema 的
``im_command_outbox`` 表，IM 不提供供所有业务直写的共享命令表）。但 **relay 代码是一份
公共框架**（``application/outbox_relay.py``）——领取、退避、重试、dead letter、指标、
Runbook 钩子统一实现；生产方**零 relay 逻辑**，只实现本模块两个契约：

- ``OutboxStore``：对自 schema ``im_command_outbox`` 表的领取/确认/退避/dead letter；
- ``build_command`` 回调（见 ``outbox_relay.BuildCommand``）：把一条 ``OutboxRecord``
  还原为 ``(SendMessageCommand, ServicePrincipal)``——生产方私有 payload 结构在此解读。

禁止 notification/社区/任务/完成卡各抄一套 relay（§6.1）——那会让铺开成本失控并制造
N 种故障语义。字段按 §6.1 最低集。
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Protocol, runtime_checkable


@dataclass(frozen=True)
class OutboxRecord:
    """领取出的一条待投递命令（§6.1 最低字段）。

    ``payload`` 是生产方私有结构，框架**不解读**——只透传给 ``build_command`` 回调还原成
    发送命令。``attempts`` 是**本次领取前已发生**的失败次数；框架据此算下次退避 / 是否
    达上限进 dead letter。``idempotency_key`` 必须稳定（同一业务命令跨重试恒定），否则
    §6.2「IM 已提交但 relay 未收响应 → 同键重试命中原消息」不成立。
    """

    command_id: str
    producer: str
    conversation_id: str
    command_type: str
    payload: dict[str, Any]
    idempotency_key: str
    attempts: int = 0
    trace_id: str | None = None
    causation_id: str | None = None


@runtime_checkable
class OutboxStore(Protocol):
    """生产方对自 schema ``im_command_outbox`` 表的操作（框架经此驱动 relay）。

    实现须保证：
    - ``claim_batch`` 领取时对行加锁（``FOR UPDATE SKIP LOCKED``）防多 relay 实例争抢同一命令；
    - ``mark_*`` 各自幂等（同 ``command_id`` 重复标记不报错）；
    - 领取与后续 ``mark_*`` 在 relay 自己的事务里，与生产方业务事务隔离（生产方业务写 +
      写 outbox 是**一个**事务，relay 领取是**另一个**事务——这是 §6.2 故障语义的基础）。
    """

    async def claim_batch(self, *, limit: int, now: int) -> list[OutboxRecord]:
        """领取 ``status=pending`` 且 ``next_attempt_at<=now`` 的命令（至多 ``limit`` 条）。"""
        ...

    async def mark_completed(self, command_id: str, *, message_id: int | None) -> None:
        """标记投递成功（``status=completed``，``completed_at=now``）。"""
        ...

    async def mark_retry(
        self,
        command_id: str,
        *,
        error: str,
        attempts: int,
        next_attempt_at: int,
    ) -> None:
        """标记本次失败、待退避重试（``status=pending``、``attempts=attempts``、
        ``last_error=error``、``next_attempt_at=退避后时刻``）。"""
        ...

    async def mark_dead_letter(self, command_id: str, *, error: str, attempts: int) -> None:
        """标记进入 dead letter（``status=dead_letter``，达最大重试，须告警 + 人工重放）。"""
        ...
