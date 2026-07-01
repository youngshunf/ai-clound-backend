-- 技能市场分类表
CREATE TABLE IF NOT EXISTS "public"."marketplace_category" (
  "id" bigserial PRIMARY KEY,
  "slug" varchar(50) NOT NULL UNIQUE,
  "name" varchar(100) NOT NULL,
  "name_en" varchar(100),
  "icon" varchar(20),
  "description" text,
  "parent_slug" varchar(50),
  "sort_order" int4 DEFAULT 0,
  "skill_count" int4 DEFAULT 0,
  "created_time" timestamptz(6) DEFAULT CURRENT_TIMESTAMP,
  "updated_time" timestamptz(6)
);

-- 索引
CREATE INDEX IF NOT EXISTS idx_category_parent ON marketplace_category(parent_slug);
CREATE INDEX IF NOT EXISTS idx_category_sort ON marketplace_category(sort_order);

-- 字段注释
COMMENT ON TABLE "public"."marketplace_category" IS '技能市场分类表';
COMMENT ON COLUMN "public"."marketplace_category"."id" IS '主键ID';
COMMENT ON COLUMN "public"."marketplace_category"."slug" IS '分类标识（唯一）';
COMMENT ON COLUMN "public"."marketplace_category"."name" IS '分类名称（中文）';
COMMENT ON COLUMN "public"."marketplace_category"."name_en" IS '分类名称（英文）';
COMMENT ON COLUMN "public"."marketplace_category"."icon" IS 'emoji图标';
COMMENT ON COLUMN "public"."marketplace_category"."description" IS '分类描述';
COMMENT ON COLUMN "public"."marketplace_category"."parent_slug" IS '父分类标识（支持层级）';
COMMENT ON COLUMN "public"."marketplace_category"."sort_order" IS '排序顺序';
COMMENT ON COLUMN "public"."marketplace_category"."skill_count" IS '技能数量（冗余字段）';
COMMENT ON COLUMN "public"."marketplace_category"."created_time" IS '创建时间';
COMMENT ON COLUMN "public"."marketplace_category"."updated_time" IS '更新时间';
