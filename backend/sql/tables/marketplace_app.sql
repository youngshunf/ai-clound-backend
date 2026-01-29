-- 技能市场应用表
CREATE TABLE "public"."marketplace_app" (
  "id" bigserial PRIMARY KEY,
  "app_id" varchar(100) COLLATE "pg_catalog"."default" NOT NULL,
  "name" varchar(200) COLLATE "pg_catalog"."default" NOT NULL,
  "description" text COLLATE "pg_catalog"."default",
  "icon_url" varchar(500) COLLATE "pg_catalog"."default",
  "author_id" int8,
  "author_name" varchar(100) COLLATE "pg_catalog"."default",
  "pricing_type" varchar(20) COLLATE "pg_catalog"."default" NOT NULL DEFAULT 'free',
  "price" numeric(10, 2) NOT NULL DEFAULT 0,
  "is_private" bool NOT NULL DEFAULT false,
  "is_official" bool NOT NULL DEFAULT false,
  "download_count" int4 NOT NULL DEFAULT 0,
  "skill_dependencies" text COLLATE "pg_catalog"."default",
  "created_time" timestamptz(6) NOT NULL DEFAULT NOW(),
  "updated_time" timestamptz(6),
  UNIQUE("app_id")
);

CREATE INDEX "idx_marketplace_app_author_id" ON "public"."marketplace_app" ("author_id");
CREATE INDEX "idx_marketplace_app_pricing_type" ON "public"."marketplace_app" ("pricing_type");
CREATE INDEX "idx_marketplace_app_download_count" ON "public"."marketplace_app" ("download_count" DESC);

COMMENT ON COLUMN "public"."marketplace_app"."id" IS '主键 ID';
COMMENT ON COLUMN "public"."marketplace_app"."app_id" IS '应用唯一标识';
COMMENT ON COLUMN "public"."marketplace_app"."name" IS '应用名称';
COMMENT ON COLUMN "public"."marketplace_app"."description" IS '应用描述';
COMMENT ON COLUMN "public"."marketplace_app"."icon_url" IS '应用图标URL';
COMMENT ON COLUMN "public"."marketplace_app"."author_id" IS '作者用户ID';
COMMENT ON COLUMN "public"."marketplace_app"."author_name" IS '作者名称';
COMMENT ON COLUMN "public"."marketplace_app"."pricing_type" IS '定价类型 (free:免费:green/paid:付费:orange/subscription:订阅:blue)';
COMMENT ON COLUMN "public"."marketplace_app"."price" IS '价格';
COMMENT ON COLUMN "public"."marketplace_app"."is_private" IS '是否私有';
COMMENT ON COLUMN "public"."marketplace_app"."is_official" IS '是否官方应用';
COMMENT ON COLUMN "public"."marketplace_app"."download_count" IS '下载次数';
COMMENT ON COLUMN "public"."marketplace_app"."skill_dependencies" IS '依赖的技能ID列表，逗号分隔';
COMMENT ON TABLE "public"."marketplace_app" IS '技能市场应用表';
