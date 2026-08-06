# Ralph Loop M1 — backend Hermes Agent 编排串通 + templates list + chat SSE

> 你是 M1 ralph 的执行体。每次迭代你看到的都是这同一份 PROMPT.md。
> 你的工作产物（代码、`NOTES.md`、commit）会在文件系统和 git history 中持续累积。

---

## 1. 一句话目标

把 `hasn-cloud-backend` 的 Hermes Agent 业务模块从"60% 骨架"推到"MVP 端到端可用"：

1. **`HermesRuntimeClient`** 加 4 个方法：`apply_template` / `get_template_status` / `install_credential` / `uninstall_credential`（runtime 侧 M2 + B3-3a 已就位）
2. **`HermesAgentAppService.create_agent`** 编排串通（按 §07 §4.1 + §09 §4.1）：
   ```
   local record(status='creating')
     → runtime.ensure_agent
     → marketplace 查模板 package_url/file_hash
     → runtime.apply_template
     → ensure_agent_token (LlmNewapiUserMappingService)
     → runtime.install_credential
     → optional runtime.start_gateway (按 payload.start_gateway 决定)
     → local record(status='ready')
   ```
3. **`HermesAgentAppService.delete_agent`** 编排串通：
   ```
   runtime.stop_gateway
     → runtime.uninstall_credential
     → revoke_agent_token
     → runtime.delete_agent
     → local record(deleted_time=now)
   ```
4. **`GET /api/v1/hermes/app/templates`** 新 endpoint：返回 marketplace_app（app_type='agent_template'）+ marketplace_app_version（is_latest=true）联合查询结果，给 website 选模板用
5. **`POST /api/v1/hermes/app/agents/{agent_id}/chat/completions`** 改造为 SSE 流式：FastAPI `StreamingResponse(media_type='text/event-stream')`，把 runtime 的 SSE chunk 透传给浏览器
6. **测试**：补 ≥ 10 个 case 覆盖关键编排路径

**不做**：BYOK 模式（payload.llm_mode='byok' 直接抛 400）、`hermes_agent_template_application` 历史表、SOP 应用、模板 reapply 冲突检测。

> 顺序约束：M1 是 MVP 三连环（M2/M1/M3）的第二环。**依赖 M2 已完成**（runtime 仓 commit `M2_DONE` 之后），runtime 的 `/template/apply` + `/credential/install` 两个 endpoint 已就位。M3 website 假设 M1 的 4 个 backend 改动已就位。

---

## 2. 必读输入（每轮迭代前重读）

| 路径 | 用途 |
|---|---|
| `docs/huanxing-hermes-runtime设计文档/04-三方API契约与并行开发边界.md` | backend ↔ runtime 调用契约 |
| `docs/huanxing-hermes-runtime设计文档/07-Agent模板应用到云端HermesAgent设计.md` §3 §4 §5 | template 应用编排责任划分 |
| `docs/huanxing-hermes-runtime设计文档/09-平台LLM计费与短期凭证.md` §4.1 §4.2 §6 | 凭证编排顺序 + API 形状 |
| `backend/app/hermes/service/hermes_agent_app_service.py` | `create_agent` (L278) / `delete_agent` (L463) 现有实现，本任务在此扩充 |
| `backend/app/hermes/service/hermes_runtime_client.py` | runtime client；本任务加 4 个方法 |
| `backend/app/llm/service/llm_newapi_user_mapping_service.py` | `ensure_agent_token` (L279) / `revoke_agent_token` (L361) 已实现，本任务直接调 |
| `backend/app/marketplace/crud/crud_marketplace_app.py` + `crud_marketplace_app_version.py` | 模板列表查询 |
| `backend/app/hermes/api/v1/app/agents.py` | 公开 endpoint；本任务加 `/templates` + 改 chat completions |
| `backend/app/hermes/api/router.py` | endpoint 注册位置 |
| `backend/sql/tables/marketplace_app.sql` + `marketplace_app_version.sql` | 表 schema 字段名 |
| `backend/代码生成使用说明.md` | codegen 使用（本任务可能不需要） |
| `.ralph/M1/NOTES.md` | 自己上一轮的进度笔记（首轮可空） |

**不要读**：webui / oauth2 / hasn / 其他无关模块的代码。

---

## 3. 入口快照（项目当前状态）

执行 M1 前的现状（baseline = backend main HEAD；假设 M2 已完成 runtime 端）：

- `backend/app/hermes/service/hermes_runtime_client.py` 已有 21 个方法（ensure_agent/soul/gateway/channel/chat 等），**缺**：`apply_template` / `get_template_status` / `install_credential` / `uninstall_credential`
- `hermes_agent_app_service.create_agent` (L278-407) 现状：
  - ✅ 创建本地 record（status='creating'）
  - ✅ 调 runtime.ensure_agent
  - ✅ 可选写 SOUL/USER（`payload.soul_content` 存在时调 put_soul）
  - ✅ 可选 start_gateway（`payload.start_gateway=true` 时）
  - ❌ **没调 ensure_agent_token**
  - ❌ **没调 install_credential**
  - ❌ **没调 apply_template**（payload.template 只是字符串存进 hermes_agent.template 字段，没真传给 runtime 渲染）
