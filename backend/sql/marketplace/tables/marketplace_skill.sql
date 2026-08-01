-- 技能市场技能表
CREATE TABLE "public"."marketplace_skill" (
  "id" bigserial PRIMARY KEY,
  "skill_id" varchar(255) COLLATE "pg_catalog"."default" NOT NULL,
  "namespace" varchar(160) COLLATE "pg_catalog"."default",
  "slug" varchar(100) COLLATE "pg_catalog"."default",
  "user_id" int8,
  "hasn_id" varchar(40) COLLATE "pg_catalog"."default",
  "status" varchar(20) COLLATE "pg_catalog"."default" NOT NULL DEFAULT 'published',
  "visibility" varchar(20) COLLATE "pg_catalog"."default" NOT NULL DEFAULT 'public',
  "requested_visibility" varchar(20) COLLATE "pg_catalog"."default" NOT NULL DEFAULT 'private',
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
  "tags" text COLLATE "pg_catalog"."default",
  "tags_en" text COLLATE "pg_catalog"."default",
  "tags_zh" text COLLATE "pg_catalog"."default",
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
  "updated_time" timestamptz(6),
  UNIQUE("skill_id")
);

CREATE INDEX "idx_marketplace_skill_category" ON "public"."marketplace_skill" ("category");
CREATE INDEX "idx_marketplace_skill_author_id" ON "public"."marketplace_skill" ("author_id");
CREATE INDEX "idx_marketplace_skill_user_id" ON "public"."marketplace_skill" ("user_id");
CREATE INDEX "idx_marketplace_skill_hasn_id" ON "public"."marketplace_skill" ("hasn_id");
CREATE INDEX "idx_marketplace_skill_status_visibility" ON "public"."marketplace_skill" ("status", "visibility");
CREATE INDEX "idx_marketplace_skill_pricing_type" ON "public"."marketplace_skill" ("pricing_type");
CREATE INDEX "idx_marketplace_skill_download_count" ON "public"."marketplace_skill" ("download_count" DESC);
CREATE INDEX "idx_marketplace_skill_source_type" ON "public"."marketplace_skill" ("source_type");
CREATE UNIQUE INDEX "idx_marketplace_skill_namespace_slug" ON "public"."marketplace_skill" ("namespace", "slug");

COMMENT ON COLUMN "public"."marketplace_skill"."id" IS '主键 ID';
COMMENT ON COLUMN "public"."marketplace_skill"."skill_id" IS '技能唯一标识';
COMMENT ON COLUMN "public"."marketplace_skill"."namespace" IS '命名空间（如 huanxing/clawhub）';
COMMENT ON COLUMN "public"."marketplace_skill"."slug" IS '技能标识符（如 translator-pro）';
COMMENT ON COLUMN "public"."marketplace_skill"."user_id" IS '资源所有者用户ID';
COMMENT ON COLUMN "public"."marketplace_skill"."hasn_id" IS '资源所有者 HASN ID';
COMMENT ON COLUMN "public"."marketplace_skill"."status" IS '发布状态';
COMMENT ON COLUMN "public"."marketplace_skill"."visibility" IS '可见性';
COMMENT ON COLUMN "public"."marketplace_skill"."requested_visibility" IS '草稿期记录的期望可见性；审核完成前实际 visibility 恒为 private';
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
COMMENT ON COLUMN "public"."marketplace_skill"."tags" IS '默认标签，JSON数组字符串';
COMMENT ON COLUMN "public"."marketplace_skill"."tags_en" IS '英文标签，JSON数组字符串';
COMMENT ON COLUMN "public"."marketplace_skill"."tags_zh" IS '中文标签，JSON数组字符串';
COMMENT ON COLUMN "public"."marketplace_skill"."source_type" IS '来源类型 (huanxing:幻形自研:purple/github:GitHub:blue/clawhub:ClawHub:green)';
COMMENT ON COLUMN "public"."marketplace_skill"."source_repo_url" IS '源仓库 URL';
COMMENT ON COLUMN "public"."marketplace_skill"."source_repo_path" IS '仓库内路径（如 huanxing-skills/productivity/translator-pro）';
COMMENT ON COLUMN "public"."marketplace_skill"."pricing_type" IS '定价类型 (free:免费:green/paid:付费:orange)';
COMMENT ON COLUMN "public"."marketplace_skill"."price" IS '价格';
COMMENT ON COLUMN "public"."marketplace_skill"."is_private" IS '是否私有';
COMMENT ON COLUMN "public"."marketplace_skill"."is_official" IS '是否官方技能';
COMMENT ON COLUMN "public"."marketplace_skill"."download_count" IS '下载次数';
COMMENT ON COLUMN "public"."marketplace_skill"."star_count" IS '星标数';
COMMENT ON COLUMN "public"."marketplace_skill"."repo_path" IS '在 huanxing-hub 中的路径';
COMMENT ON COLUMN "public"."marketplace_skill"."git_commit_hash" IS '最新同步的 commit hash';
COMMENT ON COLUMN "public"."marketplace_skill"."synced_at" IS '最后同步时间';
COMMENT ON COLUMN "public"."marketplace_skill"."translated_at" IS '最后翻译时间';
COMMENT ON TABLE "public"."marketplace_skill" IS '技能市场技能表';
