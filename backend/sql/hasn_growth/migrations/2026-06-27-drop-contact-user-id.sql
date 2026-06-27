-- 统一线索池 slice3b：删 contact.user_id（2026-06-27 福仔决策「删 app/agent/open 三 scope，再 drop contact.user_id」）
-- 承接 2026-06-27-drop-contact-scope-columns.sql。统一公共池下 contact 行无单一归属——
-- 用户拥有=lead_ref 引用，contact.user_id 已无引用（report 改 lead_ref、重复的 codegen CRUD
-- app/agent/open lead_contact 面已删，仅保留 admin CRUD 且不依赖 user_id）。
-- 保留 collection_job.user_id（采集发起者）与 export_batch.user_id（导出者）。
-- 全程幂等可重跑。PostgreSQL 语法，落 schema hasn_growth。

SET search_path TO hasn_growth, public;

ALTER TABLE contact DROP COLUMN IF EXISTS user_id;
