# app/hasn 平台核心 façade 与 god-file 拆分 — 设计提案

> 状态：**候选③ 已部分落地（P1 身份 façade + P2 应用平台服务 façade + P4 守卫），P3 长尾经评估暂不做；候选④ god-file 拆分 sync slice-1（`_sync_codec`）+ community slice-1（`_community_codec` 纯逻辑）+ community sub-service slice-A（管理端 `CommunityAdminService`）/slice-B（设置黑名单 `CommunitySettingsService`）已落地，sync DB god-class 与 community 余下读取簇 slice 暂缓**。福仔 redline：façade 落点=`app/hasn_core`、身份 JOIN=方案 A、身份先行、加 import-lint 守卫。
> 范围：仅 hasn-cloud-backend，纯**导入依赖收敛 + 文件内部分解**。**不动 DB schema、不动任何 URL、不动业务行为**。
> 关联：架构候选①（入参绑定接缝，已落地 `f763ce8e`）、候选⑥（死代码清理，已落地 `6bbbcbe1`）。
> 背景：`app/hasn` 是 39676 行 / 355 文件的遗留巨型模块，团队已陆续拆出 hasn_community / hasn_growth / hasn_task / hasn_deck / hasn_publish / hasn_knowledge / hasn_memory / billing / workbench 等独立模块（ADR-15）。但拆分**制造了边界、却没人定义边界的接口**——这正是本提案要补的两件事。

---

## ⛳ 落地状态（2026-06-16 执行回写）

