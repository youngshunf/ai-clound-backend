# backend/sql/_archive/ — 过时 SQL 归档

按 CLAUDE.md「SQL 脚本目录约定」整理时归档的过时脚本。**这些文件不再被任何代码 / 脚本 / 生产迁移 runner 引用**（归档前逐个核实过 0 路径引用），保留仅供历史追溯，**不要**再执行或作为事实源。

| 文件 | 过时原因 |
|---|---|
| `001_llm_menu_20251227.sql` | `app/llm` 网关已删（NEWAPI-P6），此 LLM 菜单 seed 失效 |
| `codegen_test_task.sql` | fba codegen 工具的一次性测试建表残留 |
| `huanxing_document_tables.sql` | 文档表已由 `app/hasn_knowledge`（document/document_version 模型）取代 |
| `init_subscription_data.sql` / `subscription_credit_system.sql` / `pay_module_v1.sql` | 旧订阅 / 积分 / 支付系统，已由 `app/billing`（hasn_billing schema）+ `app/newapi` 取代 |
| `projects.sql` / `project_topics.sql` | `app/projects` 模块已删、表已 DROP（CLEAN-2） |
| `marketplace_skill.sql` / `marketplace_skill_version.sql` / `marketplace_sync_log.sql` | 旧散落副本，已被 `backend/sql/marketplace/tables/` 下的规范版本取代 |
| `backup_hasn_20260327_174621.dump` | 2026-03-27 的一次性 PG 备份快照，不该随代码追踪（生产可重新导出） |
| `mysql/` | 本项目 PostgreSQL-only，MySQL 变体的 fba 测试数据脚本从不使用（PG 变体在 `backend/sql/postgresql/`） |

> 约定（见父仓 CLAUDE.md「后端开发流程」）：建表 bootstrap → `backend/sql/<模块>/xxx.sql` 或 `backend/sql/<模块>/tables/`；字段变更迁移 → `backend/sql/<模块>/migrations/YYYY-MM-DD-*.sql`（生产 runner 只扫 `*/migrations/*.sql`）；codegen 菜单/字典 → `backend/sql/generated/`。**禁止再往 `backend/sql/` 根目录散落脚本。**
