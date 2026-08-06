# HASN 模块合并总结

> 日期：2026-03-23
> 范围：hasn-cloud-backend 后端

---

## 一、背景与问题

HASN（Human-Agent Social Network）相关代码原先分散在三个独立模块中，导致：

1. **循环引用**：hasn_core 引用 hasn.model，hasn_social 引用 hasn_core.crud（部分不存在）
2. **运行时报错**：socketio/actions.py 和 contacts.py 导入了不存在的 CRUD 模块
3. **路径不统一**：三个模块各有独立的 API 前缀（`/hasn/`、`/hasn_core/`、`/hasn_social/`）

### 合并前模块分布

| 模块 | 路径 | 内容 |
|------|------|------|
| `hasn` | `backend/app/hasn/` | HasnClients + HasnAgents（客户端设备 & Agent 表） |
| `hasn_core` | `backend/app/hasn_core/` | HasnHumans + Messages + Conversations + UnreadCounts + WS + Auth + MessageRouter |
| `hasn_social` | `backend/app/hasn_social/` | HasnContact（联系人 + 好友请求 + 守门人） |

---

## 二、合并目标

- 所有 HASN 功能统一到 `hasn_core` 模块
- 删除 `hasn` 和 `hasn_social` 两个旧模块
- API 路径统一到 `/api/v1/hasn/` 前缀
- 解决所有循环引用和缺失导入问题

---

## 三、执行步骤

### 第 1 步：复制 hasn 模块文件（12 个文件）

将以下文件从 `backend/app/hasn/` 复制到 `backend/app/hasn_core/`，同时把 `backend.app.hasn.` 替换为 `backend.app.hasn_core.`：

- `model/hasn_agents.py`、`model/hasn_clients.py`
- `schema/hasn_agents.py`、`schema/hasn_clients.py`
- `crud/crud_hasn_agents.py`、`crud/crud_hasn_clients.py`
- `service/hasn_agents_service.py`、`service/hasn_clients_service.py`
- `api/v1/app/hasn_agents.py`、`api/v1/app/hasn_clients.py`
- `api/v1/admin/hasn_agents.py`、`api/v1/admin/hasn_clients.py`

### 第 2 步：复制 hasn_social 模块文件（9 个文件）

将以下文件从 `backend/app/hasn_social/` 复制到 `backend/app/hasn_core/`，同时把 `backend.app.hasn_social.` 替换为 `backend.app.hasn_core.`：

- `model/hasn_contacts.py`
- `schema/hasn_contacts.py`、`schema/admin/hasn_contacts.py`
- `crud/crud_contact.py`、`crud/admin/hasn_contacts.py`
- `service/route_guard.py`、`service/admin/hasn_contacts.py`
- `api/v1/contacts.py` → `api/v1/app/contacts.py`
- `api/v1/admin/hasn_contacts.py`

### 第 3 步：创建 admin 子目录 `__init__.py`

```
hasn_core/crud/admin/__init__.py
hasn_core/schema/admin/__init__.py
hasn_core/service/admin/__init__.py
```

### 第 4 步：更新 model 导出

在 `hasn_core/model/__init__.py` 新增三个导出：

```python
from backend.app.hasn_core.model.hasn_agents import HasnAgents as HasnAgents
from backend.app.hasn_core.model.hasn_clients import HasnClients as HasnClients
from backend.app.hasn_core.model.hasn_contacts import HasnContact as HasnContact
```

### 第 5 步：创建 4 个兼容性 CRUD 文件

为 socketio/actions.py 和 contacts.py 提供它们需要的业务查询方法：

| 文件 | 提供的方法 | 调用方 |
|------|-----------|--------|
| `crud/crud_agent.py` | `get_by_id`, `get_by_star_id` | socketio、contacts |
| `crud/crud_human.py` | `get_by_id`, `get_by_star_id` | contacts |
| `crud/crud_message.py` | `create`, `mark_read` | socketio |
| `crud/crud_conversation.py` | `get_or_create_direct`, `update_last_message` | socketio |

### 第 6 步：修复 hasn_core 内部 import