| 阶段 | 状态 | commit | 说明 |
|---|---|---|---|
| **P1 身份 façade** | ✅ 已落地推送 | `ce836b37`(骨架+7单测) / `03f61e71`(41文件迁移) | 新建 `app/hasn_core`（落点按 redline）。`IdentityFacade`+`HumanRef`/`AgentRef` DTO（方案 A：re-export `HasnHumans`/`HasnAgents` 模型 + `hasn_humans_dao`/`hasn_agents_dao` 单例，零行为变化）。41 个兄弟文件身份 import 收敛到 `from backend.app.hasn_core import …`，身份反向 import 残留 **0**。 |
| **P2 应用平台 façade** | ✅ 已落地推送 | `60fc0ddf` | 新建 `app/hasn_core/app_platform.py`（**services-only 深接缝**）。12 个 consumer 迁移。**关键修正**：façade 只暴露服务 + 对外 schema，**不**暴露 `WorkbenchApp` 数据类 / `HasnAppInstance`·`HasnAiNativeAppAudit` 模型——它们被各应用 manifest 在 registry 构建期消费（manifest 属应用平台内部），façade 化会成环（实测 `hasn_deck/manifest` 触发 partially-initialized circular import）。这类叶子数据类型由定义处直接 import。 |
| **P4 守卫** | ✅ 已落地推送 | `391b0001` | `test_facade_reachback_guard`：扫描 app/ 非 hasn/非 hasn_core 模块，锁死 P1/P2 两条**已收编**接缝的回归。**有意只守这两条**，非「禁止一切 `from backend.app.hasn.`」。 |
| **P3 长尾白名单** | ⏸️ 评估后暂不做 | — | 见下「P3 重估」。实测非身份反向 import 远超设计估值（~80 行 vs 估 ~22），且**异质、属平台原语**（conversations/messages/contacts/sessions/nodes/ws_router/asset/sync/audit/notifications/approval/enterprise…），主要被 `mcp/*`、`notification/*` 消费。把它们塞进一张扁平白名单 = **浅接缝（pass-through index）**，deletion test 不过关，反把 `hasn_core` 变成 god re-exporter。 |
| **候选④ god-file 拆分** | 🟡 slice-1 已落地推送 | `1311e2d6` | **sync slice-1（纯编解码层）已落地**：`hasn_sync_service.py`(2209→1701) 抽出 34 个纯函数 + 5 个纯数据常量 + `TaskSyncConflictError` → `_sync_codec.py`(581)，外部方法签名/行为零变化（AST 逐字搬运），新增 33 个零夹具单测 `test_sync_codec.py`。验证：ruff F 全清（HEAD 42→41 零新增）、app import 1547 路由不变、`TaskSyncConflictError` 跨模块 identity 稳定。**剩余 slice**：DB god-class `SqlAlchemySyncGateway`(~1100) 与 `HasnSyncService` orchestrator 仍是巨石，但 DB 绑定、进一步拆分边际收益递减、风险升高，**暂缓**（最高价值的纯逻辑可测性已达成）。`community_service.py` 同法另起一轮（见下行）。 |
| **候选④ community slice-1** | 🟡 已落地推送 | `f9371cfc` | **community 纯逻辑 codec 已落地**：`community_service.py`(3428→3333) 把 `CommunityService` 的 4 个纯静态方法（引用卡片 `hasn://` URI 派生/规范化/按 viewer 呈现 + 可见性守卫）+ 顶层 `_safe_summary` + 4 个引用卡片常量 → `_community_codec.py`(133)，staticmethod→模块函数（AST 取段+dedent），~14 处 `CommunityService._X` 调用点改走模块函数，外部接口/行为零变化。新增 27 个零夹具单测 `test_community_codec.py`（含 `hasn://` 客户端无关铁律 + 作者专属跳转门控）。验证：ruff F 全清（HEAD 62→62 零新增）、app import 1547 路由不变。**后续**：纯逻辑抽尽后改走**按子域拆 sub-service**（见下两行 slice-A/B）。 |
| **候选④ community slice-A（sub-service 拆分）** | 🟡 已落地推送 | `fe76f341` | **god-class 拆 sub-service 第一刀=管理端查询**：把 `CommunityService` 的「§管理端（只读审核可见性）」5 方法（`admin_list_posts/articles/comments` + `admin_get_post/article`）verbatim 整段搬到独立 `admin_query_service.py::CommunityAdminService`(232 行)。子域 CLEAN（4 个零跨域子域之一）。**调用点直接重指、非浅 façade 委派**：`api/v1/admin/community.py` 唯一 import 改指 `community_admin_service`，5 处调用点重指；`CommunityService` 不再保留这 5 方法（deletion test 过关）。`community_service.py` 3333→3121。验证：ruff F/E/W/N 零新增、app import 1547 路由不变、路由名零重复、回归 42 项绿。 |
| **候选④ community slice-B（sub-service 拆分）** | 🟡 已落地推送 | `19ae803f` | **第二刀=个人社区设置+黑名单**：5 方法（`get/update_community_settings` + `list/add/remove_block`）+ `DEFAULT_COMMUNITY_SETTINGS` 常量 → 独立 `settings_service.py::CommunitySettingsService`(149 行)。子域 CLEAN，唯一内部边 `update→get_community_settings` 随块搬（重指类名）。**混合调用文件的外科重指**：`api/v1/app/community.py` 有 40 处 `community_service.*`，仅 5 处属本子域 → 不能整体替换，按方法名外科重指这 5 处到 `community_settings_service`、其余 35 处保留原 import（文件新增第二条 settings_service import）。`community_service.py` 3120→2997（`HasnCommunityBlocks` 随块移出后 F401 清理）。验证：ruff F/E/W/N 零新增（HEAD 43→43+0）、app import 1547 路由不变、路由名零重复、回归 28 项绿。**剩余子域**：collections（9 方法）/ interactions（评论/赞/关注 7 方法）两个 CLEAN 子域 + feed/post/article/profile 读取簇（共享 `_batch_reactions`/`_resolve_human_hasn_id` 等 helper，最后拆），可同法续做、亦可收在此（最高价值的管理/设置/纯逻辑已离巨石）。 |

### P3 重估（为何暂不做）

设计原估「76 处反向 import、~43 身份 + ~13 应用平台 + ~9 长尾」。**实测纠偏**：总反向 import ~123 行（设计漏算了 `mcp/*`、`notification/*`、`huanxing/*`、`hasn_creator/*`、`hasn_client/*` 等非「10 个抽出模块」的消费者）。P1 收掉 43（身份），P2 收掉 ~31（应用平台服务/schema），**仍余 ~80 行**长尾。

