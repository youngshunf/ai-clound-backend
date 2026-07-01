# backend/sql/hasn/_archive/ — hasn 模块历史迁移归档

早期 Flyway 风格（`V0xx__*__migration.sql` / `__rollback.sql`）的历史迁移，**早已在生产 / 各库应用**，且**不被**生产迁移 runner（`run_pending_migrations.sh` 只扫 `*/migrations/*.sql`）扫描。当时散落在 `backend/sql/hasn/` 模块根目录，按目录约定归档至此，避免与建表 bootstrap 混放。

- 保留仅供历史追溯；**不要**再执行（尤其 `__rollback.sql` 会回滚已生效的 schema）。
- 现行迁移一律走 `backend/sql/hasn/migrations/YYYY-MM-DD-描述.sql`（幂等 `ADD COLUMN IF NOT EXISTS` 等），由 runner 追踪执行。

归档清单：V001（s0/s1 assets）、V002（humans/agents 昵称头像对齐）、V003（drop legacy name/avatar_url + agent profile sync）、V020（messages process_blocks）、V021（agents profile 额外字段），各含对应 rollback。