- `delete_agent` (L463-483) 现状：
  - ✅ 停 gateway
  - ✅ 标 deleted_time
  - ❌ **没调 uninstall_credential**
  - ❌ **没调 revoke_agent_token**
  - ❌ **没调 runtime.delete_agent**（profile 残留）
- `LlmNewapiUserMappingService.ensure_agent_token` 接口：`async def ensure_agent_token(*, db, user_id, agent_id, runtime_node_id, model_hint=None) -> dict` 返回 `{token_key, token_key_prefix, base_url, default_model, ...}`
- `revoke_agent_token`：`async def revoke_agent_token(*, db, agent_id, reason='user_request') -> bool`
- marketplace 表：`marketplace_app.app_type='agent_template'` 是 Agent 模板；`marketplace_app_version.is_latest=true` 标识当前最新版；含 `package_url` (CDN URL) 与 `file_hash` (SHA256)
- chat completions：`api/v1/app/agents.py:hermes_chat_completions` (具体行号 ralph 自查) 当前直接返 dict，未做 SSE 适配
- 测试：`tests/hermes/` 当前 0 个文件，0 个测试（B3-1/B3-2 测试在 `tests/llm/`）；本任务**强制**加测试到 `tests/hermes/`

- 当前 backend pytest baseline：ralph 自己跑一遍记下数字（`uv run pytest tests/ -q | tail -3` 或对应工具链命令）

---

## 4. owner 拍板的前置决策（不要再改）

1. **模板包传输 = backend 推送 url+hash 给 runtime**：M1 的 `apply_template` 调用前，backend 自行从 `marketplace_app + marketplace_app_version` 表查到 `package_url` + `file_hash`，作为参数传给 runtime；runtime 端不直接读 marketplace 表（决策来自 owner Q1）。
2. **templates list 数据源 = marketplace 表**：`GET /api/v1/hermes/app/templates` 直接 join 这两张表返回（决策来自 owner Q2）。**不**包装 marketplace client API（已存在）的逻辑——marketplace client API 是给桌面端，hermes templates 是给 website 选模板用的特定切面，可以共享 CRUD 但 endpoint 独立。
3. **不实现** `hermes_agent_template_application` 历史表（owner Q3）。
4. **不支持 BYOK**：`payload.llm_mode='byok'` 走 fast-fail：`raise errors.RequestError(msg='MVP 暂不支持 BYOK 模式，请使用 platform 模式')`。
5. **chat SSE = 透传 runtime SSE chunks**：runtime 的 chat_completions 已支持 stream=true（M2 不改）；本任务 backend SSE 改造把 chunks 透传给浏览器。runtime client 加 `chat_completions_stream(...)` async generator；endpoint 用 `StreamingResponse` 包装。
6. **runtime_node_id 取值**：从 `settings.HERMES_RUNTIME_NODE_ID`（已有 env）；若未设取 `'rn_default'`。

---

## 5. 必须落地的产物清单（acceptance criteria）

### 5.1 `HermesRuntimeClient` 扩展（4 个方法）

- [ ] `async def apply_template(self, runtime_profile_id: str, payload: dict, trace_id: str | None = None) -> dict`
  - POST `/runtime/v1/agents/{runtime_profile_id}/template/apply`
  - payload 形如 `{template_id, template_version, package_url, file_hash, render_context, soul_append?, user_append?}`
  - 5xx → `HermesRuntimeError`
- [ ] `async def get_template_status(self, runtime_profile_id, trace_id=None) -> dict`
  - GET `/runtime/v1/agents/{id}/template/status`
- [ ] `async def install_credential(self, runtime_profile_id, payload: dict, trace_id=None) -> dict`
  - POST `/runtime/v1/agents/{id}/credential/install`
  - payload `{token_key, base_url, default_model}`
- [ ] `async def uninstall_credential(self, runtime_profile_id, trace_id=None) -> dict`
  - DELETE `/runtime/v1/agents/{id}/credential`
- [ ] `async def chat_completions_stream(self, runtime_profile_id, payload: dict, trace_id=None) -> AsyncIterator[bytes]`
  - POST `/runtime/v1/agents/{id}/chat/completions` 带 `stream=true` 头
  - yield chunks（SSE 帧）；连接异常时 yield `event: error\ndata: {"error": "..."}\n\n` 然后结束

### 5.2 `HermesAgentAppService.create_agent` 编排重构

