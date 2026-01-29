-- 应用版本表
CREATE TABLE "public"."marketplace_app_version" (
  "id" bigserial PRIMARY KEY,
  "app_id" varchar(100) COLLATE "pg_catalog"."default" NOT NULL,
  "version" varchar(50) COLLATE "pg_catalog"."default" NOT NULL,
  "changelog" text COLLATE "pg_catalog"."default",
  "skill_dependencies_versioned" jsonb,
  "package_url" varchar(500) COLLATE "pg_catalog"."default",
  "file_hash" varchar(64) COLLATE "pg_catalog"."default",
  "file_size" int4,
  "is_latest" bool NOT NULL DEFAULT false,
  "published_at" timestamptz(6) NOT NULL DEFAULT NOW(),
  "created_time" timestamptz(6) NOT NULL DEFAULT NOW(),
  "updated_time" timestamptz(6),
  UNIQUE("app_id", "version")
);

CREATE INDEX "idx_marketplace_app_version_app_id" ON "public"."marketplace_app_version" ("app_id");
CREATE INDEX "idx_marketplace_app_version_is_latest" ON "public"."marketplace_app_version" ("is_latest");

COMMENT ON COLUMN "public"."marketplace_app_version"."id" IS '主键 ID';
COMMENT ON COLUMN "public"."marketplace_app_version"."app_id" IS '关联的应用ID';
COMMENT ON COLUMN "public"."marketplace_app_version"."version" IS '语义化版本号';
COMMENT ON COLUMN "public"."marketplace_app_version"."changelog" IS '版本更新日志';
COMMENT ON COLUMN "public"."marketplace_app_version"."skill_dependencies_versioned" IS '带版本号的技能依赖';
COMMENT ON COLUMN "public"."marketplace_app_version"."package_url" IS '完整包下载URL';
COMMENT ON COLUMN "public"."marketplace_app_version"."file_hash" IS 'SHA256校验值';
COMMENT ON COLUMN "public"."marketplace_app_version"."file_size" IS '包大小（字节）';
COMMENT ON COLUMN "public"."marketplace_app_version"."is_latest" IS '是否为最新版本';
COMMENT ON COLUMN "public"."marketplace_app_version"."published_at" IS '发布时间';
COMMENT ON TABLE "public"."marketplace_app_version" IS '应用版本表';