| 文件 | 修改内容 |
|------|---------|
| `service/hasn_auth.py` | `hasn.model.hasn_agents` → `hasn_core.model.hasn_agents`；`hasn.model.hasn_clients` → `hasn_core.model.hasn_clients`；末尾添加 `hasn_auth = hasn_auth_from_jwt` 别名 |
| `api/v1/hasn_auth_api.py` | 同上两处 import 替换 |
| `service/message_router.py` | `hasn.model.hasn_agents` → `hasn_core.model.hasn_agents`；`hasn_social.model.hasn_contacts` → `hasn_core.model.hasn_contacts` |
| `service/ws_router.py` | `hasn.model.hasn_agents` → `hasn_core.model.hasn_agents` |

### 第 7 步：修复外部文件 import

| 文件 | 修改内容 |
|------|---------|
| `backend/common/socketio/actions.py` | `hasn_social.service.route_guard` → `hasn_core.service.route_guard` |

### 第 8 步：重写路由文件

`hasn_core/api/router.py` 完全重写，合并三个模块的路由，统一前缀：

| 路由层 | 前缀 | 包含路由 |
|--------|------|---------|
| 管理端 (v1) | `/api/v1/hasn/` | hasn-humans, hasn-conversations, hasn-messages, hasn-unread-counts, hasn-clients, hasn-agents, hasn-contacts |
| 用户端 (app) | `/api/v1/hasn/app/` | hasn (认证), hasn-messages, hasn-unread-counts, hasn-clients, hasn-agents, contacts |
| Agent (agent) | `/api/v1/hasn/agent/` | hasn-messages, hasn-unread-counts |
| 公开 (open_api) | `/api/v1/hasn/open/` | hasn-messages, hasn-unread-counts |
| WebSocket (ws) | `/api/v1/hasn/` | ws/client |

### 第 9 步：更新主路由

`backend/app/router.py` 删除旧的 hasn 和 hasn_core 导入，统一为：

```python
from backend.app.hasn_core.api.router import v1 as hasn_v1, app as hasn_app, agent as hasn_agent, open_api as hasn_open, ws as hasn_ws
```

### 第 10 步：删除旧模块 + 验证

```bash
rm -rf backend/app/hasn/
rm -rf backend/app/hasn_social/
```

---

## 四、验证结果

| 验证项 | 结果 |
|--------|------|
| 模型导入（7 个模型） | ✅ 通过 |
| CRUD 导入（5 标准 + 4 兼容） | ✅ 通过 |
| 服务导入（hasn_auth, route_guard, ws_router, message_router） | ✅ 通过 |
| 路由导入（v1, app, agent, open_api, ws） | ✅ 通过 |
| 主路由加载 | ✅ 通过（474 总路由，77 个 HASN 路由） |
| 旧模块残留引用检查 | ✅ 无残留 |
| API 路径统一 | ✅ 全部在 `/api/v1/hasn/` 下 |

---

## 五、合并后文件结构

