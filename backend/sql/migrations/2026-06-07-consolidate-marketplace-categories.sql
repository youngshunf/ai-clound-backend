-- 技能市场分类按领域合并：21 个细分类 → 12 个领域 + 其他。
-- 背景：媒体被拆成 媒体/图片/音频/视频、数据拆成 分析/处理、内容拆成 内容创作/写作助手、
--       工具拆成 效率/实用/自动化、沟通拆成 沟通协作/社交媒体，chip 过多过细。
-- 事实源：app/marketplace/service/category_taxonomy.py（CANONICAL_CATEGORIES + CATEGORY_ALIASES）。
-- 种子：sql/tables/marketplace_category.sql 已同步为同一套 13 行。
-- 幂等：可重复执行（re-map 用 IN 集合、taxonomy 用 upsert、清理用 NOT IN 白名单）。

BEGIN;

-- 1) 重映射存量技能行到权威 slug（合并近义 + 兜底 NULL→other）
UPDATE marketplace_skill SET category = 'content-creation' WHERE category IN ('writing', 'content');
UPDATE marketplace_skill SET category = 'creativity'       WHERE category IN ('creative', 'design');
UPDATE marketplace_skill SET category = 'media'            WHERE category IN ('image', 'audio', 'video');
UPDATE marketplace_skill SET category = 'development'      WHERE category IN ('developer', 'dev');
UPDATE marketplace_skill SET category = 'data-analysis'   WHERE category IN ('data', 'analytics');
UPDATE marketplace_skill SET category = 'productivity'    WHERE category IN ('utility', 'efficiency', 'automation', 'tools');
UPDATE marketplace_skill SET category = 'communication'   WHERE category IN ('social', 'marketing');
UPDATE marketplace_skill SET category = 'other'           WHERE category IN ('official', 'agent', 'misc');
UPDATE marketplace_skill SET category = 'other'           WHERE category IS NULL OR btrim(category) = '';

-- 2) 模板同样重映射（分身模板的 'agent' 非领域，归入 other；NULL→other）
UPDATE marketplace_template SET category = 'content-creation' WHERE category IN ('writing', 'content');
UPDATE marketplace_template SET category = 'creativity'       WHERE category IN ('creative', 'design');
UPDATE marketplace_template SET category = 'media'            WHERE category IN ('image', 'audio', 'video');
UPDATE marketplace_template SET category = 'development'      WHERE category IN ('developer', 'dev');
UPDATE marketplace_template SET category = 'data-analysis'   WHERE category IN ('data', 'analytics');
UPDATE marketplace_template SET category = 'productivity'    WHERE category IN ('utility', 'efficiency', 'automation', 'tools');
UPDATE marketplace_template SET category = 'communication'   WHERE category IN ('social', 'marketing');
UPDATE marketplace_template SET category = 'other'           WHERE category IN ('official', 'agent', 'misc');
UPDATE marketplace_template SET category = 'other'           WHERE category IS NULL OR btrim(category) = '';

-- 3) Upsert 权威 12 领域 + 其他（名称/图标/排序对齐 category_taxonomy.py）
INSERT INTO marketplace_category (slug, name, icon, parent_slug, sort_order, created_time, updated_time)
VALUES
  ('content-creation', '内容创作', '✍️', NULL, 1,  NOW(), NOW()),
  ('creativity',       '设计创意', '🎨', NULL, 2,  NOW(), NOW()),
  ('media',            '媒体处理', '🎬', NULL, 3,  NOW(), NOW()),
  ('development',      '开发工具', '💻', NULL, 4,  NOW(), NOW()),
  ('data-analysis',    '数据分析', '📊', NULL, 5,  NOW(), NOW()),
  ('productivity',     '效率办公', '⚡', NULL, 6,  NOW(), NOW()),
  ('ai-assistant',     'AI 助手', '🤖', NULL, 7,  NOW(), NOW()),
  ('communication',    '沟通社交', '💬', NULL, 8,  NOW(), NOW()),
  ('search',           '搜索检索', '🔍', NULL, 9,  NOW(), NOW()),
  ('finance',          '金融理财', '💰', NULL, 10, NOW(), NOW()),
  ('health',           '健康医疗', '🏥', NULL, 11, NOW(), NOW()),
  ('entertainment',    '娱乐休闲', '🎮', NULL, 12, NOW(), NOW()),
  ('other',            '其他',     '📦', NULL, 99, NOW(), NOW())
ON CONFLICT (slug) DO UPDATE SET
  name = EXCLUDED.name,
  icon = EXCLUDED.icon,
  parent_slug = EXCLUDED.parent_slug,
  sort_order = EXCLUDED.sort_order,
  updated_time = NOW();

-- 4) 删除合并掉的旧分类行（白名单之外的细分类：writing/data/image/audio/video/social/
--    utility/automation/efficiency/design/marketing/developer/official/agent 等）。
--    存量技能已在步骤 1/2 重指向白名单，删除后不会产生孤儿 chip。
DELETE FROM marketplace_category WHERE slug NOT IN (
  'content-creation', 'creativity', 'media', 'development', 'data-analysis',
  'productivity', 'ai-assistant', 'communication', 'search', 'finance',
  'health', 'entertainment', 'other'
);

COMMIT;
