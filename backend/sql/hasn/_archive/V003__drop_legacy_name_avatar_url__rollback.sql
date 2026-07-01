-- =====================================================
-- V003 rollback: 重建被 drop 的列；数据无法恢复（drop 已不可逆）
-- 仅供生产环境紧急回滚 schema 结构使用。
-- =====================================================

BEGIN;

ALTER TABLE "public"."hasn_humans"
  ADD COLUMN IF NOT EXISTS "name" varchar(50),
  ADD COLUMN IF NOT EXISTS "avatar_url" varchar(500),
  ALTER COLUMN "nickname" DROP NOT NULL;

UPDATE "public"."hasn_humans"
SET "name" = "nickname", "avatar_url" = "avatar"
WHERE "name" IS NULL OR "avatar_url" IS NULL;

ALTER TABLE "public"."hasn_humans"
  ALTER COLUMN "name" SET NOT NULL;

ALTER TABLE "public"."hasn_agents"
  ADD COLUMN IF NOT EXISTS "name" varchar(50),
  ADD COLUMN IF NOT EXISTS "avatar_url" varchar(500),
  ALTER COLUMN "display_name" DROP NOT NULL;

UPDATE "public"."hasn_agents"
SET "name" = "display_name", "avatar_url" = "avatar"
WHERE "name" IS NULL OR "avatar_url" IS NULL;

ALTER TABLE "public"."hasn_agents"
  ALTER COLUMN "name" SET NOT NULL;

COMMIT;