这 80 行的目标是**平台原语**（`ws_router`、`hasn_conversations_service`、`hasn_messages_service`、`hasn_contacts_service`、`hasn_asset_service`、`hasn_sync_service`、`hasn_audit_log_service`、`crud_hasn_agent_approval_requests`、`model.hasn_notifications`、`model.hasn_enterprise_membership` 等），它们**本就是平台核心**、被 `mcp/*` 与 `notification/*` 合法消费。给每个建 façade 收益甚微；统一塞进 `hasn_core/__init__` 扁平白名单则是浅接缝。**结论**：P1+P2 已收掉两条**真深接缝**（身份、应用平台服务），剩余长尾保持现状、由 P4 守卫**只守已收编的两条**、不扩面。若未来某平台原语子系统（如 messaging、sync）独立成深接缝再单独 façade 化。

---

## 候选③：平台核心 façade（收敛 76 处反向 import）

### 现状（实测，2026-06-16）

10 个已拆出的兄弟模块仍有 **76 行**直接 `from backend.app.hasn.<crud|model|service>.…` 反向伸进 `app/hasn` 的**具体内部符号**：

| 反向依赖目标 | 行数 | 类别 |
|---|---|---|
| `crud.crud_hasn_humans`（`hasn_humans_dao`） | 24 | **身份 DAO** |
| `model.hasn_humans` / `model import HasnHumans,HasnAgents` 聚合 | ~19 | **身份模型** |
| `service.workbench_app_registry`（`WorkbenchApp`） | 9 | 应用平台/工作台 |
| `service.{ai_native_app_registry, ai_native_runtime_gateway, app_catalog_service, workbench_domain_service, instance_resolver}` | 5 | 应用平台 |
| `service.hasn_sync_service` | 2 | 同步 |
| `service.hasn_asset_service` | 2 | 资产 |
| `model.hasn_notifications` | 2 | 通知 |
| `model.hasn_enterprise_membership` | 2 | 企业 |
| `service.resource_share_service` / `model HasnResourceShare` | 2 | 协作 ACL |
| `model.{hasn_app_instance, hasn_ai_native_app_audit}` / `service.hasn_agents_service` / `model.hasn_agents` | 4 | 长尾 |

按兄弟模块：community 25、growth 19、workbench 7、task 7、deck 5、publish 4、knowledge 4、marketplace 2、memory 2、billing 1。

**关键观察**：约 **43/76（57%）是身份**（humans/agents 的 DAO + 模型）。一个身份 façade 即可收掉一多半。

### 问题（接缝泄漏，locality 缺失）

- 兄弟模块直接抠 mega-module 的 `crud`/`model`/`service` 具体符号 → `app/hasn` 内部**任何重命名/重构都会炸 10 个模块**（候选④拆 god-file 时尤其危险）。
- 没有"平台核心对外提供什么"的清单 → 新模块（creator 等）接入时全靠 grep 别人怎么 import，依赖面无约束、只会越长越多。
- 应用 deletion test：把 `crud_hasn_humans` 删了，复杂度会在 24 个 callsite 重现 → 是真依赖、值得一个显式接缝。

### 设计：显式「平台核心」façade

新建 **`backend/app/platform/`**（平台核心对外契约层；名字待定，备选 `backend/app/core/`）。兄弟模块只依赖这里，不再 `from backend.app.hasn.…` 抠内部。

#### A. 身份 façade（核心，收 ~43）

