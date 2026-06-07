-- 技能市场分类表
CREATE TABLE "public"."marketplace_category" (
  "id" bigserial PRIMARY KEY,
  "slug" varchar(50) COLLATE "pg_catalog"."default" NOT NULL,
  "name" varchar(100) COLLATE "pg_catalog"."default" NOT NULL,
  "icon" varchar(20) COLLATE "pg_catalog"."default",
  "parent_slug" varchar(50) COLLATE "pg_catalog"."default",
  "sort_order" int4 NOT NULL DEFAULT 0,
  "created_time" timestamptz(6) NOT NULL DEFAULT NOW(),
  "updated_time" timestamptz(6),
  UNIQUE("slug")
);

COMMENT ON COLUMN "public"."marketplace_category"."id" IS '主键 ID';
COMMENT ON COLUMN "public"."marketplace_category"."slug" IS '分类标识';
COMMENT ON COLUMN "public"."marketplace_category"."name" IS '分类名称';
COMMENT ON COLUMN "public"."marketplace_category"."icon" IS 'emoji图标';
COMMENT ON COLUMN "public"."marketplace_category"."parent_slug" IS '父分类标识';
COMMENT ON COLUMN "public"."marketplace_category"."sort_order" IS '排序顺序';
COMMENT ON TABLE "public"."marketplace_category" IS '技能市场分类表';

-- 初始分类数据：按领域合并后的权威 12 领域 + 其他（事实源 category_taxonomy.py）。
-- 历史细分类（writing/data/image/audio/video/social/utility/automation/...）已于
-- 2026-06-07-consolidate-marketplace-categories.sql 合并迁移，不再种子。
INSERT INTO "public"."marketplace_category" (slug, name, icon, parent_slug, sort_order) VALUES
  ('content-creation', '内容创作', '✍️', NULL, 1),
  ('creativity', '设计创意', '🎨', NULL, 2),
  ('media', '媒体处理', '🎬', NULL, 3),
  ('development', '开发工具', '💻', NULL, 4),
  ('data-analysis', '数据分析', '📊', NULL, 5),
  ('productivity', '效率办公', '⚡', NULL, 6),
  ('ai-assistant', 'AI 助手', '🤖', NULL, 7),
  ('communication', '沟通社交', '💬', NULL, 8),
  ('search', '搜索检索', '🔍', NULL, 9),
  ('finance', '金融理财', '💰', NULL, 10),
  ('health', '健康医疗', '🏥', NULL, 11),
  ('entertainment', '娱乐休闲', '🎮', NULL, 12),
  ('other', '其他', '📦', NULL, 99)
ON CONFLICT (slug) DO UPDATE SET
  name = EXCLUDED.name,
  icon = EXCLUDED.icon,
  sort_order = EXCLUDED.sort_order,
  updated_time = NOW();
