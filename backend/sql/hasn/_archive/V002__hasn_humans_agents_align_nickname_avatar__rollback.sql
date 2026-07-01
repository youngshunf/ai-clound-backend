-- =====================================================
-- V002 rollback: 仅删除新增的 nickname/avatar 列。
-- 不应在 V003 已执行（旧列被删除）后再回滚 V002。
-- =====================================================

BEGIN;

ALTER TABLE "public"."hasn_humans"
  DROP COLUMN IF EXISTS "nickname",
  DROP COLUMN IF EXISTS "avatar";

ALTER TABLE "public"."hasn_agents"
  DROP COLUMN IF EXISTS "avatar";
-- 注意：display_name 由 V001 引入，rollback 不在此处理。

COMMIT;
