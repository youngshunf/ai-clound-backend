"""hasn_sync.ports.dto · 同步内核对外 DTO（frozen 值对象）

**边界铁律（§0.1/D2）**：envelope 的 payload 由业务方构造为**完整载荷**，sync 不反查
业务表补齐。这里只声明「事件信封」与「已落事件引用」两个值对象，不携带任何业务 schema 类型。

**契约演进（R1→R2-07·已落地）**：R1 版 envelope 对齐现网 `_append_sync_event_with_id` 的真实
字段（`hasn_id/aggregate_type/aggregate_id`）。R2-07 建成 `hasn_sync.append_event(...)` PG 函数后，
去重键 `(producer, source_event_id)` 真正落地：envelope 追加 `producer`/`source_event_id`
（同在同缺——两者共同构成幂等键），ref 追加 `deduped`（命中已落行时为 True，未新增行）。
封装目标从旧 chokepoint 原地换成 PG 函数，port 形状稳定，仅实现替换。
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Any, Literal


@dataclass(frozen=True)
class SyncEnvelope:
    """待写入的同步事件信封（业务方构造完整载荷后交给 append）。

    字段与现网 `hasn_sync_events` 表一一对应；`payload` 必须是可 JSON 序列化的完整载荷，
    sync 内核不回查业务表补齐（§0.1 单向依赖）。
    """

    owner_id: str
    # 事件归属的资源身份（如所属 agent/human 的 hasn_id）
    hasn_id: str
    event_type: str
    # 聚合根类型/ID（如 'deck'/'conversation' + 具体 id），供消费者路由
    aggregate_type: str
    aggregate_id: str
    payload: dict[str, Any]
    # 事件发生时刻；None 由 append 落库时取 now()
    occurred_at: datetime | None = None
    # 幂等去重键（R2-07）：上游子系统标识（如 'hasn_im'）+ 上游源事件 id（如集成事件 event_id）。
    # 二者同在同缺——都给才启用去重、都省则退化为普通 append。sync_projector 扇出各 owner 用同一
    # source_event_id，去重键含 owner_id（在 append_event 函数内），故各 owner 各落一行不误去重。
    producer: str | None = None
    source_event_id: str | None = None


@dataclass(frozen=True)
class SyncEventRef:
    """已落库同步事件的引用（append 的返回值）。"""

    owner_id: str
    revision: int
    event_id: str
    event_type: str
    # R2-07：命中 (owner_id, producer, source_event_id) 已落行→True（返回原 revision，未新增行）。
    deduped: bool = False


@dataclass(frozen=True)
class StoredSyncEvent:
    """pull 返回的通用事件信封；payload 原样来自同步事件表。"""

    event_id: str
    event_type: str
    revision: int
    occurred_at: datetime
    payload: dict[str, Any]


@dataclass(frozen=True)
class FullRefreshContract:
    """游标不可继续增量拉取时的显式 full-refresh 契约。"""

    owner_id: str
    reason: Literal['cursor_expired', 'cursor_ahead']
    requested_revision: int
    min_available_revision: int
    head_revision: int
    required: bool = True


@dataclass(frozen=True)
class PullResult:
    """一页通用同步事件或 full-refresh 指令。"""

    events: tuple[StoredSyncEvent, ...]
    next_cursor: str
    has_more: bool
    full_refresh: FullRefreshContract | None = None


@dataclass(frozen=True)
class InboxEnvelope:
    """daemon 上行到 sync inbox 的不透明业务信封。"""

    owner_id: str
    node_id: str
    client_event_id: str
    hasn_id: str
    event_type: str
    payload: dict[str, Any]
    dedupe_key: str | None = None


@dataclass(frozen=True)
class InboxAcceptance:
    """单条 inbox 接收结果。"""

    client_event_id: str
    status: Literal['accepted', 'duplicate', 'conflict']


@dataclass(frozen=True)
class ClaimedInboxEvent:
    """worker 已持有租约的一条 inbox 事件。"""

    row_id: int
    envelope: InboxEnvelope
    attempt_count: int
    idempotency_key: str
    locked_by: str


@dataclass(frozen=True)
class PushResult:
    """一批不透明信封的接收结果。"""

    items: tuple[InboxAcceptance, ...]

    @property
    def accepted(self) -> int:
        """首次接收和幂等重复均视为已接收。"""
        return sum(item.status in {'accepted', 'duplicate'} for item in self.items)


@dataclass(frozen=True)
class RetentionResult:
    """单轮 retention 结果。"""

    deleted: int
