# AI-Native App Platform - MVP 物理表（[P0]）

> 本目录是 [P0] MVP 阶段实际落库的最小数据模型。完整 19 张表的逻辑设计见
> [docs/HASN-centralized/HASN-Protocol/Architecture/AI-Native应用平台/11-数据模型.md](../../../../../../docs/HASN-centralized/HASN-Protocol/Architecture/AI-Native应用平台/11-数据模型.md)。

## 1. 文件清单

| 序号 | SQL 文件 | 表 | Phase | 职责 |
|---|---|---|---|---|
| 001 | `001_app_manifests.sql` | `app_manifests` | P0 | App 本体 + Manifest 全量 JSONB |
| 002 | `002_app_installations.sql` | `app_installations` | P0 | 安装记录 + 授权 scope |
| 003 | `003_app_data_records.sql` | `app_data_records` | P0 | 应用数据记录（JSONB） |
| 004 | `004_app_audit_logs.sql` | `app_audit_logs` | P0 | 操作审计日志（统一记录） |

## 2. 落地命令

按编号顺序执行（002 依赖 001 的外键，003 依赖 002）：

```bash
cd hasn-cloud-backend

uv run fba codegen generate --sql-file backend/sql/app_platform/mvp/001_app_manifests.sql      --app app_platform --execute
uv run fba codegen generate --sql-file backend/sql/app_platform/mvp/002_app_installations.sql  --app app_platform --execute
uv run fba codegen generate --sql-file backend/sql/app_platform/mvp/003_app_data_records.sql   --app app_platform --execute
uv run fba codegen generate --sql-file backend/sql/app_platform/mvp/004_app_audit_logs.sql     --app app_platform --execute
```

`fba codegen` 会同时：

- 在 `huanxing` 数据库执行 `CREATE TABLE`（`--execute`）
- 在 `backend/app/app_platform/{model,schema,crud,service,api}/` 生成对应代码
- 生成对应菜单 SQL 与字典 SQL

## 3. 设计要点

### 3.1 完整设计 + 分阶段落表

每张 [P0] 表都为未来 [P1]/[P2] 拆分预留了 placeholder 字段（如 `app_installations.listing_id` / `entitlement_id` 保留 nullable）。

[P0] 不落表的能力（Tool/Resource/Event 定义、scope 注册、版本历史等）通过 `app_manifests.manifest_jsonb` 内嵌承载，[P1]/[P2] 升阶时只做 `INSERT INTO new_table SELECT FROM jsonb_*`，不洗数据。

### 3.2 fba codegen 约定

- 时间字段使用 `created_time / updated_time`（不是 `created_at / updated_at`）
- 时间字段类型 `TIMESTAMP WITH TIME ZONE`
- 字典字段注释使用 `(value:label:color/...)` 格式（status/decision/risk_level 等）
- 主键、唯一索引、外键、`CHECK` 约束齐全

### 3.3 不外键约束的原则

- 审计表（`app_audit_logs`）不外键业务表，避免反向阻塞业务删除
- [P0] 表不外键 [P2] 表（如 `app_installations.listing_id` 不引用 `app_listings`），避免 [P0] 强依赖未实现的 [P2]

## 4. 历史草稿处理

`backend/sql/app_platform/` 上层目录下已有的下列文件是设计阶段产出的历史草稿，**不作为 MVP 落表依据**，应迁移到 `backend/sql/app_platform/_archive/` 或删除：

```
001_create_permission_tables.sql      [P1] 权限系统表的早期草稿
002_app_scopes.sql                    [P1] 同上
003_app_permission_grants.sql         [P1] 同上
004_app_dynamic_permission_requests.sql [P1] 同上
005_create_app_core_tables.sql        [P0] 早期版本（含语法错误，已被 mvp/001-004 取代）
006_app_data_records.sql              [P0] 早期版本（已被 mvp/003 取代）
006_app_manifests.sql                 空文件
006_create_app_data_records.sql       [P0] 重复（已被 mvp/003 取代）
007_app_permission_audit_logs.sql     [P1] 独立审计表的早期草稿
007_create_app_permission_audit_logs.sql 同上
999_rename_timestamp_columns.sql      迁移修正脚本
app_*.sql （单表草稿）                 各种早期单表 SQL，含 SQL 语法错误（如 TIMESTAMP WITH TIME ZONE 重复三次）
```

**已知 bug**：

- `app_installations.sql`（顶层）第 23 行 `TIMESTAMP WITH TIME ZONE WITH TIME ZONE WITH TIME ZONE` 重复三次，会导致 PostgreSQL 解析失败
- `006_app_manifests.sql` 是 0 字节空文件
- `006_app_data_records.sql` 与 `006_create_app_data_records.sql` 表定义重复

**操作建议**：

1. 在合并到 main 前，先 `git mv backend/sql/app_platform/{001..007,999,app_*}.sql backend/sql/app_platform/_archive/`
2. 之后所有新增/修改物理表的 SQL 都进入 `mvp/`（[P0]）或将来的 `phase1/`（[P1]）等子目录
3. `_archive/` 下的文件仅供溯源，不会被 fba codegen 扫描或执行

## 5. 升阶到 [P1] 的迁移路径

当 [P0] 上线稳定、需要升级到 [P1] 时：

1. 在 `backend/sql/app_platform/phase1/` 下新增表 SQL：`app_versions / app_tools / app_resources / app_events / app_event_deliveries / platform_scopes / app_scopes / app_permission_grants`
2. 写迁移 SQL `backend/sql/app_platform/migrations/YYYY-MM-DD-promote-to-p1.sql`：
   - `INSERT INTO app_versions(...) SELECT app_id, current_version, manifest_jsonb FROM app_manifests`
   - `INSERT INTO app_tools(...) SELECT app_id, t.* FROM app_manifests, jsonb_to_recordset(manifest_jsonb->'tools') AS t(...)`
   - `INSERT INTO app_permission_grants(...) SELECT installation_id, jsonb_array_elements_text(granted_scopes) FROM app_installations`
3. 补外键约束：`ALTER TABLE app_data_records ADD CONSTRAINT fk_... FOREIGN KEY (resource_id) REFERENCES app_resources(resource_id)`
4. 升级 `app_installations.status` `CHECK` 约束以纳入 `update_available / pending_reauth / suspended`

升阶过程中 [P0] manifest_jsonb 不删除字段，保持向后可读，直到 [P1] 双写期结束。
