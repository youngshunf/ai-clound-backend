"""hasn_im · 通信与关系域（独立模块·独立 schema·单向依赖）

16 号设计事实源：`docs/hasn-node设计文档/01-核心架构/16-云端IM-Python底层服务化重构设计*.md`。

本模块拥有消息、会话、成员周期、已读游标、抑制箱、会话资产授权、联系人、关系请求、
信任、拉黑、通信设置和确定性判权。

**单向依赖铁律（§0.1/§2.2）**：
- 其他模块只能 import `hasn_im.ports`（Protocol + DTO），不得 import application/adapters/ORM model。
- hasn_im domain/application 不得反向调用上游业务 service/crud/API（IM 热路径不出 IM 域）。
- 调用更底层、无业务语义的 `hasn_sync.ports.SyncAppender` 不属于反向业务依赖。
"""
