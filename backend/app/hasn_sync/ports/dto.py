"""hasn_sync.ports.dto · 同步内核对外 DTO（frozen 值对象）

**边界铁律（§0.1/D2）**：envelope 的 payload 由业务方构造为**完整载荷**，sync 不反查
业务表补齐。这里只声明「事件信封」与「已落事件引用」两个值对象，不携带任何业务 schema 类型。

**契约演进（R1→R2-07）**：R1 版 envelope 对齐**现网** `_append_sync_event_with_id` 的真实
字段（`hasn_id/aggregate_type/aggregate_id`）——如实包装，不声明现网还不支持的能力（避免
fake）。R2-07 建成 `hasn_sync.append_event(...)` PG 函数后，去重键 `(producer, source_event_id)`
才**真正**落地：届时给 envelope 追加 `producer`/`source_event_id`、给 ref 追加 `deduped`，
并把封装目标从当前 chokepoint 原地换成 PG 函数（port 形状稳定，仅实现替换）。
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Any


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


@dataclass(frozen=True)
class SyncEventRef:
    """已落库同步事件的引用（append 的返回值）。"""

    owner_id: str
    revision: int
    event_id: str
    event_type: str
