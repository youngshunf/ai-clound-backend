# 图坊模型目录发布面加固（IMG4-H）· 总览

> 本目录管理「图坊模型签名目录两阶段发布端点」上线后一轮 xhigh 代码评审的收口施工。
> 施工细则见 [01-施工清单与验收.md](./01-施工清单与验收.md)。

## 1. 背景

commit `e33d8195`（merge，含 `d374ae38` + `8d16dd00`）新增了图坊模型签名目录的两阶段发布面：

| 端点 | 作用 |
|---|---|
| `POST /api/v1/hasn/app-catalogs/{pk}/model-package-stage` | 流式上传模型 zip 到公共桶，返回进入签名正文的云端权威字段（key/url/sha256/compressed_size），**不切换在线目录** |
| `POST /api/v1/hasn/app-catalogs/{pk}/model-catalog` | 接收离线签名的 schema v1 目录，校验后原子写入 `config_json.models.signed_catalog` 并 `sync_bump('platform_config')` |

改动落点 4 个文件、574 行新增：

- `backend/app/hasn/api/v1/admin/hasn_app_catalog.py`（+52）
- `backend/app/hasn/service/app_catalog_service.py`（+304）
- `backend/plugin/s3/service/storage_service.py`（+28，新增 `get_public_package_storage` 脱离会话快照）
- `backend/tests/hasn/test_imagelab_signed_model_catalog.py`（+190）

评审以 6 个 finder 并行扫描、34 个独立 verifier 逐条对抗核实，60 条候选中 53 条留存、7 条被驳回，去重后收敛为 15 项。

## 2. 关键前提：签名工具是第一道闸，云端不是唯一防线

排优先级前必须先认清这条链路事实，否则会把一批「纵深不对称」误当成「生产随时会炸」：

```text
package-imagelab-model.sh          # license/size/sha256 取引擎 KNOWN_MODELS 权威声明，对不上即 SystemExit
        ↓
imagelab_manifest_tool sign-models # 写出签名文件前，用 daemon 同款验证器自检
        ↓                          #   apps/daemon/src/bin/imagelab_manifest_tool.rs:148
        ↓                          #   verify_models_with_public_key → ImageLabModelCatalogVerifier::verify
        ↓                          #     → validate_catalog_payload → validate_model → validate_package
云端 POST /model-catalog           # 结构校验（本次评审对象）
        ↓
daemon ImageLabModelCatalogVerifier # 内置 Ed25519 信任根验签 + 同一套 validate_*
```

**签名工具在产出签名文件前跑的就是 daemon 的那一个验证器**（`model_artifact.rs` 的
`validate_model`/`validate_package`：SPDX license、`key.contains("..")`、4 GiB 上限、
`PackageSourceRef::parse` 要求 https 或 loopback、`is_safe_token` 排除 `.`/`..`）。
所以经正规工具链产出的目录，不可能带着这些非法值走到云端。

由此得到本轮的严重度分层——**这是排期依据**：

| 层 | 判据 | 含义 |
|---|---|---|
| **A 类** | 云端是唯一防线，签名工具与 daemon 都覆盖不到 | 真实缺陷，必修 |
| **B 类** | 签名工具已挡一道，云端只是纵深不对称 | 补齐即可，一次性批量处理 |
| **C 类** | 健壮性 / 可观测性缺口 | 顺手做掉 |

A 类之所以存在，是因为有三类东西天然在签名工具的覆盖之外：

1. **stage 阶段发生在签名之前**——`runtime_name`/`version`/`pk` 是裸表单字段，没有任何工具校验过。
2. **跨发布不变量是单文档验证器看不到的**——签名工具只验一份文档自洽，
   序列单调、同版本异摘要、重放拒绝**只有云端能做**，daemon 也做不了。
3. **`PUT /{pk}/config` 是一条完全绕开校验的写路径**——同权限、整块覆盖 `config_json`。

## 3. 三条主线与 15 项分布

**主线一 · 内存与资源边界失守（A 类）**
新端点唯一的设计卖点是「流式、有界内存、GB 级模型不占内存」，但全局
`OperaLogMiddleware` 在路由执行前就把整个 multipart body 缓冲成 bytes 再 decode 成等长 str，
该目标在本应用的中间件栈下根本不成立；`/{pk}/model-catalog` 又是先 `read()` 全量再判 8 MiB。

