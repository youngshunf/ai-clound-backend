"""hasn_im.consumers.base · 消费者契约与 DTO（durable/best-effort 两类·doc16 §7.2）

消费者分两类，语义不同，**禁止混用**：

| 类别 | 处理成功定义 | 失败处理 | retention 低水位 |
|---|---|---|---|
| durable | 副作用已持久提交（与 cursor 推进同事务） | 退避重试 → dead letter，人工处置 | 参与 |
| best-effort | **已尝试投递**（成败均推进 cursor） | 不重试、不进 DLQ，记 metric | 不参与 |

best-effort 的丢失由 daemon 常驻 sync pull（§8.2）兜底，**严禁**给它套 durable 语义——否则
Redis/推送通道故障期间积压会阻塞 retention，恢复后重放造成推送风暴。
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from enum import Enum
from typing import Any, Protocol, runtime_checkable

from sqlalchemy.ext.asyncio import AsyncSession


class ConsumerClass(str, Enum):
    """消费者语义类别（§7.2）。"""

    DURABLE = 'durable'
    BEST_EFFORT = 'best_effort'


@dataclass(frozen=True)
class IntegrationEvent:
    """从 integration_events 取出的一条待消费事件（值对象·不暴露 ORM）。"""

    event_seq: int
    event_id: str
    event_type: str
    aggregate_type: str
    aggregate_id: str
    payload: dict[str, Any]
    aggregate_seq: int | None = None
    trace_id: str | None = None
    causation_id: str | None = None
    occurred_at: datetime | None = None
    shard_key: int = 0


@runtime_checkable
class EventConsumer(Protocol):
    """单个消费者契约。框架按 event_seq 顺序把事件交给 ``handle``。

    - ``name``：稳定消费者名（= offsets/failures 主键、lease 键），全局唯一；
    - ``consumer_class``：durable / best-effort（决定失败与 cursor 语义）；
    - ``handle``：处理一条事件。**durable** 的副作用写必须用传入的 ``db``（与 cursor 推进
      同事务由框架提交）；失败即 ``raise``（框架据此退避/dead letter）。**best-effort** 同样
      收 ``db`` 但其成功与否都不阻塞 cursor；投递失败 ``raise``（框架记 metric 后仍推进）。
    """

    @property
    def name(self) -> str: ...

    @property
    def consumer_class(self) -> ConsumerClass: ...

    async def handle(self, event: IntegrationEvent, db: AsyncSession) -> None: ...
