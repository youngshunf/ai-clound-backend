-- ============================================
-- 技能市场完整迁移 SQL
-- 日期：2026-05-26
-- 说明：如果表已存在则跳过创建，确保幂等性
-- ============================================

BEGIN;

-- ============================================
-- 1. 创建 marketplace_skill 表（如果不存在）
-- ============================================
CREATE TABLE IF NOT EXISTS "public"."marketplace_skill" (
  "id" bigserial PRIMARY KEY,
  "skill_id" varchar(255) COLLATE "pg_catalog"."default" NOT NULL,
  "namespace" varchar(160) COLLATE "pg_catalog"."default",
  "slug" varchar(100) COLLATE "pg_catalog"."default",
  "user_id" int8,
  "hasn_id" varchar(40) COLLATE "pg_catalog"."default",
  "status" varchar(20) COLLATE "pg_catalog"."default" NOT NULL DEFAULT 'published',
  "visibility" varchar(20) COLLATE "pg_catalog"."default" NOT NULL DEFAULT 'public',
  "reviewed_by" int8,
  "reviewed_at" timestamptz(6),
  "review_note" text COLLATE "pg_catalog"."default",
  "published_at" timestamptz(6),
  "suspended_at" timestamptz(6),
  "suspend_reason" text COLLATE "pg_catalog"."default",
  "name" varchar(200) COLLATE "pg_catalog"."default" NOT NULL,
  "name_en" varchar(200) COLLATE "pg_catalog"."default",
  "name_zh" varchar(200) COLLATE "pg_catalog"."default",
  "description" text COLLATE "pg_catalog"."default",
  "description_en" text COLLATE "pg_catalog"."default",
  "description_zh" text COLLATE "pg_catalog"."default",
  "source_language" varchar(10) COLLATE "pg_catalog"."default",
  "icon_url" varchar(500) COLLATE "pg_catalog"."default",
  "emoji" varchar(20) COLLATE "pg_catalog"."default",
  "author_id" int8,
  "author_name" varchar(100) COLLATE "pg_catalog"."default",
  "category" varchar(50) COLLATE "pg_catalog"."default",
  "tags" varchar(500) COLLATE "pg_catalog"."default",
  "source_type" varchar(20) COLLATE "pg_catalog"."default" DEFAULT 'github',
  "source_repo_url" varchar(500) COLLATE "pg_catalog"."default",
  "source_repo_path" varchar(500) COLLATE "pg_catalog"."default",
  "pricing_type" varchar(20) COLLATE "pg_catalog"."default" NOT NULL DEFAULT 'free',
  "price" numeric(10, 2) NOT NULL DEFAULT 0,
  "is_private" bool NOT NULL DEFAULT false,
  "is_official" bool NOT NULL DEFAULT false,
  "download_count" int4 NOT NULL DEFAULT 0,
  "star_count" int4 NOT NULL DEFAULT 0,
  "repo_path" varchar(500) COLLATE "pg_catalog"."default",
  "git_commit_hash" varchar(64) COLLATE "pg_catalog"."default",
  "synced_at" timestamptz(6),
  "translated_at" timestamptz(6),
  "created_time" timestamptz(6) NOT NULL DEFAULT NOW(),
  "updated_time" timestamptz(6)
);

-- 添加唯一约束（如果不存在）
DO $$
BEGIN
  IF NOT EXISTS (
    SELECT 1 FROM pg_constraint WHERE conname = 'marketplace_skill_skill_id_key'
  ) THEN
    ALTER TABLE "public"."marketplace_skill" ADD CONSTRAINT "marketplace_skill_skill_id_key" UNIQUE("skill_id");
  END IF;
END $$;

