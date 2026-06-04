-- 补齐 huanxing-skills 目录名对应的市场分类，并合并近义分类
-- 背景：huanxing 技能 category 取自目录名（finance/health/social/utility/search/
--       developer/creative/official），其中 5 个不在 marketplace_category 表里，
--       另外 3 个与既有分类近义。详见 github_sync_service.HUANXING_CATEGORY_ALIASES。
-- 幂等：可重复执行。

-- 1) 新增 5 个真新分类（finance/health/social/utility/search）
INSERT INTO marketplace_category (slug, name, icon, parent_slug, sort_order, created_time, updated_time)
SELECT v.slug, v.name, v.icon, NULL, v.sort_order, NOW(), NOW()
FROM (VALUES
    ('finance', '金融理财', '💰', 30),
    ('health',  '健康医疗', '🏥', 31),
    ('social',  '社交媒体', '💬', 32),
    ('utility', '实用工具', '🔧', 33),
    ('search',  '搜索检索', '🔍', 34)
) AS v(slug, name, icon, sort_order)
WHERE NOT EXISTS (
    SELECT 1 FROM marketplace_category c WHERE c.slug = v.slug
);

-- 2) 合并近义/非域分类到既有 slug（修正存量技能行）
UPDATE marketplace_skill SET category = 'development' WHERE category = 'developer';
UPDATE marketplace_skill SET category = 'creativity'  WHERE category = 'creative';
UPDATE marketplace_skill SET category = 'other'       WHERE category = 'official';
