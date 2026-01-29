-- 用户下载记录表
CREATE TABLE "public"."marketplace_download" (
  "id" bigserial PRIMARY KEY,
  "user_id" int8 NOT NULL,
  "item_type" varchar(20) COLLATE "pg_catalog"."default" NOT NULL,
  "item_id" varchar(100) COLLATE "pg_catalog"."default" NOT NULL,
  "version" varchar(50) COLLATE "pg_catalog"."default" NOT NULL,
  "downloaded_at" timestamptz(6) NOT NULL DEFAULT NOW(),
  "created_time" timestamptz(6) NOT NULL DEFAULT NOW(),
  "updated_time" timestamptz(6)
);

CREATE INDEX "idx_marketplace_download_user_id" ON "public"."marketplace_download" ("user_id");
CREATE INDEX "idx_marketplace_download_item" ON "public"."marketplace_download" ("item_type", "item_id");
CREATE INDEX "idx_marketplace_download_downloaded_at" ON "public"."marketplace_download" ("downloaded_at" DESC);

COMMENT ON COLUMN "public"."marketplace_download"."id" IS '主键 ID';
COMMENT ON COLUMN "public"."marketplace_download"."user_id" IS '用户ID';
COMMENT ON COLUMN "public"."marketplace_download"."item_type" IS '类型 (app:应用/skill:技能)';
COMMENT ON COLUMN "public"."marketplace_download"."item_id" IS '应用或技能ID';
COMMENT ON COLUMN "public"."marketplace_download"."version" IS '下载的版本';
COMMENT ON COLUMN "public"."marketplace_download"."downloaded_at" IS '下载时间';
COMMENT ON TABLE "public"."marketplace_download" IS '用户下载记录表';
