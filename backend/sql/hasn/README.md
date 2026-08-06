# HASN S0/S1 SQL 与 codegen 前置闭环

> 阶段：S0/S1（合并前置任务）
> 范围：仅 `hasn-cloud-backend` 服务端轨，迁移式重构，不推倒重写。
> 契约源：`docs/openapi-hasn-cloud-v1.yaml`、`sql/errors.md`、`../docs/hasn-node设计文档/46/48/49/50`。

## S0/S1 执行计划

1. **S0 契约读取与冻结检查**
   - 读取 P0 tag `contracts-v1.0.0-p0`、OpenAPI、错误码、codegen 说明。
   - 验证 OpenAPI 可解析、HASN paths 均为 `/api/v1/hasn/*`。
   - 不修改 HASN Protocol schemas、OpenAPI、错误码。
2. **S1 迁移式模型设计**
   - 保留 `hasn_humans` / `hasn_agents` / `hasn_contacts` / `hasn_conversations` / `hasn_messages` 等旧资产。
   - 只补字段、索引、backfill/rollback；不删除旧表、不清空数据。
   - 所有 HASN SQL 均放在 `backend/sql/hasn/`。
3. **codegen 可行性验证**
   - 单表 `hasn_*.sql` 作为 codegen 输入，命令形态：
     `uv run fba codegen generate --sql-file backend/sql/hasn/<table>.sql --app hasn --execute`。
   - 本轮不手写 CRUD 样板；如生成器不能覆盖基础 CRUD，后续执行应输出 `CODEGEN_GAP`。
4. **S2/S4/S3/S5/S6 延后**
   - 本目录只给 S2/S4/S3/S5/S6 所需表设计，不实现 onboarding、消息中枢、sandbox、channel、反滥用业务逻辑。

## SQL 文件清单与归类

### codegen 输入表（基础 CRUD / 管理查询骨架由生成器负责）

- 既有资产升级：
  - `hasn_humans.sql`
  - `hasn_agents.sql`
  - `hasn_agent_capabilities.sql`
  - `hasn_contacts.sql`
  - `hasn_conversations.sql`
  - `hasn_messages.sql`
  - `hasn_group_members.sql`
  - `hasn_unread_counts.sql`
  - `hasn_audit_log.sql`
  - `hasn_notifications.sql`
  - `hasn_trade_sessions.sql`
  - `hasn_clients.sql`（旧客户端设备资产，保留兼容；新 hasn-node 主入口使用 `hasn_nodes`）
- S0/S1 identity / owner binding：
  - `hasn_nodes.sql`
  - `hasn_node_bindings.sql`
  - `hasn_owner_api_keys.sql`
- S1 新协议能力表：
  - `hasn_sync_events.sql`
  - `hasn_sync_inbox_events.sql`
  - `hasn_agent_runtime_reports.sql`
  - `hasn_suppressed_messages.sql`
- S1 先落 SQL、业务后续阶段启用的表：
  - `hasn_pending_intents.sql`（S5 使用，TTL 24h）
  - `hasn_channel_bindings.sql`（S5 使用）
  - `hasn_tenant_sandboxes.sql`（S3 使用）

### 非 CRUD 业务表 / 迁移补偿

- `hasn_sync_events.sql`、`hasn_sync_inbox_events.sql`、`hasn_suppressed_messages.sql`、`hasn_agent_runtime_reports.sql` 虽可用 codegen 生成管理/查询骨架，但业务写入必须由后续 S4/S3/S5 服务逻辑控制，不能依赖通用 CRUD 代表完整业务语义。
- `memory_namespace_revisions.sql` 是记忆 namespace 权威 revision 表，归属 memory sync 业务，不走通用 CRUD 生成写入。
- `V001__hasn_s0_s1_existing_assets__migration.sql`：旧表补字段、索引、保守 backfill。
- `V001__hasn_s0_s1_existing_assets__rollback.sql`：仅回滚 S1 additive 字段/索引；S2/S4 写入后不能无备份执行。

## 旧表字段迁移 / backfill / rollback 方案

| 表 | 迁移 | backfill | rollback |
|---|---|---|---|
| `hasn_humans` | 补 `profile_revision` / `policy_revision` / `sync_revision` | 默认 `1` | 删除新增 revision 字段与索引 |
| `hasn_agents` | 补 Profile、Capability Summary、runtime summary 脱敏缓存 | `display_name <- name`，`bio <- description`，JSON 字段默认 `{}` | 删除新增字段与索引 |
| `hasn_contacts` | 补 channel 来源、关系/sync revision | revision 默认 `1`，channel 字段为空 | 删除新增字段与索引 |
| `hasn_conversations` | 补 `owner_id`、`hasn_id`、`peer_hasn_id`、`sync_revision`、`deleted_at` | direct 会话先由 participant_a/b 保守回填；群聊后续 S4 按成员生成 owner 视图 | 删除新增字段与索引 |
| `hasn_messages` | 补 `owner_id`、`hasn_id`、sender/recipient、runtime/dispatch/sync 字段 | to-Agent 消息优先从 `hasn_agents.owner_id` 推断 owner；其余用 Human/Owner fallback；RuntimeUnavailable 保持 `delivery_status=delivered` + `dispatch_status=runtime_unavailable` | 删除新增字段与索引；执行前必须确认无后续阶段依赖 |

## Runtime 与消息可达性硬边界

- 服务端只保存 runtime summary：`runtime_type`、`runtime_status`、`adapter_registered`、`handle_available`、`binding_id`、`summary_json`。
- 禁止保存：workspace、endpoint、PID、CLI args、OAuth path。
- `RuntimeUnavailable` 不是 `MessageDeliveryFailed`；消息进入 Human/Owner inbox、Agent inbox、owner copy 或 suppressed inbox 后即为可达。
- 每条持久化消息必须规划 `owner_id + hasn_id` 显式归属；`hasn_messages.sql` 和 `hasn_suppressed_messages.sql` 均已包含。

## 验证结果记录

- OpenAPI：`3.1.0` / `info.version=1.0.0-p0` / `paths=17`。
- 错误码：`8024 ERR_MESSAGE_DELIVERY_FAILED` 明确不得用于 RuntimeUnavailable；`8034 ERR_RUNTIME_PRIVATE_METADATA_REJECTED` 覆盖私有元数据拒绝。
- codegen 说明：HASN SQL 统一使用 `backend/sql/hasn/<table>.sql`，CRUD 禁止手写，生成物包括 backend model/schema/crud/service/api、frontend views/api、menu SQL、dict SQL。
- codegen CLI：`uv run fba codegen generate --help` 可用；当前 CLI 无 dry-run 参数，本轮用 parser pytest 验证 `backend/sql/hasn/*.sql` 均满足单表 codegen 输入形态。
- memory sync revision 表保持手写 SQL + 手写服务逻辑，不纳入通用 CRUD 生成链。
- blocking gap：本轮未发现阻止 S0/S1 SQL 设计的 `CONTRACT_GAP`；OpenAPI `MessageHubSendRequest.envelope` 为开放对象，后续 S4 实现必须由服务端映射规则保证 `owner_id + hasn_id`，不得从客户端发明字段。
