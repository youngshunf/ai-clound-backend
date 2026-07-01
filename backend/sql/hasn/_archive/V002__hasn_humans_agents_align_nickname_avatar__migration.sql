-- =====================================================
-- V002__hasn_humans_agents_align_nickname_avatar__migration.sql
-- 字段命名对齐 sys_user：
--   hasn_humans: name -> nickname, avatar_url -> avatar
--   hasn_agents: name -> display_name(唯一显示名), avatar_url -> avatar
-- 阶段 1（双写）：仅 ADD COLUMN + 回填，不删旧列、不动 NOT NULL。
-- 旧列删除与 NOT NULL 调整在 V003 完成。
-- =====================================================

BEGIN;

-- 1) hasn_humans: 加 nickname / avatar 双写列
ALTER TABLE "public"."hasn_humans"
  ADD COLUMN IF NOT EXISTS "nickname" varchar(50),
  ADD COLUMN IF NOT EXISTS "avatar" varchar(500);

UPDATE "public"."hasn_humans"
SET
  "nickname" = COALESCE(NULLIF("nickname", ''), NULLIF("name", '')),
  "avatar" = COALESCE("avatar", "avatar_url")
WHERE "nickname" IS NULL OR "avatar" IS NULL;

COMMENT ON COLUMN "public"."hasn_humans"."nickname" IS '昵称（与 sys_user.nickname 对齐；阶段 3 后取代 name）';
COMMENT ON COLUMN "public"."hasn_humans"."avatar" IS '头像（与 sys_user.avatar 对齐；阶段 3 后取代 avatar_url）';

-- 2) hasn_agents: 加 avatar 双写列；display_name 已在 V001 加好并回填
ALTER TABLE "public"."hasn_agents"
  ADD COLUMN IF NOT EXISTS "avatar" varchar(500);

UPDATE "public"."hasn_agents"
SET
  "avatar" = COALESCE("avatar", "avatar_url"),
  "display_name" = COALESCE(NULLIF("display_name", ''), NULLIF("name", ''))
WHERE "avatar" IS NULL OR "display_name" IS NULL OR "display_name" = '';

COMMENT ON COLUMN "public"."hasn_agents"."avatar" IS '头像（与 sys_user.avatar 对齐；阶段 3 后取代 avatar_url）';
COMMENT ON COLUMN "public"."hasn_agents"."display_name" IS 'Agent 显示名（支持中文；阶段 3 升为 NOT NULL 并取代 name）';

-- 3) NOT NULL 约束在 V003（drop 阶段）一并处理
-- Validation queries (post-deploy 检查):
--   SELECT count(*) FROM hasn_humans WHERE nickname IS NULL OR (avatar IS NULL AND avatar_url IS NOT NULL);
--   SELECT count(*) FROM hasn_agents WHERE display_name IS NULL OR (avatar IS NULL AND avatar_url IS NOT NULL);

COMMIT;
