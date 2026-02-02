-- 应用版本表（用于桌面端版本检测和更新）
CREATE TABLE "public"."app_version" (
  "id" bigserial PRIMARY KEY,
  "app_code" varchar(50) COLLATE "pg_catalog"."default" NOT NULL DEFAULT 'creator-flow',
  "version" varchar(50) COLLATE "pg_catalog"."default" NOT NULL,
  "platform" varchar(30) COLLATE "pg_catalog"."default" NOT NULL,
  "arch" varchar(20) COLLATE "pg_catalog"."default" NOT NULL,
  "download_url" varchar(500) COLLATE "pg_catalog"."default" NOT NULL,
  "file_hash" varchar(64) COLLATE "pg_catalog"."default" NOT NULL,
  "file_size" int8 NOT NULL DEFAULT 0,
  "filename" varchar(200) COLLATE "pg_catalog"."default",
  "changelog" text COLLATE "pg_catalog"."default",
  "min_version" varchar(50) COLLATE "pg_catalog"."default",
  "is_force_update" bool NOT NULL DEFAULT false,
  "is_latest" bool NOT NULL DEFAULT false,
  "status" varchar(20) COLLATE "pg_catalog"."default" NOT NULL DEFAULT 'draft',
  "published_at" timestamptz(6),
  "created_time" timestamptz(6) NOT NULL DEFAULT NOW(),
  "updated_time" timestamptz(6),
  UNIQUE("app_code", "version", "platform", "arch")
);

CREATE INDEX "idx_app_version_app_code" ON "public"."app_version" ("app_code");
CREATE INDEX "idx_app_version_platform_arch" ON "public"."app_version" ("platform", "arch");
CREATE INDEX "idx_app_version_is_latest" ON "public"."app_version" ("is_latest");
CREATE INDEX "idx_app_version_status" ON "public"."app_version" ("status");

COMMENT ON TABLE "public"."app_version" IS '应用版本表';
COMMENT ON COLUMN "public"."app_version"."id" IS '主键 ID';
COMMENT ON COLUMN "public"."app_version"."app_code" IS '应用标识 (creator-flow:CreatorFlow/craft-agent:CraftAgent)';
COMMENT ON COLUMN "public"."app_version"."version" IS '语义化版本号';
COMMENT ON COLUMN "public"."app_version"."platform" IS '平台 (darwin:macOS/win32:Windows/linux:Linux)';
COMMENT ON COLUMN "public"."app_version"."arch" IS '架构 (x64:x64/arm64:ARM64)';
COMMENT ON COLUMN "public"."app_version"."download_url" IS '下载地址';
COMMENT ON COLUMN "public"."app_version"."file_hash" IS 'SHA256 校验值';
COMMENT ON COLUMN "public"."app_version"."file_size" IS '文件大小（字节）';
COMMENT ON COLUMN "public"."app_version"."filename" IS '文件名';
COMMENT ON COLUMN "public"."app_version"."changelog" IS '更新日志';
COMMENT ON COLUMN "public"."app_version"."min_version" IS '最小兼容版本';
COMMENT ON COLUMN "public"."app_version"."is_force_update" IS '是否强制更新';
COMMENT ON COLUMN "public"."app_version"."is_latest" IS '是否为最新版本';
COMMENT ON COLUMN "public"."app_version"."status" IS '状态 (draft:草稿:blue/published:已发布:green/deprecated:已废弃:orange)';
COMMENT ON COLUMN "public"."app_version"."published_at" IS '发布时间';