```python
# backend/app/platform/identity.py
from dataclasses import dataclass

@dataclass(frozen=True)
class HumanRef:          # 轻量只读 DTO，不是 ORM
    hasn_id: str
    user_id: int
    nickname: str
    avatar_url: str | None
    # …按现有 callsite 实际读到的字段补全

@dataclass(frozen=True)
class AgentRef:
    hasn_id: str
    owner_hasn_id: str
    nickname: str
    profession: str | None
    avatar_url: str | None

class IdentityFacade:
    async def get_human(self, db, *, hasn_id: str) -> HumanRef | None: ...
    async def get_human_by_user_id(self, db, *, user_id: int) -> HumanRef | None: ...
    async def batch_humans(self, db, *, hasn_ids: list[str]) -> dict[str, HumanRef]: ...
    async def get_agent(self, db, *, hasn_id: str) -> AgentRef | None: ...
    async def batch_agents(self, db, *, hasn_ids: list[str]) -> dict[str, AgentRef]: ...

identity = IdentityFacade()   # 单例，内部仍调 hasn_humans_dao / HasnAgents（实现私有）
```

24 个 `hasn_humans_dao.get_*` 点查 → `identity.get_human(...)`。**实现仍复用 `app/hasn` 现有 crud**（façade 只是把它私有化在接缝后），零行为变化。

**关键设计张力（需福仔定调）**：~10 处不是点查，而是兄弟模块在**自己的 SQLAlchemy 查询里 JOIN `HasnHumans` 表**（如 community 取作者信息）。façade 返回 DTO 解决不了 JOIN。两个方案：

- **方案 A（推荐，低风险）**：把 `HasnHumans`/`HasnAgents` 这两张**身份模型**物理迁到 `backend/app/platform/identity/models.py`（或在该处 re-export），作为**受认可的共享只读契约**。JOIN 类 callsite 改 `from backend.app.platform.identity import HasnHumans`——依赖**显式且合法**（平台核心公开身份模型），而非"抠 mega-module 内脏"。点查走 façade。`app/hasn` 内部 re-export 兼容。
- **方案 B（更纯，高成本）**：身份所有访问（含 JOIN）都过 façade，提供"带作者信息的读模型/视图查询"方法，兄弟模块不碰身份表。需要为每种 JOIN 形态加 façade 方法，工作量大、且可能损失查询灵活性。

> 我倾向 **A**：把身份模型升格为平台公开契约（显式 > 偷抠），点查用 façade 收敛，JOIN 直接 import 公开模型。B 留作后续若要彻底零耦合再做。

#### B. 应用平台 façade（收 ~13）

`workbench_app_registry` / `ai_native_app_registry` / `app_catalog_service` / `workbench_domain_service` / `ai_native_runtime_gateway` / `instance_resolver` 已是 service 单例。新增 `backend/app/platform/app_platform.py` 作为**单一聚合导出点**（re-export 这些既有单例 + `WorkbenchApp` 类型），兄弟模块从这里 import。零实现迁移、纯 import 收口；好处是应用平台对外面只有**一个**门面，后续真要重构其内部不影响兄弟。

#### C. 长尾（asset 2 / notification 2 / sync 2 / resource_share 2 / enterprise 2）

数量都 ≤2，单独建 façade 不划算。统一在 `backend/app/platform/__init__.py` 做**白名单 re-export**（`from backend.app.platform import hasn_asset_service, HasnResourceShare, …`），把"允许跨模块共享的平台符号"集中登记成一张**显式清单**。新增共享需求 → 先进这张清单，而不是随手 `from backend.app.hasn.…`。

### 明确不做

- ❌ 不动任何 DB 表 / schema / 迁移。
- ❌ 不动任何 URL / 路由 / 信封。
- ❌ 不改业务逻辑——façade 实现就是转调现有 crud/service。
- ❌ 不强制兄弟模块"零依赖平台核心"——目标是**显式、收敛、可治理的依赖**，不是消灭依赖。

### 迁移计划（分批，每批一个 tested commit，worktree 内做完合并回主 clone 再推）

- **P1 身份 façade**（收 ~43，价值最大）：建 `platform/identity`，迁/re-export 身份模型（方案 A），24 个点查 callsite 改 façade，10 个 JOIN callsite 改 import 公开模型。`app/hasn` 加兼容 re-export。
- **P2 应用平台 façade**（收 ~13）：建 `platform/app_platform` 聚合导出，13 个 callsite 重指。
- **P3 长尾白名单**（收 ~9）：`platform/__init__` 登记清单，剩余 callsite 重指。
- **P4 治理**：加一条轻量 import-lint（禁止兄弟模块新增 `from backend.app.hasn.` 直抠，CI 守卫），并写进 CLAUDE.md。

