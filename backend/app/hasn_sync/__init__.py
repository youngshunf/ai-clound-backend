"""hasn_sync · 跨领域同步内核（独立模块·独立 schema）

拥有 owner 维事件流、cursor、inbox、retention 和 full-refresh 协议。

**铁律（§0.1/§8.1）**：
- `hasn_sync` 是纯同步内核，**不 import、查询或外键关联任何业务模块/schema**；
- 业务方（含 IM 的 sync_projector）构造**完整载荷**，sync 不反查业务表补载荷（D2）；
- 所有 append 统一经 `hasn_sync.append_event(...)` 单实现进入（§3.2），`SyncAppender`
  port 只是该函数的薄封装——envelope 校验、幂等与 revision 分配只存在这一份实现。
"""
