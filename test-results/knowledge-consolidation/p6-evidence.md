# 知识库实例/凭据收编迁移 — P6 真实验证证据

> 实施文档：`docs/hasn-node设计文档/14-AI-Native应用平台/实施/03-知识库实例与凭据收编迁移实施.md`
> 仓库：`hasn-cloud-backend`（fork，主分支 `huanxing`）
> worktree 分支：`feat/knowledge-instance-credential-consolidation`
> 日期：2026-06-06　数据库：本地 PostgreSQL 16 `huanxing@127.0.0.1:15432`

## 目标（用户硬要求）

> 「我不希望有两套存在」——彻底删除 `hasn_ragflow_instance` / `hasn_ragflow_credential`，
> 知识库（RAGFlow）实例/凭据并入统一应用平台底座，云端**物理上只剩**
> `hasn_app_instance` + `hasn_app_credential`。

## P2 数据迁移核对（迁移前 → 迁移后，本地 PG）

| 维度 | 旧表（迁移前） | 新表（app_id='knowledge'） |
|---|---|---|
| 实例 | 5 条（3 enterprise pending_config + 2 public active：id5=`117.72.92.229:9380`、id6=`127.0.0.1:18082`）| 4 条（3 enterprise + 1 public）|
| public 取舍 | 双 active public | 保留绑定 active 凭据最多者 → id6（本地）→ 新 id50；id5（0 凭据）丢弃，零凭据被孤立 |
| 凭据 | 2 条（user 4、user 1，均绑 instance6）| 2 条（均绑 app_instance_id=50）|
| 加密 | bytea（secret_crypto）| key_encryption 明文密文存 credential_ref（逐条解密对账一致）|
| RAGFlow 私有字段 | 实例列 public_pem/embd/llm；凭据列 ragflow_user_id/tenant | 下沉各自 `config` JSONB |

迁移脚本 `scripts/migrate_knowledge_to_app_instance.py`：幂等 + 默认 dry-run + `--commit`；
单事务 flush→逐条解密对账→commit/rollback；双 active public 护栏（保留+丢弃零静默）。

## P5 删表（DoD：只剩一套）

```
$ psql ... -f backend/sql/hasn/migrations/2026-06-06-drop-ragflow-instance-credential.sql
DROP TABLE
DROP TABLE
DROP_EXIT=0
$ ... "SELECT count(*) FROM information_schema.tables WHERE table_name IN
       ('hasn_ragflow_instance','hasn_ragflow_credential')"
0          ← 两旧表已物理删除
```

删表前已 `pg_dump` 两表数据留底：`test-results/ragflow-migration-backup/ragflow_tables_data_2026-06-06.sql`
（含加密凭据 blob，**不入 git**，仅本地回滚兜底）。

## P5 三道静态门

| 门 | 结果 |
|---|---|
| `grep -rn 'HasnRagflow\|hasn_ragflow_instance\|hasn_ragflow_credential' backend/app` | 仅命中 codegen 生成的 `hasn_app_credential` 文档注释（"泛化 hasn_ragflow_credential"），无代码引用 ✅ |
| `import backend.app.hasn.api.router` | OK，v1 188 路由（删 2 条 /ragflow/* 后）✅ |
| `pytest backend/tests/hasn`（475 passed）| 4 例失败均 **pre-existing**，已比对 pristine main clone 逐一一致（非回归）：`test_session_models::...import_and_construct`（HasnSessionArtifacts 名错，与本改无关）、`test_workbench_domain_service::test_create_enterprise_approves_owner...` + `...cloud_aggregated_workspace_stats`（community 自动安装 app_count 2≠1）、`test_workbench_generated_wrappers::...[hasn_agents]` ✅ |

## P6 真实 HTTP E2E（真实 PG，删旧表后，零 mock）

`backend/tests/hasn/test_knowledge_consolidation_http_e2e.py`（ASGITransport + 真实 PostgreSQL）：

```
6 passed in 2.99s
  ✓ test_get_credentials_resolves_public_instance_post_drop
      —— personal 工作空间 GET 凭据，resolver 经 hasn_app_instance 解析公共实例（删表后仍出实例）
  ✓ test_get_credentials_reads_seeded_credential_via_config_demotion
      —— 凭据经 hasn_app_credential 读回；ragflow_user_id/tenant 从 config 取；active 解密 api_key
  ✓ test_refresh_with_active_credential_skips_provision
      —— refresh 已有 active 凭据分支：跳过 provision 直接回读（不依赖真实 RAGFlow）
  ✓ test_save_and_get_enterprise_instance_via_config_demotion
      —— 企业实例写/读 hasn_app_instance(scope=enterprise)；public_pem/embd/llm 下沉 config；admin key 加密存
  ✓ test_owner_isolation_credentials       —— A 的凭据不串到 B
  ✓ test_enterprise_admin_gate_forbids_non_owner —— 非企业 owner/admin → 403（授权未削弱）
```

字段契约守恒：daemon `/knowledge/credentials*` 与 webui 企业实例配置响应字段名不变
（`instance_id`/`url`/`api_key`/`ragflow_user_id`/`admin_api_key_encrypted='stored'` 等），收编对调用方透明。

## infra-gated（未在本进程内 E2E）

真实 RAGFlow（:18082）provision/检索 + restricted grant + agent search 命中/denied 403 全链路，
依赖 RAGFlow:18082 + 隔离 daemon（`rf_full_stack_runner.py` / `rf_daemon_direct_e2e.py`）。
本次环境 RAGFlow:18082 与 cloud:8020 均未起（`curl` → 000），属 infra-gated；该链路的代码路径
（`refresh` active 分支、凭据解密下发、实例解析、授权）已由上面 6 项真实 PG E2E 覆盖到不依赖 RAGFlow 的部分。
数据面（检索/上传/agent search）走 hasn-node daemon 直连 RagFlow，本收编未改其契约（见 RF-CLOUD/RF-LIVE 既有证据）。

## 结论

- ✅ 旧两套表物理删除，云端只剩 `hasn_app_instance` + `hasn_app_credential`。
- ✅ 控制面读写经新表全绿（真实 PG HTTP E2E 6/6）。
- ✅ 对 daemon/webui 契约透明（字段名守恒）。
- ⏸ 真实 RAGFlow 全链路 infra-gated（需起 :18082 + 隔离 daemon，代码路径已就绪）。