**主线二 · 发布不变量与归属守卫可绕过（A 类）**
同版本异摘要守卫按可变的 `dependency_id` 配对而非制品身份；stage/publish 都缺 `app_id` 归属校验
（孪生的 finance 引擎面有）；重放守卫只跟到 daemon 两条读路径中的一条；
`PUT /{pk}/config` 可静默抹掉 `signed_catalog`。

**主线三 · 云端校验相对 daemon 系统性放宽（B 类）**
明文 http URL、key 缺 `..`/空白拦截、token 允许 `.`/`..`、尺寸无上界、
`schema_version != 1` 放行 `true`/`1.0`、`display_name` 按字符计而 daemon 按字节计、
`filename` 正则可达 133 字符。逐项都是「引擎孪生实现有、模型侧移植时丢了」。

分布：A 类 9 项、B 类 4 项（另含 2 项评审摘要提及但未进前 15 的漂移）、C 类 2 项。

## 4. 施工编排

四个批次，每批一次提交（遵循父仓 CLAUDE.md「小步提交」）：

| 批次 | 主题 | 条目 | 类 |
|---|---|---|---|
| **B1** | 内存与资源边界 | H-01 H-02 H-03 | A |
| **B2** | 发布不变量与归属守卫 | H-04 H-05 H-06 H-07 H-08 | A |
| **B3** | 校验对齐（含纵深补齐） | H-09 ~ H-13 | B |
| **B4** | 健壮性与可观测性 | H-14 H-15 | C |

分支与 worktree（复杂功能 → 必走 worktree，分支名从文档号确定性派生）：

```bash
# 主 clone 停在 huanxing 不动
cd /Users/mac/openclaw-workspace/huanxing/huanxing-project/hasn-cloud-backend
git worktree add .worktrees/img4-h-publish-hardening -b fix/img4-h-model-publish-hardening
```

- 分支：`fix/img4-h-model-publish-hardening`
- worktree：`hasn-cloud-backend/.worktrees/img4-h-publish-hardening`
- 目标仓 / 主分支：`hasn-cloud-backend` / `huanxing`
- **禁止从 worktree 直推**：完成后回主 clone `huanxing` → `git fetch origin huanxing` → merge → 主 clone push。

## 5. 被驳回与舍去的条目（不施工，留档备查）

7 条经核实驳回，主要两类：

| 条目 | 驳回理由 |
|---|---|
| 模型 stage 丢了客户端 sha256 交叉校验（3 条重复） | `release.sha256` 是运维本地对 .onnx 算的、独立于服务端；daemon 安装时 `verify_expanded` 逐文件比对摘要，并拒绝夹带/缺失文件。传中损坏必被拦，摘要链不会自洽。 |
| license 未按 SPDX 校验（2 条重复） | license 由 `package-imagelab-model.sh` 从引擎 `KNOWN_MODELS` 取（现值 `Apache-2.0`/`MIT`），非法 SPDX 在 `sign-models` 自检阶段即失败，到不了云端。**但仍作为 B 类纳入 H-13 补齐纵深。** |
| 只有 service 层单测、违反「加端点要跑真实 HTTP」 | 本仓 CLAUDE.md 该条确实存在，但已有 `test_response_envelope_contract.py` 全路由内省守卫覆盖外壳漂移；**仍作为 H-15 的验收项要求补真实 HTTP 冒烟。** |
| 模型包借用 `category='film_engine'` 上传 | 该类别策略即「公共读 + https CDN」，语义匹配；仅注释措辞未更新，归入 B4 顺手改。 |

规范类问题按阈值舍去，不单列条目，但纳入 B4 收尾：新增代码引入 6 条 ruff 错误
（`ANN001`×3、`ANN202`×1、`C901`×2），以及 engine/model 两套校验器大面积复制粘贴。

## 6. 事实源

- 图坊 v4 收口方案：`docs/hasn-node设计文档/14-AI-Native应用平台/30-图坊/实施/01-v4-完整可用图坊应用收口施工方案.md`（父仓）
- daemon 校验器：`hasn-node/apps/daemon/src/domains/imagelab/model_artifact.rs`
- daemon 目录读路径：`hasn-node/apps/daemon/src/domains/imagelab/broker.rs:845`
- 签名工具：`hasn-node/apps/daemon/src/bin/imagelab_manifest_tool.rs`
- URL 策略：`hasn-node/crates/hasn-local-runtime-artifact/src/contract.rs:44-75`
- 响应信封硬规则与分支纪律：本仓 `CLAUDE.md`
