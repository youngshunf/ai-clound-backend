"""hasn_sync.ports.sync_appender · SyncAppender 契约（§8.1）

**唯一 chokepoint**：所有跨领域同步事件 append 必须经此 port → 落到 `hasn_sync.append_event(...)`
单实现（§3.2）。envelope 校验、幂等去重（`(producer, source_event_id)`）、owner revision 单调
分配只存在那一份 PG 函数里，禁止在业务侧另起一套 append。

依赖方向（§0.1）：`hasn_im` 的 sync_projector 消费者 → 本 port 是**允许**的（本 port 更底层、
无业务语义）；反之 sync 内核**不得**回调任何业务 service。
"""

from __future__ import annotations

from typing import Protocol, runtime_checkable

from backend.app.hasn_sync.ports.dto import SyncEnvelope, SyncEventRef


@runtime_checkable
class SyncAppender(Protocol):
    """跨领域同步事件的唯一写入口（薄封装 append_event 单实现）。"""

    async def append(self, envelope: SyncEnvelope) -> SyncEventRef:
        """把一条完整载荷的 owner 事件追加进同步流。

        幂等：同一 `(producer, source_event_id)` 重复调用返回既有事件（`deduped=True`），
        不新增行、不推进 revision。
        """
        ...