-- 创建索引（如果不存在）
CREATE INDEX IF NOT EXISTS "idx_marketplace_skill_category" ON "public"."marketplace_skill" ("category");
CREATE INDEX IF NOT EXISTS "idx_marketplace_skill_author_id" ON "public"."marketplace_skill" ("author_id");
CREATE INDEX IF NOT EXISTS "idx_marketplace_skill_user_id" ON "public"."marketplace_skill" ("user_id");
CREATE INDEX IF NOT EXISTS "idx_marketplace_skill_hasn_id" ON "public"."marketplace_skill" ("hasn_id");
CREATE INDEX IF NOT EXISTS "idx_marketplace_skill_status_visibility" ON "public"."marketplace_skill" ("status", "visibility");
CREATE INDEX IF NOT EXISTS "idx_marketplace_skill_pricing_type" ON "public"."marketplace_skill" ("pricing_type");
CREATE INDEX IF NOT EXISTS "idx_marketplace_skill_download_count" ON "public"."marketplace_skill" ("download_count" DESC);
CREATE INDEX IF NOT EXISTS "idx_marketplace_skill_source_type" ON "public"."marketplace_skill" ("source_type");
CREATE UNIQUE INDEX IF NOT EXISTS "idx_marketplace_skill_namespace_slug" ON "public"."marketplace_skill" ("namespace", "slug");

-- 添加注释
COMMENT ON TABLE "public"."marketplace_skill" IS '技能市场技能表';
COMMENT ON COLUMN "public"."marketplace_skill"."id" IS '主键 ID';
COMMENT ON COLUMN "public"."marketplace_skill"."skill_id" IS '技能唯一标识';
COMMENT ON COLUMN "public"."marketplace_skill"."namespace" IS '命名空间（如 huanxing/clawhub）';
COMMENT ON COLUMN "public"."marketplace_skill"."slug" IS '技能标识符（如 translator-pro）';
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
COMMENT ON COLUMN "public"."marketplace_skill"."name" IS '技能名称';
COMMENT ON COLUMN "public"."marketplace_skill"."name_en" IS '英文名称';
COMMENT ON COLUMN "public"."marketplace_skill"."name_zh" IS '中文名称';
COMMENT ON COLUMN "public"."marketplace_skill"."description" IS '技能描述';
COMMENT ON COLUMN "public"."marketplace_skill"."description_en" IS '英文描述';
COMMENT ON COLUMN "public"."marketplace_skill"."description_zh" IS '中文描述';
COMMENT ON COLUMN "public"."marketplace_skill"."source_language" IS '源语言（en/zh，用于判断哪个是原文）';
COMMENT ON COLUMN "public"."marketplace_skill"."icon_url" IS 'SVG图标URL';
COMMENT ON COLUMN "public"."marketplace_skill"."emoji" IS 'emoji图标';
COMMENT ON COLUMN "public"."marketplace_skill"."author_id" IS '作者用户ID';
COMMENT ON COLUMN "public"."marketplace_skill"."author_name" IS '作者名称';
COMMENT ON COLUMN "public"."marketplace_skill"."category" IS '分类';
COMMENT ON COLUMN "public"."marketplace_skill"."tags" IS '标签，逗号分隔';
COMMENT ON COLUMN "public"."marketplace_skill"."source_type" IS '来源类型 (huanxing:幻形自研:purple/github:GitHub:blue/clawhub:ClawHub:green)';
COMMENT ON COLUMN "public"."marketplace_skill"."source_repo_url" IS '源仓库 URL';
COMMENT ON COLUMN "public"."marketplace_skill"."source_repo_path" IS '仓库内路径（如 huanxing-skills/productivity/translator-pro）';
COMMENT ON COLUMN "public"."marketplace_skill"."pricing_type" IS '定价类型 (free:免费:green/paid:付费:orange)';
COMMENT ON COLUMN "public"."marketplace_skill"."price" IS '价格';
COMMENT ON COLUMN "public"."marketplace_skill"."is_private" IS '是否私有';
COMMENT ON COLUMN "public"."marketplace_skill"."is_official" IS '是否官方技能';
COMMENT ON COLUMN "public"."marketplace_skill"."download_count" IS '下载次数';
COMMENT ON COLUMN "public"."marketplace_skill"."star_count" IS '星标数';
COMMENT ON COLUMN "public"."marketplace_skill"."repo_path" IS '在 hasn-hub 中的路径';
COMMENT ON COLUMN "public"."marketplace_skill"."git_commit_hash" IS '最新同步的 commit hash';
COMMENT ON COLUMN "public"."marketplace_skill"."synced_at" IS '最后同步时间';
COMMENT ON COLUMN "public"."marketplace_skill"."translated_at" IS '最后翻译时间';

