-- 技能市场技能表（支持多语言）
CREATE TABLE IF NOT EXISTS "public"."marketplace_skill" (
  "id" bigserial PRIMARY KEY,
  "skill_id" varchar(100) NOT NULL UNIQUE,
  "name_en" varchar(200),
  "name_zh" varchar(200),
  "description_en" text,
  "description_zh" text,
  "source_language" varchar(10),
  "icon_url" varchar(500),
  "emoji" varchar(20),
  "author_id" int8,
  "author_name" varchar(100),
  "category" varchar(50),
  "tags" text,
  "tags_en" text,
  "tags_zh" text,
  "pricing_type" varchar(20) DEFAULT 'free',
  "price" numeric(10, 2) DEFAULT 0,
  "is_private" bool DEFAULT false,
  "is_official" bool DEFAULT false,
  "download_count" int4 DEFAULT 0,
  "star_count" int4 DEFAULT 0,
  "repo_path" varchar(500),
  "git_commit_hash" varchar(64),
  "synced_at" timestamptz(6),
  "translated_at" timestamptz(6),
  "created_time" timestamptz(6) DEFAULT CURRENT_TIMESTAMP,
  "updated_time" timestamptz(6)
);

-- 索引
CREATE INDEX IF NOT EXISTS idx_marketplace_skill_category ON marketplace_skill(category);
CREATE INDEX IF NOT EXISTS idx_marketplace_skill_author ON marketplace_skill(author_id);
CREATE INDEX IF NOT EXISTS idx_marketplace_skill_downloads ON marketplace_skill(download_count DESC);
CREATE INDEX IF NOT EXISTS idx_marketplace_skill_synced ON marketplace_skill(synced_at);
CREATE INDEX IF NOT EXISTS idx_marketplace_skill_name_zh ON marketplace_skill USING gin(to_tsvector('chinese', COALESCE(name_zh, '')));
CREATE INDEX IF NOT EXISTS idx_marketplace_skill_desc_zh ON marketplace_skill USING gin(to_tsvector('chinese', COALESCE(description_zh, '')));

-- 字段注释
COMMENT ON TABLE "public"."marketplace_skill" IS '技能市场技能表';
COMMENT ON COLUMN "public"."marketplace_skill"."id" IS '主键ID';
COMMENT ON COLUMN "public"."marketplace_skill"."skill_id" IS '技能唯一标识';
COMMENT ON COLUMN "public"."marketplace_skill"."name_en" IS '英文名称';
COMMENT ON COLUMN "public"."marketplace_skill"."name_zh" IS '中文名称';
COMMENT ON COLUMN "public"."marketplace_skill"."description_en" IS '英文描述';
COMMENT ON COLUMN "public"."marketplace_skill"."description_zh" IS '中文描述';
COMMENT ON COLUMN "public"."marketplace_skill"."source_language" IS '原始语言 (en/zh)';
COMMENT ON COLUMN "public"."marketplace_skill"."icon_url" IS 'SVG图标URL';
COMMENT ON COLUMN "public"."marketplace_skill"."emoji" IS 'emoji图标';
COMMENT ON COLUMN "public"."marketplace_skill"."author_id" IS '作者用户ID';
COMMENT ON COLUMN "public"."marketplace_skill"."author_name" IS '作者名称';
COMMENT ON COLUMN "public"."marketplace_skill"."category" IS '分类';
COMMENT ON COLUMN "public"."marketplace_skill"."tags" IS '默认标签，JSON数组字符串';
COMMENT ON COLUMN "public"."marketplace_skill"."tags_en" IS '英文标签，JSON数组字符串';
COMMENT ON COLUMN "public"."marketplace_skill"."tags_zh" IS '中文标签，JSON数组字符串';
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
COMMENT ON COLUMN "public"."marketplace_skill"."created_time" IS '创建时间';
COMMENT ON COLUMN "public"."marketplace_skill"."updated_time" IS '更新时间';
