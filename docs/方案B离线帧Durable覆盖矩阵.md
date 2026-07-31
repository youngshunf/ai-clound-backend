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
| `hasn.message.invalidated` | `hasn_im.consumers.realtime_notifier` | 消息撤回事实、IM integration event | `hasn_sync_events(message.recalled)`；历史快照包含最终状态 | `event_id`，辅以 `message_id` | 增量 sync 已原子修正消息状态、正文和会话预览；重放幂等 | durable sync |
| `hasn.conversation.invalidated` | `hasn_im.consumers.realtime_notifier` | 会话变更事实、IM integration event | `hasn_sync_events(conversation.updated)`；会话权威快照 | `event_id`，辅以 `conversation_id` | 增量 sync 按 revision 回源权威投影；投影滞后或依赖故障时停位重放 | durable sync |
| `hasn.task.exec` | `hasn_task.service.task_dispatch_outbox.TaskDispatchRelay` | `hasn_task.task_dispatch_outbox` 与同事务 `hasn_sync_events(task.exec)` | Owner sync feed；daemon SQLite `sync_command_inbox` | `dispatch_id=task:run:{run_id}:exec` | 实时帧与 sync 共用幂等收件箱；登录、重连和周期补拉接管到期租约 | durable sync |
| `hasn.contact.request_received` | `hasn.service.hasn_contacts_service` | `hasn_contact_requests` | daemon 联系人请求权威快照 | `request_id` | 本地优先读路径会回源刷新请求快照 | durable sync |
| `hasn.contact.connected` | `hasn.service.hasn_contacts_service`、`hasn.api.v1.app.contacts` | `hasn_contacts`、`hasn_contact_requests` | daemon 登录及联系人读路径刷新权威快照 | `request_id` | 快照幂等覆盖本地联系人镜像 | durable sync |
| `hasn.contact.removed` | `hasn.service.hasn_contacts_service` | `hasn_contacts` 权威关系状态 | daemon 联系人权威快照删除缺失关系 | `peer_id` | 快照归档云端已不存在的关系 | durable sync |
| `WorkspaceSwitched` | `hasn.service.workspace_notification_subscriber` | owner 工作台 `active_enterprise_id` | 工作台读面始终读取权威接口 | 无；daemon 不消费该历史兼容帧 | 不回放不会丢失客户端状态，写 Redis 反而只会保留无消费者通知 | 瞬时无需离线 |
| `hasn.typing` | `hasn_im.api.ws_node._handle_typing` | 无；它不是业务事实 | 无需恢复 | 无 | 仅在线有意义 | 瞬时无需离线 |

## 3. 已落门禁

- `offline_frame_policy.py` 是离线帧分类、稳定身份和恢复说明的唯一注册表。
- `NodeSessionRealtimeGateway.push_to_owner` 拒绝未登记的方法。
- `_enqueue_offline` 在写 Redis 前解析真实 HASN 信封并校验稳定身份。它位于「业务事实
  已提交后」的 best-effort 推送路径上，策略异常只记 `error` 并跳过入队，不冒泡成 5xx；
  真正的 fail-closed 由 CI 静态守卫（未登记帧）和启动门禁
  `assert_offline_recovery_mode_supported`（`sync` 模式的 durable 缺口）承担。
- 入队走原子 Lua：`RPUSH` + 超出 `OFFLINE_MAX_LENGTH=1000` 时 `LTRIM` 最旧 + `EXPIRE`。
  先前 `rpush` 后单独 `expire` 的写法对「长期离线且持续收帧」的实体等于永不过期且无长度
  上限。被裁剪的帧一律是 `durable_sync`，仍可由 PostgreSQL sync/history 恢复，裁剪会告警。
- `redis` 与 `dual` 对 durable 帧继续写 Redis；所有模式都不保存
  `hasn.typing` 和未被 daemon 消费的 `WorkspaceSwitched`。
- **`dual` 仍从 Redis 补推**：它是切 `sync` 前的观测窗，若此时就停读，daemon 侧 sync
  一旦有缺口，用户在观测期内已经丢帧、7 天对账只剩事后统计。同一帧经 WS 与 sync pull
  重复到达由客户端按稳定 `message_id`/`event_id` 去重（hasn-node
  `pull_once_replay_is_idempotent_on_duplicate_message_id` 覆盖）。
- 只有 `sync` 停写并停读 Redis；claim、ACK 和遗留 get 三个入口在 `sync` 模式下于构造
  `hasn:offline:*` key 前返回。
- AST 静态守卫枚举生产 `RealtimeFrame` 方法；新增字面量方法未登记时 CI 失败。唯一动态
  方法来自任务 outbox，并被固定 `_METHOD='hasn.task.exec'` 校验。
- `hasn_offline_shadow_reconcile` 每 5 分钟扫描真实 Redis LIST，并按稳定身份与当前 schema
  中的 `hasn_sync_events` 对账；`sync-only` 只统计最近七天，当前 Redis 候选则额外精确
  查询仍保留的历史 sync 事实，避免 LIST 整键 TTL 被新追加刷新后把旧帧误报为不可恢复。
  指标只使用 `result` 低基数标签。
- 走权威快照（而非 sync event）恢复的联系人帧必须**真实核验**：`request_received`/
  `connected` 查 `hasn_contact_requests` 仍有该行，`removed` 查 `hasn_contacts` 确已无该
  关系；核验不过计入 `snapshot_unverified` 并汇入 `redis_only_unrecoverable`。否则
  「映射不到 sync 类型就算快照兜底」会让 `redis_only_unrecoverable=0` 这条门槛对联系人
  类帧恒真、形同虚设。
- 存量 `transient` 影子帧单列计数，不再和「真的解析不了」混进同一个 `malformed`。

## 4. 进入 `sync` 前仍须完成的生产门槛

1. 在真实 PostgreSQL 上验证任务 run、业务 outbox 与 `task.exec` sync event 同事务回滚、
   幂等重试和 Redis 影子三集合对账。
2. 在真实云端与双设备上验证断网、撤回、会话成员变更、任务命令、retention gap 和空库恢复。
3. 生产切到 `dual` 后连续 7 天采集 shadow 报告，且每轮
   `redis_only_unrecoverable=0`。
4. 完成以上门槛前不得切到 `sync`；实现已补齐不等于生产证据已经成立。

## 5. 遗留路径

`hasn_im.application.message_service.recall_message` 仍构造旧
`{"cmd":"MESSAGE_RECALLED"}` 载荷，但仓内没有调用方；现行撤回入口走
`LocalImGateway.recall_message` 和 integration event。旧函数不得重新接线，后续应单独删除。
若它被意外调用，离线策略会因缺少 `method` 显式失败，不会把未审计帧写入 Redis。