-- ============================================
-- 2. 创建 marketplace_skill_version 表（如果不存在）
-- ============================================
CREATE TABLE IF NOT EXISTS "public"."marketplace_skill_version" (
  "id" bigserial PRIMARY KEY,
  "skill_id" varchar(255) COLLATE "pg_catalog"."default" NOT NULL,
  "version" varchar(50) COLLATE "pg_catalog"."default" NOT NULL,
  "changelog" text COLLATE "pg_catalog"."default",
  "package_url" varchar(500) COLLATE "pg_catalog"."default",
  "file_hash" varchar(64) COLLATE "pg_catalog"."default",
  "file_size" int4,
  "is_latest" bool NOT NULL DEFAULT false,
  "published_at" timestamptz(6) NOT NULL DEFAULT NOW(),
  "created_time" timestamptz(6) NOT NULL DEFAULT NOW(),
  "updated_time" timestamptz(6)
);

-- 添加唯一约束（如果不存在）
DO $$
BEGIN
  IF NOT EXISTS (
    SELECT 1 FROM pg_constraint WHERE conname = 'marketplace_skill_version_skill_id_version_key'
  ) THEN
    ALTER TABLE "public"."marketplace_skill_version" ADD CONSTRAINT "marketplace_skill_version_skill_id_version_key" UNIQUE("skill_id", "version");
  END IF;
END $$;

-- 创建索引（如果不存在）
CREATE INDEX IF NOT EXISTS "idx_marketplace_skill_version_skill_id" ON "public"."marketplace_skill_version" ("skill_id");
CREATE INDEX IF NOT EXISTS "idx_marketplace_skill_version_is_latest" ON "public"."marketplace_skill_version" ("is_latest");

-- 添加注释
COMMENT ON TABLE "public"."marketplace_skill_version" IS '技能版本表';
COMMENT ON COLUMN "public"."marketplace_skill_version"."id" IS '主键 ID';
COMMENT ON COLUMN "public"."marketplace_skill_version"."skill_id" IS '关联的技能ID';
COMMENT ON COLUMN "public"."marketplace_skill_version"."version" IS '语义化版本号';
COMMENT ON COLUMN "public"."marketplace_skill_version"."changelog" IS '版本更新日志';
COMMENT ON COLUMN "public"."marketplace_skill_version"."package_url" IS '完整包下载URL';
COMMENT ON COLUMN "public"."marketplace_skill_version"."file_hash" IS 'SHA256校验值';
COMMENT ON COLUMN "public"."marketplace_skill_version"."file_size" IS '包大小（字节）';
COMMENT ON COLUMN "public"."marketplace_skill_version"."is_latest" IS '是否为最新版本';
COMMENT ON COLUMN "public"."marketplace_skill_version"."published_at" IS '发布时间';