- [ ] 输入 payload 校验：若 `payload.llm_mode == 'byok'` → `raise RequestError(...)` 立即返回
- [ ] 流程改成（按顺序，每步失败统一处理回滚）：
  1. 校验 agent_name 唯一（已有，沿用）
  2. **resolve template**：从 marketplace 查 `(app_id=payload.template, app_type='agent_template')` → 拿 `template_version`(latest) + `package_url` + `file_hash`；查不到抛 `template_not_found`
  3. 创建本地 record（status='creating'）
  4. `runtime.ensure_agent(...)`（已有）
  5. `runtime.apply_template(profile_id, {template_id, template_version, package_url, file_hash, render_context: {agent_name, owner_user_id, owner_display_name, locale, timezone, now}, soul_append: payload.soul_content?, user_append: payload.user_content?})`
  6. `ensure_agent_token(db, user_id, agent_id, runtime_node_id=settings.HERMES_RUNTIME_NODE_ID, model_hint=llm_model)` → 拿 `{token_key, base_url, default_model}`
  7. `runtime.install_credential(profile_id, {token_key, base_url, default_model})`
  8. 若 `payload.start_gateway`（默认 True）→ `runtime.start_gateway(...)`
  9. 更新本地 record `status='ready'`，写 `template_version` 字段，commit
- [ ] 失败处理：
  - 任意步骤失败 → 标 `status='error'`、写 `last_error`，**尽力回滚**：try-catch 调 `revoke_agent_token` + `runtime.uninstall_credential` + `runtime.delete_agent`，无论失败都 swallow（避免回滚链放大异常）
  - 用 `_record_operation` 记审计日志（已有 helper）
- [ ] 返回的 `_agent_card` payload 含 `template_version` 字段

### 5.3 `HermesAgentAppService.delete_agent` 编排重构

- [ ] 流程：
  1. 校验 agent 归属当前 user
  2. `runtime.stop_gateway(...)` (swallow error，gateway 可能本来就停)
  3. `runtime.uninstall_credential(...)` (swallow error)
  4. `revoke_agent_token(db, agent_id, reason='agent_deleted')` (swallow error，但必 try)
  5. `runtime.delete_agent(...)` (swallow error)
  6. 本地 record `deleted_time=now`，commit
- [ ] `_record_operation` 记审计

### 5.4 templates list endpoint

- [ ] 新文件 `backend/app/hermes/api/v1/app/templates.py`：
  - `@router.get('/templates', summary='获取可选 Agent 模板列表')`
  - 不需 user 鉴权（公开）；或沿用 dashboard auth（看 `agents.py` 范式选）
  - 实现：JOIN `marketplace_app + marketplace_app_version` (app_type='agent_template' AND is_latest=true)，返回 `[{app_id, name, description, emoji, icon_url, version, package_url, file_hash, skill_dependencies}]`（package_url + file_hash 给 backend 内部用，**不暴露给 website**——response 只返 `[{app_id, name, description, emoji, icon_url, version}]`，敏感字段过滤掉）
  - 如果 marketplace 表里没有 7 个模板（hub 还没 publish），返回空数组而不是错误
- [ ] 注册到 `api/router.py` 在 `/api/v1/hermes/app/` 前缀下

### 5.5 chat completions SSE

- [ ] `agents.py:hermes_chat_completions` endpoint 改造：
  - 检查 payload `stream` 字段：若 `stream=true` 走 SSE 路径，否则保持原样（向后兼容）
  - SSE 路径：`return StreamingResponse(self._stream_chat(...), media_type='text/event-stream')`
  - `_stream_chat` 是 service 层 async generator，调 `runtime_client.chat_completions_stream(...)` 透传
  - 错误处理：异常时 yield `event: error\ndata: {"error_code": "..."}\n\n`

### 5.6 测试（≥ 10 个 case）

新建 `tests/hermes/test_agent_app_service.py`：

- [ ] **create_agent happy path**：mock runtime_client + ensure_agent_token；断言 6 步顺序被调用、final status='ready'
- [ ] **create_agent byok rejection**：payload.llm_mode='byok' → 400
- [ ] **create_agent template_not_found**：marketplace 无该模板 → 错误返回 + 本地 record 不创建
- [ ] **create_agent runtime ensure fails**：mock ensure_agent 抛错 → 本地 record status='error' + 不调后续步骤
- [ ] **create_agent install_credential fails 触发回滚**：mock install_credential 抛错 → revoke_agent_token + runtime.delete_agent 被调
- [ ] **delete_agent happy path**：mock 4 个调用都成功 → deleted_time 写入
- [ ] **delete_agent stop_gateway fails 不阻断**：mock stop_gateway 抛错 → 仍走 revoke + delete + mark deleted
- [ ] **templates list with seeded marketplace**：fixture 插 2 条 marketplace_app 数据 → endpoint 返回 2 条不含 package_url
- [ ] **templates list empty**：marketplace 空 → 返 []
- [ ] **chat SSE streaming**：mock runtime chat_completions_stream yield 3 chunks → endpoint 响应 content-type='text/event-stream' + body 是 3 chunks 拼接

