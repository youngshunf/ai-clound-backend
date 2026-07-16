"""hasn_sync.ports.dto · 同步内核对外 DTO（frozen 值对象）

**边界铁律（§0.1/D2）**：envelope 的 payload 由业务方构造为**完整载荷**，sync 不反查
业务表补齐。这里只声明「事件信封」与「已落事件引用」两个值对象，不携带任何业务 schema 类型。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any


@dataclass(frozen=True)
class SyncEnvelope:
    """待写入的同步事件信封（业务方构造完整载荷后交给 append）。"""

    owner_id: str
    event_type: str
    payload: dict[str, Any]
    producer: str
    # 幂等去重键：同一 (producer, source_event_id) 只落一条 owner 事件（§7.2）
    source_event_id: str
    occurred_at: datetime | None = None
    # 可选路由/分片元信息（不含业务语义）
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class SyncEventRef:
    """已落库同步事件的引用（append 的返回值）。"""

    owner_id: str
    revision: int
    event_type: str
    # 幂等命中已存在事件时为 True（未新增行）
    deduped: bool = False