-- ============================================
-- 3. 创建 marketplace_template 表（如果不存在）
-- ============================================
CREATE TABLE IF NOT EXISTS "public"."marketplace_template" (
  "id" bigserial PRIMARY KEY,
  "template_id" varchar(255) COLLATE "pg_catalog"."default" NOT NULL,
  "namespace" varchar(160) COLLATE "pg_catalog"."default",
  "slug" varchar(100) COLLATE "pg_catalog"."default",
  "user_id" int8,
  "hasn_id" varchar(40) COLLATE "pg_catalog"."default",
  "status" varchar(20) COLLATE "pg_catalog"."default" NOT NULL DEFAULT 'published',
  "visibility" varchar(20) COLLATE "pg_catalog"."default" NOT NULL DEFAULT 'public',
  "reviewed_by" int8,
  "reviewed_at" timestamptz(6),
  "review_note" text COLLATE "pg_catalog"."default",
  "published_at" timestamptz(6),
  "suspended_at" timestamptz(6),
  "suspend_reason" text COLLATE "pg_catalog"."default",
  "template_type" varchar(20) COLLATE "pg_catalog"."default" NOT NULL DEFAULT 'agent_template',
  "name" varchar(200) COLLATE "pg_catalog"."default" NOT NULL,
  "name_en" varchar(200) COLLATE "pg_catalog"."default",
  "name_zh" varchar(200) COLLATE "pg_catalog"."default",
  "description" text COLLATE "pg_catalog"."default",
  "description_en" text COLLATE "pg_catalog"."default",
  "description_zh" text COLLATE "pg_catalog"."default",
  "source_language" varchar(10) COLLATE "pg_catalog"."default",
  "icon_url" varchar(500) COLLATE "pg_catalog"."default",
  "emoji" varchar(20) COLLATE "pg_catalog"."default",
  "author_id" int8,
  "author_name" varchar(100) COLLATE "pg_catalog"."default",
  "pricing_type" varchar(20) COLLATE "pg_catalog"."default" NOT NULL DEFAULT 'free',
  "price" numeric(10, 2) NOT NULL DEFAULT 0,
  "is_private" bool NOT NULL DEFAULT false,
  "is_official" bool NOT NULL DEFAULT false,
  "download_count" int4 NOT NULL DEFAULT 0,
  "category" varchar(50) COLLATE "pg_catalog"."default",
  "tags" varchar(500) COLLATE "pg_catalog"."default",
  "source_type" varchar(20) COLLATE "pg_catalog"."default" DEFAULT 'github',
  "source_repo_url" varchar(500) COLLATE "pg_catalog"."default",
  "source_repo_path" varchar(500) COLLATE "pg_catalog"."default",
  "skill_dependencies" text COLLATE "pg_catalog"."default",
  "sop_dependencies" text COLLATE "pg_catalog"."default",
  "repo_path" varchar(500) COLLATE "pg_catalog"."default",
  "git_commit_hash" varchar(64) COLLATE "pg_catalog"."default",
  "synced_at" timestamptz(6),
  "translated_at" timestamptz(6),
  "created_time" timestamptz(6) NOT NULL DEFAULT NOW(),
  "updated_time" timestamptz(6)
);

-- 添加唯一约束（如果不存在）
DO $$
BEGIN
  IF NOT EXISTS (
    SELECT 1 FROM pg_constraint WHERE conname = 'marketplace_template_template_id_key'
  ) THEN
    ALTER TABLE "public"."marketplace_template" ADD CONSTRAINT "marketplace_template_template_id_key" UNIQUE("template_id");
  END IF;
END $$;

-- 创建索引（如果不存在）
CREATE INDEX IF NOT EXISTS "idx_marketplace_template_author_id" ON "public"."marketplace_template" ("author_id");
CREATE INDEX IF NOT EXISTS "idx_marketplace_template_user_id" ON "public"."marketplace_template" ("user_id");
CREATE INDEX IF NOT EXISTS "idx_marketplace_template_hasn_id" ON "public"."marketplace_template" ("hasn_id");
CREATE INDEX IF NOT EXISTS "idx_marketplace_template_status_visibility" ON "public"."marketplace_template" ("status", "visibility");
CREATE INDEX IF NOT EXISTS "idx_marketplace_template_pricing_type" ON "public"."marketplace_template" ("pricing_type");
CREATE INDEX IF NOT EXISTS "idx_marketplace_template_download_count" ON "public"."marketplace_template" ("download_count" DESC);
CREATE INDEX IF NOT EXISTS "idx_marketplace_template_template_type" ON "public"."marketplace_template" ("template_type");
CREATE UNIQUE INDEX IF NOT EXISTS "idx_marketplace_template_namespace_slug" ON "public"."marketplace_template" ("namespace", "slug");

