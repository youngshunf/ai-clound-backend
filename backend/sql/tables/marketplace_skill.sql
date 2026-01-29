-- 技能市场技能表
CREATE TABLE "public"."marketplace_skill" (
  "id" bigserial PRIMARY KEY,
  "skill_id" varchar(100) COLLATE "pg_catalog"."default" NOT NULL,
  "name" varchar(200) COLLATE "pg_catalog"."default" NOT NULL,
  "description" text COLLATE "pg_catalog"."default",
  "icon_url" varchar(500) COLLATE "pg_catalog"."default",
  "author_id" int8,
  "author_name" varchar(100) COLLATE "pg_catalog"."default",
  "category" varchar(50) COLLATE "pg_catalog"."default",
  "tags" varchar(500) COLLATE "pg_catalog"."default",
  "pricing_type" varchar(20) COLLATE "pg_catalog"."default" NOT NULL DEFAULT 'free',
  "price" numeric(10, 2) NOT NULL DEFAULT 0,
  "is_private" bool NOT NULL DEFAULT false,
  "is_official" bool NOT NULL DEFAULT false,
  "download_count" int4 NOT NULL DEFAULT 0,
  "created_time" timestamptz(6) NOT NULL DEFAULT NOW(),
  "updated_time" timestamptz(6),
  UNIQUE("skill_id")
);

CREATE INDEX "idx_marketplace_skill_category" ON "public"."marketplace_skill" ("category");
CREATE INDEX "idx_marketplace_skill_author_id" ON "public"."marketplace_skill" ("author_id");
CREATE INDEX "idx_marketplace_skill_pricing_type" ON "public"."marketplace_skill" ("pricing_type");
CREATE INDEX "idx_marketplace_skill_download_count" ON "public"."marketplace_skill" ("download_count" DESC);

COMMENT ON COLUMN "public"."marketplace_skill"."id" IS '主键 ID';
COMMENT ON COLUMN "public"."marketplace_skill"."skill_id" IS '技能唯一标识';
COMMENT ON COLUMN "public"."marketplace_skill"."name" IS '技能名称';
COMMENT ON COLUMN "public"."marketplace_skill"."description" IS '技能描述';
COMMENT ON COLUMN "public"."marketplace_skill"."icon_url" IS 'SVG图标URL';
COMMENT ON COLUMN "public"."marketplace_skill"."author_id" IS '作者用户ID';
COMMENT ON COLUMN "public"."marketplace_skill"."author_name" IS '作者名称';
COMMENT ON COLUMN "public"."marketplace_skill"."category" IS '分类';
COMMENT ON COLUMN "public"."marketplace_skill"."tags" IS '标签，逗号分隔';
COMMENT ON COLUMN "public"."marketplace_skill"."pricing_type" IS '定价类型 (free:免费:green/paid:付费:orange)';
COMMENT ON COLUMN "public"."marketplace_skill"."price" IS '价格';
COMMENT ON COLUMN "public"."marketplace_skill"."is_private" IS '是否私有';
COMMENT ON COLUMN "public"."marketplace_skill"."is_official" IS '是否官方技能';
COMMENT ON COLUMN "public"."marketplace_skill"."download_count" IS '下载次数';
COMMENT ON TABLE "public"."marketplace_skill" IS '技能市场技能表';
