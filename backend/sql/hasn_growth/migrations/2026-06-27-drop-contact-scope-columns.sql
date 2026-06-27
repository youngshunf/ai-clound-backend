-- 统一线索池 slice3：删废弃 lead_scope 过渡列（2026-06-27 福仔「彻底式」统一线索池）
-- 承接 2026-06-27-unified-lead-pool.sql（slice2 已切 service 到 lead_ref 引用模型、已 DROP 旧 scope CHECK 约束）。
-- 统一池后 lead_scope（contact/collection_job/lead_export_batch 三表）不再决定线索归属——
-- 归属与用户级状态全部下沉 lead_ref，lead_scope 已是无值依赖死列，本迁移删除之。
--
-- 注：contact.user_id / collection_job.user_id 本迁移**不删**：
--   · collection_job.user_id 仍有意义（记「谁发起的采集」，run_job 据此为发起者建 lead_ref）；
--   · contact.user_id 尚被 codegen CRUD 面（/api/v1/growth/lead/contacts app/agent/open 的按行归属判定）引用，
--     该面与统一公共池语义冲突、是 /leads(funnel_service) 的重复脚手架，去留待单独决策后再 drop。
-- 全程幂等可重跑。PostgreSQL 语法，落 schema hasn_growth。

SET search_path TO hasn_growth, public;

ALTER TABLE contact DROP COLUMN IF EXISTS lead_scope;
ALTER TABLE collection_job DROP COLUMN IF EXISTS lead_scope;
ALTER TABLE export_batch DROP COLUMN IF EXISTS lead_scope;
