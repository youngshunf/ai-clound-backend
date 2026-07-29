# 方案 B 离线帧 Durable 覆盖矩阵

> 状态：施工中  
> 对账时间：2026-07-30  
> 适用开关：`HASN_OFFLINE_RECOVERY=redis|dual|sync`

## 1. 判定口径

只有同时满足以下条件的帧才归类为“durable sync”：

1. 业务事实或命令先持久化到 PostgreSQL；
2. daemon 断线后能通过 sync pull 或权威快照主动恢复；
3. SQLite 事务幂等提交成功后才推进 cursor；
4. WS 与 sync 同时到达时能依靠稳定 ID 保证用户可见一次。

“PostgreSQL 已有记录”不等于已经完成 durable 恢复。若 daemon 尚未应用对应 sync
事件、重连不会主动读取快照，仍归类为“缺口待补”。

## 2. 当前覆盖矩阵

| 离线候选帧 | 生产来源 | PostgreSQL 事实或命令 | sync / 快照路径 | 稳定身份 | daemon 当前状态 | 分类 |
|---|---|---|---|---|---|---|
| `hasn.message.new` | `hasn_im.consumers.realtime_notifier` | `hasn_messages`、IM integration event | `hasn_sync_events(message.new)`；retention gap 后进入消息历史快照 | `message_id` | 已实现 sync pull、SQLite 幂等写和历史快照 | durable sync |
| `hasn.message.invalidated` | `hasn_im.consumers.realtime_notifier` | 消息撤回事实、IM integration event | `hasn_sync_events(message.recalled)`；历史快照包含最终状态 | `event_id`，辅以 `message_id` | 增量 sync 尚未应用 `message.recalled` | 缺口待补 |
| `hasn.conversation.invalidated` | `hasn_im.consumers.realtime_notifier` | 会话变更事实、IM integration event | `hasn_sync_events(conversation.updated)`；会话权威快照 | `event_id`，辅以 `conversation_id` | 增量 sync 尚未应用 `conversation.updated` | 缺口待补 |
| `hasn.task.exec` | `hasn_task.service.task_dispatch_outbox.TaskDispatchRelay` | `hasn_task.task_dispatch_outbox` | 尚无 daemon 可拉取的执行命令 feed | `dispatch_id=task:run:{run_id}:exec` | relay 在实时端口返回后即把 outbox 标记完成；离线超过 Redis TTL 无法恢复 | 缺口待补 |
| `hasn.contact.request_received` | `hasn.service.hasn_contacts_service` | `hasn_contact_requests` | daemon 联系人请求权威快照 | `request_id` | 本地优先读路径会回源刷新请求快照 | durable sync |
| `hasn.contact.connected` | `hasn.service.hasn_contacts_service`、`hasn.api.v1.app.contacts` | `hasn_contacts`、`hasn_contact_requests` | daemon 登录及联系人读路径刷新权威快照 | `request_id` | 快照幂等覆盖本地联系人镜像 | durable sync |
| `hasn.contact.removed` | `hasn.service.hasn_contacts_service` | `hasn_contacts` 权威关系状态 | daemon 联系人权威快照删除缺失关系 | `peer_id` | 快照归档云端已不存在的关系 | durable sync |
| `WorkspaceSwitched` | `hasn.service.workspace_notification_subscriber` | owner 工作台 `active_enterprise_id` | 工作空间权威快照 | 暂无事件 ID | 尚未证明 daemon 重连会主动刷新并失效本地状态 | 缺口待补 |
| `hasn.typing` | `hasn_im.api.ws_node._handle_typing` | 无；它不是业务事实 | 无需恢复 | 无 | 仅在线有意义 | 瞬时无需离线 |

## 3. 已落门禁

- `offline_frame_policy.py` 是离线帧分类、稳定身份和恢复说明的唯一注册表。
- `NodeSessionRealtimeGateway.push_to_owner` 拒绝未登记的方法。
- `_enqueue_offline` 在写 Redis 前解析真实 HASN 信封并校验稳定身份。
- `redis` 与 `dual` 对 durable/gap 帧继续写 Redis；所有模式都不保存
  `hasn.typing`。
- `sync` 对 durable 帧停写 Redis，对 gap 帧显式报错，禁止静默丢失。
- `sync` 模式的 claim、ACK 和遗留 get 三个入口都在构造
  `hasn:offline:*` key 前返回。
- AST 静态守卫枚举生产 `RealtimeFrame` 方法；新增字面量方法未登记时 CI 失败。唯一动态
  方法来自任务 outbox，并被固定 `_METHOD='hasn.task.exec'` 校验。

## 4. 必须补齐后才能进入 `sync`

1. 将 `task.exec` 作为 PostgreSQL durable 命令提供给 daemon，并以 `dispatch_id`
   在 SQLite 内原子认领；WS 和 sync 竞态只能执行一次。
2. daemon 增量 sync 应用 `message.recalled` 与 `conversation.updated`，SQLite 提交失败时
   不推进 cursor。
3. 为工作空间切换建立可验证的重连快照刷新，或追加 PostgreSQL sync event；不能只依赖
   在线通知。
4. 完成真实 PostgreSQL、真实云端和双设备 E2E 后，把对应注册项从 `gap` 改为
   `durable_sync`。

## 5. 遗留路径

`hasn_im.application.message_service.recall_message` 仍构造旧
`{"cmd":"MESSAGE_RECALLED"}` 载荷，但仓内没有调用方；现行撤回入口走
`LocalImGateway.recall_message` 和 integration event。旧函数不得重新接线，后续应单独删除。
若它被意外调用，离线策略会因缺少 `method` 显式失败，不会把未审计帧写入 Redis。
