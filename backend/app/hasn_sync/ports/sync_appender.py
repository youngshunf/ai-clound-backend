"""hasn_sync.ports.sync_appender · SyncAppender 契约（§3.2/§8.1）

**唯一 chokepoint**：所有跨领域同步事件 append 必须经此 port → 落到单一实现（§3.2）。
owner revision 单调分配（per-owner advisory xact lock）只存在那一份实现里，禁止在业务侧
另起一套 append。R1 阶段封装现网 `SqlAlchemySyncGateway._append_sync_event_with_id`；
R2-07 建成 `hasn_sync.append_event(...)` PG 函数后原地替换封装目标，port 形状不变。

**事务契约（§7.1）**：`append` 接调用方 `db`，与业务写**同一事务**落库（同一 commit）——
禁止 appender 自开事务，否则业务写与同步事件裂成两次 commit（正是本重构要消灭的裂脑）。

依赖方向（§0.1）：`hasn_im` 的 sync_projector 消费者 → 本 port 是**允许**的（本 port 更底层、
无业务语义）；反之 sync 内核**不得**回调任何业务 service。
"""

from __future__ import annotations

from typing import Protocol, runtime_checkable

from sqlalchemy.ext.asyncio import AsyncSession

from backend.app.hasn_sync.ports.dto import SyncEnvelope, SyncEventRef


@runtime_checkable
class SyncAppender(Protocol):
    """跨领域同步事件的唯一写入口（薄封装单实现的 revision 分配）。"""

    async def append(self, db: AsyncSession, envelope: SyncEnvelope) -> SyncEventRef:
        """在调用方事务内追加一条完整载荷的 owner 事件，返回已落库引用（含分配的 revision）。

        `db` 必须是业务写所在的同一 Session——append 与业务写同事务落库（§7.1）。
        """
        ...