```
backend/app/hasn_core/
├── __init__.py
├── model/
│   ├── __init__.py              # 导出全部 7 个模型
│   ├── hasn_humans.py           # 原 hasn_core
│   ├── hasn_conversations.py    # 原 hasn_core
│   ├── hasn_messages.py         # 原 hasn_core
│   ├── hasn_unread_counts.py    # 原 hasn_core
│   ├── hasn_agents.py           # ← 从 hasn 迁入
│   ├── hasn_clients.py          # ← 从 hasn 迁入
│   └── hasn_contacts.py         # ← 从 hasn_social 迁入
├── schema/
│   ├── hasn_humans.py
│   ├── hasn_conversations.py
│   ├── hasn_messages.py
│   ├── hasn_unread_counts.py
│   ├── hasn_agents.py           # ← 从 hasn 迁入
│   ├── hasn_clients.py          # ← 从 hasn 迁入
│   ├── hasn_contacts.py         # ← 从 hasn_social 迁入
│   └── admin/
│       └── hasn_contacts.py     # ← 从 hasn_social 迁入
├── crud/
│   ├── crud_hasn_humans.py
│   ├── crud_hasn_conversations.py
│   ├── crud_hasn_messages.py
│   ├── crud_hasn_unread_counts.py
│   ├── crud_hasn_agents.py      # ← 从 hasn 迁入（标准 CRUDPlus）
│   ├── crud_hasn_clients.py     # ← 从 hasn 迁入（标准 CRUDPlus）
│   ├── crud_contact.py          # ← 从 hasn_social 迁入
│   ├── crud_agent.py            # ★ 新建（业务查询兼容层）
│   ├── crud_human.py            # ★ 新建（业务查询兼容层）
│   ├── crud_message.py          # ★ 新建（socketio 兼容层）
│   ├── crud_conversation.py     # ★ 新建（socketio 兼容层）
│   └── admin/
│       └── hasn_contacts.py     # ← 从 hasn_social 迁入
├── service/
│   ├── hasn_auth.py             # 修复 import + 添加 hasn_auth 别名
│   ├── hasn_humans_service.py
│   ├── hasn_conversations_service.py
│   ├── hasn_messages_service.py
│   ├── hasn_unread_counts_service.py
│   ├── hasn_agents_service.py   # ← 从 hasn 迁入
│   ├── hasn_clients_service.py  # ← 从 hasn 迁入
│   ├── message_router.py        # 修复 import
│   ├── ws_router.py             # 修复 import
│   ├── route_guard.py           # ← 从 hasn_social 迁入
│   └── admin/
│       └── hasn_contacts.py     # ← 从 hasn_social 迁入
├── api/
│   ├── router.py                # ★ 重写（合并三个路由）
│   ├── ws_client.py
│   └── v1/
│       ├── hasn_auth_api.py     # 修复 import
│       ├── admin/
│       │   ├── hasn_humans.py
│       │   ├── hasn_conversations.py
│       │   ├── hasn_messages.py
│       │   ├── hasn_unread_counts.py
│       │   ├── hasn_agents.py   # ← 从 hasn 迁入
│       │   ├── hasn_clients.py  # ← 从 hasn 迁入
│       │   └── hasn_contacts.py # ← 从 hasn_social 迁入
│       ├── app/
│       │   ├── hasn_messages.py
│       │   ├── hasn_unread_counts.py
│       │   ├── hasn_agents.py   # ← 从 hasn 迁入
│       │   ├── hasn_clients.py  # ← 从 hasn 迁入
│       │   └── contacts.py      # ← 从 hasn_social 迁入
│       ├── agent/
│       │   ├── hasn_messages.py
│       │   └── hasn_unread_counts.py
│       └── open/
│           ├── hasn_messages.py
│           └── hasn_unread_counts.py
```

---

## 六、删除的目录

```
backend/app/hasn/           # 整个目录（12 个 .py 文件）
backend/app/hasn_social/    # 整个目录（14 个 .py 文件）
```

---

## 七、API 路径变更对照

| 合并前 | 合并后 |
|--------|--------|
| `/api/v1/hasn/hasn-clients/...` | `/api/v1/hasn/hasn-clients/...`（不变） |
| `/api/v1/hasn/hasn-agents/...` | `/api/v1/hasn/hasn-agents/...`（不变） |
| `/api/v1/hasn/app/hasn-clients/...` | `/api/v1/hasn/app/hasn-clients/...`（不变） |
| `/api/v1/hasn/app/hasn-agents/...` | `/api/v1/hasn/app/hasn-agents/...`（不变） |
| `/api/v1/hasn_core/hasn-humanss/...` | `/api/v1/hasn/hasn-humans/...`（路径修正） |
| `/api/v1/hasn_core/hasn-messagess/...` | `/api/v1/hasn/hasn-messages/...`（路径修正） |
| `/api/v1/hasn_core/hasn-conversationss/...` | `/api/v1/hasn/hasn-conversations/...`（路径修正） |
| `/api/v1/hasn_core/hasn-unread-countss/...` | `/api/v1/hasn/hasn-unread-counts/...`（路径修正） |
| `/api/v1/hasn_core/app/hasn/...`（认证） | `/api/v1/hasn/app/hasn/...`（认证） |
| `/api/v1/hasn_core/agent/...` | `/api/v1/hasn/agent/...` |
| `/api/v1/hasn_core/open/...` | `/api/v1/hasn/open/...` |
| （hasn_social 路由未注册） | `/api/v1/hasn/app/contacts/...`（新增） |
| `/api/v1/hasn/ws/client` | `/api/v1/hasn/ws/client`（不变） |

> ⚠️ 注意：原 hasn_core 路由中的 prefix 存在拼写问题（如 `hasn-humanss` 多了一个 s），此次合并中已一并修正。

---

## 八、注意事项

1. **前端适配**：如果管理端或桌面端有调用 `/api/v1/hasn_core/...` 路径的请求，需更新为 `/api/v1/hasn/...`
2. **数据库无变更**：本次合并仅涉及代码层面的文件迁移和 import 修复，不涉及数据库表结构变更
3. **生产部署**：部署时需完整替换后端代码，确保旧的 `hasn/` 和 `hasn_social/` 目录被删除
