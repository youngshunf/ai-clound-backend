# CLAUDE.md

本仓库是唤星（HuanXing）云端后端，**独立 git 仓库**（父项目 `huanxing-project` 自 2026-04-20 起放弃 submodule，各子仓自管自 push）。完整项目上下文、技术栈、`fba codegen` 与后端开发规范见父项目 `huanxing-project/CLAUDE.md`；本仓贡献细则见 `CONTRIBUTING.md`。

- **仓性质**：**fork 仓**（基于上游 fork）。我们的**主分支是 `huanxing`**；`main` 是上游分支，**只用于跟随上游 sync，不要把我们的代码合进 `main`**。feature 合并与 `git push` 一律针对 `huanxing`。

## 响应格式硬规则（统一信封，违反会让 daemon 解析炸）

**正常业务接口一律用统一返回格式**：`response_model=ResponseModel`（含子类 `ResponseSchemaModel`）+ `return response_base.success(data=...)`，产出 `{code, msg, data}` 信封。**不许**裸 `return SomeSchema(...)`（`-> SomeSchema`）——FastAPI 会直接序列化成裸对象绕过信封，而 daemon transport `.send()` → `decode_ok_envelope` 依赖信封，裸返回会让 daemon 报 `error decoding response body`（2026-06-02 权限 tab `get_scope_catalog` 事故，commit `54da4c4`）。

- **裸返回仅限"统一信封根本满足不了"的接口**：OpenAI/Anthropic 兼容代理（外部 SDK 按原生形状解析）、文件/YAML/下载/导出、重定向、第三方 webhook（须回 provider 指定文本）。图省事不算理由。
- **守卫**：`backend/tests/test_response_envelope_contract.py` 内省全部路由，断言不许新增非信封业务接口（白名单 `KNOWN_NON_ENVELOPE` 分"真例外"和"已知欠债"两段）。新接口若不走信封又非真例外 → 测试红。
- **加端点要跑真实 HTTP**（打运行中 8020），不能只跑 service 层 E2E——后者绕过 HTTP，抓不到这类外壳漂移。

## 云端 MCP 平台工具入参禁止二进制 base64（铁律）

> 权威在父仓 `CLAUDE.md` →「Agent 工具入参禁止二进制 base64／字节块」。

**`backend/app/mcp/tools/*` 下任何 Agent 平台工具，input_schema 禁止出现 `content=<base64>` / `*_base64` / 字节块入参。** base64 逼分身把整段字节灌进上下文，上传几张图标就撑爆上下文、烧天量 token。二进制走「本地工具传 path（daemon 读盘上云）」或「`hasn://asset/{id}` 引用」。

- **正解范式**：`knowledge.upload_document`（`content_text` 或 `asset_uri`，非 base64）· `voice.transcribe`（`audio=hasn://asset/{id}`）。
- **反例（已删）**：`hasn.asset.create`（`content` base64/text）已整体删除，改由本地 `hasn.asset.upload(path)` 承接（daemon 侧 `AssetGateway` 落桶）。
- **文本创作类豁免**：分身自撰的文本产物（deck 的 `html`、artifact 的 `body`、`message.send` 的 `content`、designsystem 的 `content`）内容本身即交付物，可内联，不受此限。
- **CR 闸门**：新增/改动平台工具若要收文件/二进制，一律回「path 或 hasn://asset 引用」，评审见 `*_base64` 入参即拦。

## 多会话分支纪律（主仓恒在主分支，新建分支必走 worktree）

多会话 / 多 agent 会同时在同一个主 clone 上工作，**绝不**为了开发把主仓库 `git checkout` 到 feature 分支（会互相 reset/覆盖——曾发生 A 会话 merge、B 会话 `git reset` 撤销并清掉对方工作区改动，来回数轮差点丢工作）。

- **主仓库（主 clone）始终停在主分支 `huanxing`，不随意切换。**
- **小修复 / 小 UI 改 / 文档** → 直接在 `huanxing` 上做 → 跑最小校验 → 立即提交，不新建分支。
- **稍复杂的功能** → 从 `huanxing` `git worktree add ../<名> -b <分支>` 拉独立工作树开发，主仓库不动；完成后**回主 clone `huanxing` 合并、再从主 clone push**、删 worktree。
- **铁律：禁止在 worktree 里直接推送代码——所有代码必须先合并回主 clone，再从主 clone 推送**（福仔 2026-06-13）：❌ 禁止 worktree 里 `git push origin HEAD:huanxing`（只推动 origin/huanxing，主 clone 不前进）；本机/CI 从主 clone 编译运行，worktree 直推后主 clone 停在旧 commit → 重编"没变化"。✅ 正确：worktree 开发完 → `cd` 回主 clone（停 `huanxing`）→ `git fetch origin huanxing` →（落后则 merge 整合）→ `git merge <分支>` → 主 clone `git push origin huanxing` → 删 worktree。撞别会话脏文件则协调或外科 `git checkout <分支> -- <文件>`，**绝不**退回 worktree 直推。
- 一句话：**新建分支 = 必走 worktree**。提交用 `git commit -m "..." -- <你的文件>` 精确提交，发现别的会话的脏/staged 改动**不要碰**；push **一律在主 clone**，push 前先 `git fetch origin huanxing` 整合，**禁止 force-push**。
- **开工前必查在制分支（硬门·防压缩/唤醒后丢分支重开·2026-07-18）**：会话被压缩或钩子唤醒后，「我在哪条分支/worktree 干活」会随上下文摘要丢失，醒来的会话易**另起新分支重做**、把旧分支变成重复提交的孤儿（本仓 `wf-app-productization`/`wf-cloud-seed`/`wf-p4-cloud` 一堆孤儿即此根因，均为 workflow_template 功能被重复重做后遗弃）。三条防线：**① 登记**——开 worktree 即把 `分支名+worktree 路径` 写进施工 Task 描述或 `project_*.md` 记忆（持久态，压缩冲不掉）；**② 先核对**——动手前 `git worktree list` + 查登记，已有本轮分支就复用、严禁新建同类分支（SessionStart/PreCompact 全局钩子 `~/.claude/hooks/worktree-context.py` 会自动回显各仓在制 worktree 与搁浅分支）；**③ 确定性命名**——分支名从文档号/任务 id 派生（如 `wf-doc95-p9`），禁随机后缀，同名撞车即逼复用。权威详版在父仓 `CLAUDE.md`「多会话分支纪律」。

## 代码注释统一使用中文（铁律）

> 权威在父仓 `CLAUDE.md` 开发规范小节。

**我们新增 / 修改的 Python 代码，注释一律用中文**——行内注释、`# TODO`/`# FIXME` 说明、函数 / 类 docstring、SQL `COMMENT ON` 的说明性文字。团队以中文协作，中文注释才能让队友第一时间读懂意图。

- ✅ **正确**：`# 正常业务接口一律用统一信封，避免 daemon 解析炸`
- ❌ **错误**：`# business endpoints must return the unified envelope`
- **允许保留英文**：标识符、API 路径、错误码、URL、命令、包名、技术术语/缩写（`FastAPI`、`Pydantic`、`JWT`、`scope` 等）；只要求注释里的**说明性文字**用中文。
- **本仓是 fork 仓**：**不必**批量翻译上游既有英文注释（避免无谓 diff 与合并冲突），只约束我们新写 / 改动的代码。
