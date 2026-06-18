-- =====================================================
-- 迁移：创建设计系统生成应用（app_id=designsystem）云端权威 4 表
-- schema=hasn_designsystem：design_system / revision / collaborator / consumer_link
-- 生产执行：psql -d huanxing -f backend/sql/hasn_designsystem/migrations/2026-06-18-create-designsystem-tables.sql
--
-- 幂等 + 防「空自动建表」陷阱（CLEAN 系列教训）：运行中 backend 的 lifespan create_all 可能在迁移前
--   按 model 自动建空表（无完整索引/约束）。每表先「若存在且为空 → DROP」清掉半成品，再用完整 DDL
--   重建（IF NOT EXISTS 兜住「已有数据、不该 DROP」的情形 → no-op）。可重复执行。
-- =====================================================
CREATE SCHEMA IF NOT EXISTS "hasn_designsystem";

-- ── design_system ──────────────────────────────────────────────
DO $$
BEGIN
  IF to_regclass('hasn_designsystem.design_system') IS NOT NULL
     AND (SELECT count(*) FROM "hasn_designsystem"."design_system") = 0 THEN
    DROP TABLE "hasn_designsystem"."design_system";
  END IF;
END $$;

CREATE TABLE IF NOT EXISTS "hasn_designsystem"."design_system" (
  "id"                  bigserial      PRIMARY KEY,
  "owner_hasn_id"       varchar(64)    NOT NULL,
  "name"                varchar(128)   NOT NULL,
  "slug"                varchar(128)   NOT NULL,
  "category"            varchar(48),
  "source_kind"         varchar(32)    NOT NULL DEFAULT 'generated',
  "score"               smallint,
  "grade"               varchar(16),
  "recommend_rebuild"   boolean        NOT NULL DEFAULT false,
  "is_builtin"          boolean        NOT NULL DEFAULT false,
  "enterprise_id"       bigint,
  "current_revision_id" bigint,
  "content_hash"        varchar(128)   NOT NULL DEFAULT '',
  "created_time"        timestamptz(6) NOT NULL DEFAULT now(),
  "updated_time"        timestamptz(6),
  "deleted_time"        timestamptz(6),
  UNIQUE ("owner_hasn_id", "slug")
);
CREATE INDEX IF NOT EXISTS "idx_ds_owner"      ON "hasn_designsystem"."design_system" ("owner_hasn_id") WHERE "deleted_time" IS NULL;
CREATE INDEX IF NOT EXISTS "idx_ds_builtin"    ON "hasn_designsystem"."design_system" ("is_builtin") WHERE "is_builtin";
CREATE INDEX IF NOT EXISTS "idx_ds_enterprise" ON "hasn_designsystem"."design_system" ("enterprise_id") WHERE "enterprise_id" IS NOT NULL;

-- ── revision ───────────────────────────────────────────────────
DO $$
BEGIN
  IF to_regclass('hasn_designsystem.revision') IS NOT NULL
     AND (SELECT count(*) FROM "hasn_designsystem"."revision") = 0 THEN
    DROP TABLE "hasn_designsystem"."revision";
  END IF;
END $$;

CREATE TABLE IF NOT EXISTS "hasn_designsystem"."revision" (
  "id"                         bigserial      PRIMARY KEY,
  "design_system_id"           bigint         NOT NULL,
  "rev_no"                     int            NOT NULL,
  "author_kind"                varchar(8)     NOT NULL DEFAULT 'human',
  "author_id"                  varchar(64)    NOT NULL DEFAULT '',
  "bundle_asset_id"            varchar(128),
  "tokens_css"                 text,
  "design_tokens_json"         jsonb,
  "tailwind_css"               text,
  "design_md"                  text,
  "components_html"            text,
  "components_manifest_json"   jsonb,
  "token_contract_report_json" jsonb,
  "note"                       varchar(512),
  "created_time"               timestamptz(6) NOT NULL DEFAULT now(),
  "updated_time"               timestamptz(6)
);
CREATE UNIQUE INDEX IF NOT EXISTS "uq_ds_revision_no" ON "hasn_designsystem"."revision" ("design_system_id", "rev_no");
CREATE INDEX IF NOT EXISTS "idx_ds_revision_ds" ON "hasn_designsystem"."revision" ("design_system_id");

-- ── collaborator ───────────────────────────────────────────────
DO $$
BEGIN
  IF to_regclass('hasn_designsystem.collaborator') IS NOT NULL
     AND (SELECT count(*) FROM "hasn_designsystem"."collaborator") = 0 THEN
    DROP TABLE "hasn_designsystem"."collaborator";
  END IF;
END $$;

CREATE TABLE IF NOT EXISTS "hasn_designsystem"."collaborator" (
  "id"               bigserial      PRIMARY KEY,
  "design_system_id" bigint         NOT NULL,
  "agent_hasn_id"    varchar(64)    NOT NULL,
  "added_by"         varchar(64)    NOT NULL,
  "created_time"     timestamptz(6) NOT NULL DEFAULT now(),
  "updated_time"     timestamptz(6)
);
CREATE UNIQUE INDEX IF NOT EXISTS "uq_ds_collaborator" ON "hasn_designsystem"."collaborator" ("design_system_id", "agent_hasn_id");

-- ── consumer_link ──────────────────────────────────────────────
DO $$
BEGIN
  IF to_regclass('hasn_designsystem.consumer_link') IS NOT NULL
     AND (SELECT count(*) FROM "hasn_designsystem"."consumer_link") = 0 THEN
    DROP TABLE "hasn_designsystem"."consumer_link";
  END IF;
END $$;

CREATE TABLE IF NOT EXISTS "hasn_designsystem"."consumer_link" (
  "id"                bigserial      PRIMARY KEY,
  "design_system_id"  bigint         NOT NULL,
  "consumer_app"      varchar(32)    NOT NULL,
  "consumer_ref"      varchar(128)   NOT NULL,
  "bound_revision_id" bigint,
  "created_time"      timestamptz(6) NOT NULL DEFAULT now(),
  "updated_time"      timestamptz(6)
);
CREATE UNIQUE INDEX IF NOT EXISTS "uq_ds_consumer_link" ON "hasn_designsystem"."consumer_link" ("consumer_app", "consumer_ref");
CREATE INDEX IF NOT EXISTS "idx_ds_consumer_ds" ON "hasn_designsystem"."consumer_link" ("design_system_id");