-- 添加注释
COMMENT ON TABLE "public"."marketplace_template" IS '技能市场模板表（Agent模板/技能包/SOP包）';
COMMENT ON COLUMN "public"."marketplace_template"."id" IS '主键 ID';
COMMENT ON COLUMN "public"."marketplace_template"."template_id" IS '模板唯一标识';
COMMENT ON COLUMN "public"."marketplace_template"."namespace" IS '命名空间（如 huanxing/clawhub）';
COMMENT ON COLUMN "public"."marketplace_template"."slug" IS '模板标识符';
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
COMMENT ON COLUMN "public"."marketplace_template"."template_type" IS '模板类型 (agent_template:Agent模板:blue/skill_pack:技能包:cyan/sop_pack:SOP包:purple)';
COMMENT ON COLUMN "public"."marketplace_template"."name" IS '模板名称';
COMMENT ON COLUMN "public"."marketplace_template"."name_en" IS '英文名称';
COMMENT ON COLUMN "public"."marketplace_template"."name_zh" IS '中文名称';
COMMENT ON COLUMN "public"."marketplace_template"."description" IS '模板描述';
COMMENT ON COLUMN "public"."marketplace_template"."description_en" IS '英文描述';
COMMENT ON COLUMN "public"."marketplace_template"."description_zh" IS '中文描述';
COMMENT ON COLUMN "public"."marketplace_template"."source_language" IS '源语言（en/zh，用于判断哪个是原文）';
COMMENT ON COLUMN "public"."marketplace_template"."icon_url" IS '模板图标URL';
COMMENT ON COLUMN "public"."marketplace_template"."emoji" IS 'emoji图标';
COMMENT ON COLUMN "public"."marketplace_template"."author_id" IS '作者用户ID';
COMMENT ON COLUMN "public"."marketplace_template"."author_name" IS '作者名称';
COMMENT ON COLUMN "public"."marketplace_template"."pricing_type" IS '定价类型 (free:免费:green/paid:付费:orange/subscription:订阅:blue)';
COMMENT ON COLUMN "public"."marketplace_template"."price" IS '价格';
COMMENT ON COLUMN "public"."marketplace_template"."is_private" IS '是否私有';
COMMENT ON COLUMN "public"."marketplace_template"."is_official" IS '是否官方模板';
COMMENT ON COLUMN "public"."marketplace_template"."download_count" IS '下载次数';
COMMENT ON COLUMN "public"."marketplace_template"."category" IS '分类';
COMMENT ON COLUMN "public"."marketplace_template"."tags" IS '标签，逗号分隔';
COMMENT ON COLUMN "public"."marketplace_template"."source_type" IS '来源类型 (github:GitHub:blue/clawhub:ClawHub:green/local:本地:gray)';
COMMENT ON COLUMN "public"."marketplace_template"."source_repo_url" IS '源仓库 URL';
COMMENT ON COLUMN "public"."marketplace_template"."source_repo_path" IS '仓库内路径';
COMMENT ON COLUMN "public"."marketplace_template"."skill_dependencies" IS '依赖的技能ID列表，逗号分隔';
COMMENT ON COLUMN "public"."marketplace_template"."sop_dependencies" IS '依赖的SOP ID列表，逗号分隔';
COMMENT ON COLUMN "public"."marketplace_template"."repo_path" IS '在 hasn-hub 中的路径';
COMMENT ON COLUMN "public"."marketplace_template"."git_commit_hash" IS '最新同步的 commit hash';
COMMENT ON COLUMN "public"."marketplace_template"."synced_at" IS '最后同步时间';
COMMENT ON COLUMN "public"."marketplace_template"."translated_at" IS '最后翻译时间';

-- ============================================
-- 4. 创建 marketplace_template_version 表（如果不存在）
-- ============================================
CREATE TABLE IF NOT EXISTS "public"."marketplace_template_version" (
  "id" bigserial PRIMARY KEY,
  "template_id" varchar(255) COLLATE "pg_catalog"."default" NOT NULL,
  "version" varchar(50) COLLATE "pg_catalog"."default" NOT NULL,
  "changelog" text COLLATE "pg_catalog"."default",
  "skill_dependencies_versioned" jsonb,
  "package_url" varchar(500) COLLATE "pg_catalog"."default",
  "file_hash" varchar(64) COLLATE "pg_catalog"."default",
  "file_size" int4,
  "is_latest" bool NOT NULL DEFAULT false,
  "published_at" timestamptz(6) NOT NULL DEFAULT NOW(),
  "created_time" timestamptz(6) NOT NULL DEFAULT NOW(),
  "updated_time" timestamptz(6)
);

