-- =====================================================
-- V003__drop_legacy_name_avatar_url__migration.sql
-- 阶段 3（删旧）：删除 V002 引入双写期保留的旧列
--   hasn_humans: drop name, drop avatar_url
--   hasn_agents: drop name, drop avatar_url；display_name SET NOT NULL
-- 前置条件：所有 reader/writer 已切到 nickname/avatar/display_name；
-- 所有 client 已强制最低版本（旧版本读 name/avatar_url 会得到 NULL）。
-- =====================================================

BEGIN;

-- 1) hasn_humans: 强制 nickname 非空（V002 已回填，保险起见再次回填）
UPDATE "public"."hasn_humans"
SET "nickname" = COALESCE(NULLIF("nickname", ''), NULLIF("name", ''))
WHERE "nickname" IS NULL OR "nickname" = '';

ALTER TABLE "public"."hasn_humans"
  ALTER COLUMN "nickname" SET NOT NULL;

ALTER TABLE "public"."hasn_humans"
  DROP COLUMN "name",
  DROP COLUMN "avatar_url";

-- 2) hasn_agents: 强制 display_name 非空（V001 已回填，保险）
UPDATE "public"."hasn_agents"
SET "display_name" = COALESCE(NULLIF("display_name", ''), NULLIF("name", ''))
WHERE "display_name" IS NULL OR "display_name" = '';

ALTER TABLE "public"."hasn_agents"
  ALTER COLUMN "display_name" SET NOT NULL;

ALTER TABLE "public"."hasn_agents"
  DROP COLUMN "name",
  DROP COLUMN "avatar_url";

COMMIT;
