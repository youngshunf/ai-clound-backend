-- ============================================
-- 技能市场用户归属与发布状态字段
-- 日期：2026-05-28
-- ============================================

BEGIN;

ALTER TABLE "public"."marketplace_skill"
  ALTER COLUMN "skill_id" TYPE varchar(255),
  ALTER COLUMN "namespace" TYPE varchar(160),
  ADD COLUMN IF NOT EXISTS "user_id" int8,
  ADD COLUMN IF NOT EXISTS "hasn_id" varchar(40) COLLATE "pg_catalog"."default",
  ADD COLUMN IF NOT EXISTS "status" varchar(20) COLLATE "pg_catalog"."default",
  ADD COLUMN IF NOT EXISTS "visibility" varchar(20) COLLATE "pg_catalog"."default",
  ADD COLUMN IF NOT EXISTS "reviewed_by" int8,
  ADD COLUMN IF NOT EXISTS "reviewed_at" timestamptz(6),
  ADD COLUMN IF NOT EXISTS "review_note" text COLLATE "pg_catalog"."default",
  ADD COLUMN IF NOT EXISTS "published_at" timestamptz(6),
  ADD COLUMN IF NOT EXISTS "suspended_at" timestamptz(6),
  ADD COLUMN IF NOT EXISTS "suspend_reason" text COLLATE "pg_catalog"."default",
  ADD COLUMN IF NOT EXISTS "star_count" int4 NOT NULL DEFAULT 0,
  ADD COLUMN IF NOT EXISTS "repo_path" varchar(500) COLLATE "pg_catalog"."default",
  ADD COLUMN IF NOT EXISTS "git_commit_hash" varchar(64) COLLATE "pg_catalog"."default",
  ADD COLUMN IF NOT EXISTS "synced_at" timestamptz(6),
  ADD COLUMN IF NOT EXISTS "translated_at" timestamptz(6);

ALTER TABLE "public"."marketplace_template"
  ALTER COLUMN "template_id" TYPE varchar(255),
  ALTER COLUMN "namespace" TYPE varchar(160),
  ADD COLUMN IF NOT EXISTS "user_id" int8,
  ADD COLUMN IF NOT EXISTS "hasn_id" varchar(40) COLLATE "pg_catalog"."default",
  ADD COLUMN IF NOT EXISTS "status" varchar(20) COLLATE "pg_catalog"."default",
  ADD COLUMN IF NOT EXISTS "visibility" varchar(20) COLLATE "pg_catalog"."default",
  ADD COLUMN IF NOT EXISTS "reviewed_by" int8,
  ADD COLUMN IF NOT EXISTS "reviewed_at" timestamptz(6),
  ADD COLUMN IF NOT EXISTS "review_note" text COLLATE "pg_catalog"."default",
  ADD COLUMN IF NOT EXISTS "published_at" timestamptz(6),
  ADD COLUMN IF NOT EXISTS "suspended_at" timestamptz(6),
  ADD COLUMN IF NOT EXISTS "suspend_reason" text COLLATE "pg_catalog"."default",
  ADD COLUMN IF NOT EXISTS "repo_path" varchar(500) COLLATE "pg_catalog"."default",
  ADD COLUMN IF NOT EXISTS "git_commit_hash" varchar(64) COLLATE "pg_catalog"."default",
  ADD COLUMN IF NOT EXISTS "synced_at" timestamptz(6),
  ADD COLUMN IF NOT EXISTS "translated_at" timestamptz(6);

ALTER TABLE "public"."marketplace_skill_version"
  ALTER COLUMN "skill_id" TYPE varchar(255);

ALTER TABLE "public"."marketplace_template_version"
  ALTER COLUMN "template_id" TYPE varchar(255);

ALTER TABLE "public"."marketplace_download"
  ALTER COLUMN "resource_id" TYPE varchar(255);

UPDATE "public"."marketplace_skill"
SET
  "status" = CASE WHEN COALESCE("is_private", false) = false THEN 'published' ELSE 'draft' END,
  "visibility" = CASE WHEN COALESCE("is_private", false) = false THEN 'public' ELSE 'private' END
WHERE "status" IS NULL OR "visibility" IS NULL;

UPDATE "public"."marketplace_template"
SET
  "status" = CASE WHEN COALESCE("is_private", false) = false THEN 'published' ELSE 'draft' END,
  "visibility" = CASE WHEN COALESCE("is_private", false) = false THEN 'public' ELSE 'private' END
WHERE "status" IS NULL OR "visibility" IS NULL;

ALTER TABLE "public"."marketplace_skill"
  ALTER COLUMN "status" SET DEFAULT 'published',
  ALTER COLUMN "status" SET NOT NULL,
  ALTER COLUMN "visibility" SET DEFAULT 'public',
  ALTER COLUMN "visibility" SET NOT NULL;