如果 backend 用 unittest 而非 pytest，沿用 `tests/llm/` 的范式。

### 5.7 验收命令

```bash
cd hasn-cloud-backend
NO_PROXY=127.0.0.1,localhost,::1 uv run pytest tests/ -q   # 原数 + ≥10 新增，0 failed
git status                                                 # working tree 干净
```

---

## 6. Hard rules（违反等同验收失败）

1. **不引入新依赖**——backend 已有 fastapi / sqlalchemy / pydantic / httpx 等；够用。
2. **不动 `hasn-cloud-backend` 的 LLM 网关 / 用户中心 / 订阅 / oauth2 模块**——只动 `app/hermes/` + `app/llm/` 引用。
3. **不动 marketplace 的 schema / CRUD**——templates endpoint 只 read。
4. **不实现 `hermes_agent_template_application` 表**（owner Q3 决策）。
5. **跑 pytest 之前确认 NO_PROXY 包含 `127.0.0.1`**（A1 RETRO 教训跨仓适用）。
6. **token_key 不进 commit message / log / git tracked file**：测试 fixture 用 `"sk-hxTESTONLY..."`。
7. **每个原子改动一个 commit**：`feat(backend): add 4 runtime client methods (template + credential)` / `refactor(backend): wire create_agent through token + credential install + template apply` / `refactor(backend): wire delete_agent reverse cleanup` / `feat(backend): expose hermes templates list endpoint` / `feat(backend): chat completions SSE streaming` / `test(backend): hermes agent app service coverage`。
8. **不调 `--no-verify` / `--force`、不动 git 远程**。
9. **回滚链 swallow error**：失败回滚的 try-catch 里**只**记 `_record_operation`，绝不 reraise（避免主异常被回滚链异常掩盖）。
10. **chat SSE 必须真透传 chunks**，不准 buffer 全部内容再返回——这违背 SSE 流式语义。

---

## 7. 失败降级策略

- 若 marketplace 还没有 7 个 hub 模板 publish 进表，**create_agent 流程会失败**。降级：保留 fast-fail（`template_not_found`），让 owner 手动跑 `hasn-hub/scripts/publish_hub.py` 把模板 publish 进 marketplace 表后重试。NOTES 记下"依赖 hub publish 完成"。
- 若 `LlmNewapiUserMappingService.ensure_agent_token` 当前签名跟 §3 描述不符，照实际签名走，**不重构 service**；NOTES 记。
- 若 chat SSE 在 backend 异步 generator 嵌套有问题（fastapi `StreamingResponse` 是同步 iter），改用 `EventSourceResponse`（sse-starlette 包，**已在 backend deps 则可用，否则保持原样不流式**）；如不可用，回退 `stream=false` 一次性返回 + NOTES 记"SSE 推到下一阶段补"。
- 若回滚链中 `runtime.delete_agent` 在 profile 不存在时报 404 不 swallow，改成只 swallow 4xx + 5xx 全部，NOTES 记。
- 若现有测试基础设施需要 fixture (`db` / `client`) 没有，照 `tests/llm/test_llm_newapi_user_mapping_service.py` 范式抄一份。

---

## 8. NOTES.md 维护

`.ralph/M1/NOTES.md` 同前轮结构：

```
# M1 NOTES — last updated: <UTC ISO8601>

## Iteration <N>
- 完成：<本轮做了什么>
- 验收项进度：5.1 [x/5] / 5.2 [x/?] / 5.3 [x/?] / 5.4 [x/?] / 5.5 [x/?] / 5.6 [x/10] / 5.7 [x/2]
- 卡点：<本轮没解决的具体错误信息>
- 下轮第一件事：<具体到文件 + 函数>

## 已确认决策
- ...

## TODO 队列
1. ...
```

---

## 9. 完成判定

§5.1 / §5.2 / §5.3 / §5.4 / §5.5 / §5.6 / §5.7 全部 √，git working tree 干净，输出：

```
<promise>M1_DONE</promise>
```

---

## 10. 单轮迭代 SOP

每轮固定流程：

1. `git status` —— 起点干净
2. 读 `.ralph/M1/NOTES.md`
3. 读必读输入变化部分
4. 跑 `NO_PROXY=127.0.0.1,localhost,::1 uv run pytest tests/ -q` —— 看哪些验收项在炸
5. 选**一项** §5.x checklist 推进
6. 改文件 + 跑对应测试
7. `git add ... && git commit`（conventional commit）
8. 更新 NOTES.md
9. 检查 §9 → 满足则 emit `<promise>M1_DONE</promise>`，否则静默结束本轮