-- 添加唯一约束（如果不存在）
DO $$
BEGIN
  IF NOT EXISTS (
    SELECT 1 FROM pg_constraint WHERE conname = 'marketplace_template_version_template_id_version_key'
  ) THEN
    ALTER TABLE "public"."marketplace_template_version" ADD CONSTRAINT "marketplace_template_version_template_id_version_key" UNIQUE("template_id", "version");
  END IF;
END $$;

-- 创建索引（如果不存在）
CREATE INDEX IF NOT EXISTS "idx_marketplace_template_version_template_id" ON "public"."marketplace_template_version" ("template_id");
CREATE INDEX IF NOT EXISTS "idx_marketplace_template_version_is_latest" ON "public"."marketplace_template_version" ("is_latest");

-- 添加注释
COMMENT ON TABLE "public"."marketplace_template_version" IS '模板版本表';
COMMENT ON COLUMN "public"."marketplace_template_version"."id" IS '主键 ID';
COMMENT ON COLUMN "public"."marketplace_template_version"."template_id" IS '关联的模板ID';
COMMENT ON COLUMN "public"."marketplace_template_version"."version" IS '语义化版本号';
COMMENT ON COLUMN "public"."marketplace_template_version"."changelog" IS '版本更新日志';
COMMENT ON COLUMN "public"."marketplace_template_version"."skill_dependencies_versioned" IS '带版本号的技能依赖';
COMMENT ON COLUMN "public"."marketplace_template_version"."package_url" IS '完整包下载URL';
COMMENT ON COLUMN "public"."marketplace_template_version"."file_hash" IS 'SHA256校验值';
COMMENT ON COLUMN "public"."marketplace_template_version"."file_size" IS '包大小（字节）';
COMMENT ON COLUMN "public"."marketplace_template_version"."is_latest" IS '是否为最新版本';
COMMENT ON COLUMN "public"."marketplace_template_version"."published_at" IS '发布时间';

-- ============================================
-- 5. 创建 marketplace_sync_log 表（如果不存在）
-- ============================================
CREATE TABLE IF NOT EXISTS "public"."marketplace_sync_log" (
  "id" bigserial PRIMARY KEY,
  "sync_type" varchar(20) COLLATE "pg_catalog"."default" NOT NULL,
  "source" varchar(50) COLLATE "pg_catalog"."default" NOT NULL,
  "status" varchar(20) COLLATE "pg_catalog"."default" NOT NULL DEFAULT 'running',
  "total_count" int4 NOT NULL DEFAULT 0,
  "success_count" int4 NOT NULL DEFAULT 0,
  "failed_count" int4 NOT NULL DEFAULT 0,
  "error_message" text COLLATE "pg_catalog"."default",
  "started_at" timestamptz(6) NOT NULL DEFAULT NOW(),
  "completed_at" timestamptz(6),
  "created_time" timestamptz(6) NOT NULL DEFAULT NOW(),
  "updated_time" timestamptz(6)
);

-- 创建索引（如果不存在）
CREATE INDEX IF NOT EXISTS "idx_marketplace_sync_log_sync_type" ON "public"."marketplace_sync_log" ("sync_type");
CREATE INDEX IF NOT EXISTS "idx_marketplace_sync_log_source" ON "public"."marketplace_sync_log" ("source");
CREATE INDEX IF NOT EXISTS "idx_marketplace_sync_log_status" ON "public"."marketplace_sync_log" ("status");
CREATE INDEX IF NOT EXISTS "idx_marketplace_sync_log_started_at" ON "public"."marketplace_sync_log" ("started_at" DESC);