### 风险与回滚

- 风险：**多会话并发**正在编辑 community/growth（最热）。→ 对策：P1 在 worktree 内一次做完、合并前 `git fetch` 整合；每个 callsite 改动是机械 import 替换，冲突易解。
- 回滚：纯 import 收敛 + re-export 兼容，任何一批可单独 revert，不留 schema 残留。
- 测试：façade 加纯函数/真 DB 单测（点查、批量、缺失返 None）；每批跑 `app import` 冒烟 + 受影响模块的既有测试，断言路由数/行为不变。

---

## 候选④：god-file 内部拆分（不改外部接口）

### 现状（实测最大文件）

| 文件 | 行数 | 职责（揉在一起） |
|---|---|---|
| `hasn_community/service/community_service.py` | 3429 | 社区域 + 可见性 + 关系 + 内容 CRUD |
| `hasn/service/hasn_sync_service.py` | 2209 | 跨设备同步状态机 + 去重 + 事件发布 + presence + workspace 态 |
| `hasn/service/workbench_domain_service.py` | 1244 | 工作台目录 + 应用 CRUD + entitlement |
| `hasn/service/message_router.py` | 1119 | 消息路由分发 |
| `hasn/service/hasn_agents_service.py` | 1117 | 分身生命周期 |
| `hasn/service/hasn_message_hub_service.py` | 1022 | 消息入站去重投递 |

这些**按杠杆算是深的**（小接口背后很多行为），但**实现是无内部接缝的巨石**：难单测、难导航、改一处怕碰全部。候选④ = **在同一外部接口下，把内部切成可独立测试的单元**（不是再拆模块、不动外部调用方）。

### 设计

以 `hasn_sync_service.py`（2209，最高优先：4 个模块反向依赖它、且最难推理）为样板：

- 保持 `hasn_sync_service` 单例**对外方法签名不变**（所有调用方零改）。
- 内部切出（同目录子模块或类）：
  - `sync_state_machine`：事件状态收敛/冲突解决（**纯逻辑，可单测**）。
  - `sync_dedup`：去重。
  - `sync_event_publisher`：出站事件/WS 发布。
  - `sync_presence`：presence/workspace 态。
- `hasn_sync_service` 变成**薄编排**，转调上述单元。

`community_service.py`（3429）同法：可见性判定（纯函数）/ 关系图 / 内容 CRUD 切开。

### 收益

- 测试面从"整条 sync 链路（需真 DB+Redis）"降到"状态机纯函数单测"。
- 改动定位精确；新人/AI 读单块即懂。
- locality 不变（仍在同模块），但每块可推理。

### 迁移计划

- **先做 sync**（风险最高、依赖最多）：仅当候选③ P1 身份 façade 落地后做更稳（届时 sync 内部重构不会牵动外部身份依赖）。
- 一次拆一个 god-file，每个拆分是"提取内部单元 + 原文件转调 + 单元加单测"，外部接口与测试断言守住即绿。
- community_service 与候选③ P1 同属 community 热区，**错峰**做，避免并发冲突。

---

## 执行方式与待决策点

**执行方式**：全程 worktree（off `huanxing`），分批每批 tested commit，合并回主 clone 再从主 clone 推送（遵 CLAUDE.md 铁律，禁 worktree 直推、禁 force-push）。

**待福仔 redline**：
1. **façade 落点命名**：`backend/app/platform/` vs `backend/app/core/` vs 其它？
2. **身份 JOIN 方案 A vs B**（升格身份模型为平台公开契约 / 还是一切过 façade）？我推荐 A。
3. **批次顺序与启动时机**：是否 P1 身份 façde 先行、确认稳了再碰 god-file（sync）？还是别的优先级？
4. **是否要 P4 import-lint 守卫**（CI 禁新增 `from backend.app.hasn.` 直抠）？

确认后我即在 worktree 开 P1。