ALTER TABLE "public"."marketplace_template"
  ALTER COLUMN "status" SET DEFAULT 'published',
  ALTER COLUMN "status" SET NOT NULL,
  ALTER COLUMN "visibility" SET DEFAULT 'public',
  ALTER COLUMN "visibility" SET NOT NULL;

CREATE INDEX IF NOT EXISTS "idx_marketplace_skill_user_id" ON "public"."marketplace_skill" ("user_id");
CREATE INDEX IF NOT EXISTS "idx_marketplace_skill_hasn_id" ON "public"."marketplace_skill" ("hasn_id");
CREATE INDEX IF NOT EXISTS "idx_marketplace_skill_status_visibility" ON "public"."marketplace_skill" ("status", "visibility");
CREATE UNIQUE INDEX IF NOT EXISTS "idx_marketplace_skill_namespace_slug"
  ON "public"."marketplace_skill" ("namespace", "slug");
CREATE INDEX IF NOT EXISTS "idx_marketplace_template_user_id" ON "public"."marketplace_template" ("user_id");
CREATE INDEX IF NOT EXISTS "idx_marketplace_template_hasn_id" ON "public"."marketplace_template" ("hasn_id");
CREATE INDEX IF NOT EXISTS "idx_marketplace_template_status_visibility" ON "public"."marketplace_template" ("status", "visibility");
CREATE UNIQUE INDEX IF NOT EXISTS "idx_marketplace_template_namespace_slug"
  ON "public"."marketplace_template" ("namespace", "slug");

COMMENT ON COLUMN "public"."marketplace_skill"."user_id" IS '资源所有者用户ID';
COMMENT ON COLUMN "public"."marketplace_skill"."hasn_id" IS '资源所有者 HASN ID';
COMMENT ON COLUMN "public"."marketplace_skill"."status" IS '发布状态';
COMMENT ON COLUMN "public"."marketplace_skill"."visibility" IS '可见性';
COMMENT ON COLUMN "public"."marketplace_skill"."reviewed_by" IS '审核人用户ID';
COMMENT ON COLUMN "public"."marketplace_skill"."reviewed_at" IS '审核时间';
COMMENT ON COLUMN "public"."marketplace_skill"."review_note" IS '审核备注';
COMMENT ON COLUMN "public"."marketplace_skill"."published_at" IS '发布时间';
COMMENT ON COLUMN "public"."marketplace_skill"."suspended_at" IS '封禁时间';
COMMENT ON COLUMN "public"."marketplace_skill"."suspend_reason" IS '封禁原因';
COMMENT ON COLUMN "public"."marketplace_skill"."star_count" IS '星标数';
COMMENT ON COLUMN "public"."marketplace_skill"."repo_path" IS '在 hasn-hub 中的路径';
COMMENT ON COLUMN "public"."marketplace_skill"."git_commit_hash" IS '最新同步的 commit hash';
COMMENT ON COLUMN "public"."marketplace_skill"."synced_at" IS '最后同步时间';
COMMENT ON COLUMN "public"."marketplace_skill"."translated_at" IS '最后翻译时间';

COMMENT ON COLUMN "public"."marketplace_template"."user_id" IS '资源所有者用户ID';
COMMENT ON COLUMN "public"."marketplace_template"."hasn_id" IS '资源所有者 HASN ID';
COMMENT ON COLUMN "public"."marketplace_template"."status" IS '发布状态';
COMMENT ON COLUMN "public"."marketplace_template"."visibility" IS '可见性';
COMMENT ON COLUMN "public"."marketplace_template"."reviewed_by" IS '审核人用户ID';
COMMENT ON COLUMN "public"."marketplace_template"."reviewed_at" IS '审核时间';
COMMENT ON COLUMN "public"."marketplace_template"."review_note" IS '审核备注';
COMMENT ON COLUMN "public"."marketplace_template"."published_at" IS '发布时间';
COMMENT ON COLUMN "public"."marketplace_template"."suspended_at" IS '封禁时间';
COMMENT ON COLUMN "public"."marketplace_template"."suspend_reason" IS '封禁原因';
COMMENT ON COLUMN "public"."marketplace_template"."repo_path" IS '在 hasn-hub 中的路径';
COMMENT ON COLUMN "public"."marketplace_template"."git_commit_hash" IS '最新同步的 commit hash';
COMMENT ON COLUMN "public"."marketplace_template"."synced_at" IS '最后同步时间';
COMMENT ON COLUMN "public"."marketplace_template"."translated_at" IS '最后翻译时间';

COMMIT;