-- 添加注释
COMMENT ON TABLE "public"."marketplace_sync_log" IS '技能市场同步日志表';
COMMENT ON COLUMN "public"."marketplace_sync_log"."id" IS '主键 ID';
COMMENT ON COLUMN "public"."marketplace_sync_log"."sync_type" IS '同步类型 (skill:技能:blue/template:模板:cyan/full:全量:purple)';
COMMENT ON COLUMN "public"."marketplace_sync_log"."source" IS '同步来源 (github:GitHub:blue/clawhub:ClawHub:green/webhook:Webhook:purple/manual:手动:gray)';
COMMENT ON COLUMN "public"."marketplace_sync_log"."status" IS '同步状态 (running:运行中:blue/success:成功:green/failed:失败:red/partial:部分成功:orange)';
COMMENT ON COLUMN "public"."marketplace_sync_log"."total_count" IS '总数量';
COMMENT ON COLUMN "public"."marketplace_sync_log"."success_count" IS '成功数量';
COMMENT ON COLUMN "public"."marketplace_sync_log"."failed_count" IS '失败数量';
COMMENT ON COLUMN "public"."marketplace_sync_log"."error_message" IS '错误信息';
COMMENT ON COLUMN "public"."marketplace_sync_log"."started_at" IS '开始时间';
COMMENT ON COLUMN "public"."marketplace_sync_log"."completed_at" IS '完成时间';

-- ============================================
-- 6. 创建 marketplace_download 表（如果不存在）
-- ============================================
CREATE TABLE IF NOT EXISTS "public"."marketplace_download" (
  "id" bigserial PRIMARY KEY,
  "user_id" int8 NOT NULL,
  "resource_type" varchar(20) COLLATE "pg_catalog"."default" NOT NULL,
  "resource_id" varchar(255) COLLATE "pg_catalog"."default" NOT NULL,
  "resource_name" varchar(200) COLLATE "pg_catalog"."default",
  "version" varchar(50) COLLATE "pg_catalog"."default" NOT NULL,
  "download_source" varchar(50) COLLATE "pg_catalog"."default",
  "ip_address" varchar(50) COLLATE "pg_catalog"."default",
  "user_agent" text COLLATE "pg_catalog"."default",
  "downloaded_at" timestamptz(6) NOT NULL DEFAULT NOW(),
  "created_time" timestamptz(6) NOT NULL DEFAULT NOW(),
  "updated_time" timestamptz(6)
);

-- 创建索引（如果不存在）
CREATE INDEX IF NOT EXISTS "idx_marketplace_download_user_id" ON "public"."marketplace_download" ("user_id");
CREATE INDEX IF NOT EXISTS "idx_marketplace_download_resource" ON "public"."marketplace_download" ("resource_type", "resource_id");
CREATE INDEX IF NOT EXISTS "idx_marketplace_download_downloaded_at" ON "public"."marketplace_download" ("downloaded_at" DESC);

-- 添加注释
COMMENT ON TABLE "public"."marketplace_download" IS '用户下载记录表';
COMMENT ON COLUMN "public"."marketplace_download"."id" IS '主键 ID';
COMMENT ON COLUMN "public"."marketplace_download"."user_id" IS '用户ID';
COMMENT ON COLUMN "public"."marketplace_download"."resource_type" IS '资源类型 (skill:技能:blue/template:模板:cyan)';
COMMENT ON COLUMN "public"."marketplace_download"."resource_id" IS '资源 ID';
COMMENT ON COLUMN "public"."marketplace_download"."resource_name" IS '资源名称';
COMMENT ON COLUMN "public"."marketplace_download"."version" IS '下载的版本';
COMMENT ON COLUMN "public"."marketplace_download"."download_source" IS '下载来源（web/api/cli）';
COMMENT ON COLUMN "public"."marketplace_download"."ip_address" IS 'IP 地址';
COMMENT ON COLUMN "public"."marketplace_download"."user_agent" IS 'User Agent';
COMMENT ON COLUMN "public"."marketplace_download"."downloaded_at" IS '下载时间';

COMMIT;

-- ============================================
-- 迁移完成
-- ============================================
