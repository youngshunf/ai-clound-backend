-- =====================================================
--  菜单初始化 SQL (MySQL)
-- 自动生成于: 2026-01-28 20:42:38.306204+08:00
-- 支持幂等操作：已存在则更新，不存在则新增
-- =====================================================

-- 查找父级目录菜单 (path = /marketplace)
SET @parent_id = (SELECT id FROM sys_menu WHERE path = '/marketplace' AND type = 0 ORDER BY id LIMIT 1);

-- 如果父级目录不存在，创建它
INSERT INTO sys_menu (title, name, path, sort, icon, type, component, perms, status, display, cache, link, remark, parent_id, created_time, updated_time)
SELECT '', 'Marketplace', '/marketplace', 1, 'lucide:folder', 0, 'BasicLayout', NULL, 1, 1, 1, '', 'marketplace模块', NULL, NOW(), NULL
FROM DUAL WHERE @parent_id IS NULL;

-- 重新获取父级目录 ID
SET @parent_id = COALESCE(@parent_id, LAST_INSERT_ID());

-- 查找主菜单 (path = /marketplace/marketplace_category)
SET @menu_id = (SELECT id FROM sys_menu WHERE path = '/marketplace/marketplace_category' AND type = 1 ORDER BY id LIMIT 1);

-- 如果主菜单不存在，创建它
INSERT INTO sys_menu (title, name, path, sort, icon, type, component, perms, status, display, cache, link, remark, parent_id, created_time, updated_time)
SELECT '', 'MarketplaceCategory', '/marketplace/marketplace_category', 1, 'lucide:list', 1, '/marketplace/marketplace_category/index', NULL, 1, 1, 1, '', '技能市场分类', @parent_id, NOW(), NULL
FROM DUAL WHERE @menu_id IS NULL;

-- 如果已存在，更新它
UPDATE sys_menu SET
    title = '',
    name = 'MarketplaceCategory',
    component = '/marketplace/marketplace_category/index',
    remark = '技能市场分类',
    parent_id = @parent_id,
    updated_time = NOW()
WHERE id = @menu_id AND @menu_id IS NOT NULL;

-- 重新获取菜单 ID
SET @menu_id = COALESCE(@menu_id, LAST_INSERT_ID());

-- 新增按钮（不存在则插入）
INSERT INTO sys_menu (title, name, path, sort, icon, type, component, perms, status, display, cache, link, remark, parent_id, created_time, updated_time)
SELECT '新增', 'AddMarketplaceCategory', NULL, 1, NULL, 2, NULL, 'marketplace:category:add', 1, 0, 1, '', NULL, @menu_id, NOW(), NULL
FROM DUAL WHERE NOT EXISTS (SELECT 1 FROM sys_menu WHERE perms = 'marketplace:category:add' AND parent_id = @menu_id);

-- 编辑按钮
INSERT INTO sys_menu (title, name, path, sort, icon, type, component, perms, status, display, cache, link, remark, parent_id, created_time, updated_time)
SELECT '编辑', 'EditMarketplaceCategory', NULL, 2, NULL, 2, NULL, 'marketplace:category:edit', 1, 0, 1, '', NULL, @menu_id, NOW(), NULL
FROM DUAL WHERE NOT EXISTS (SELECT 1 FROM sys_menu WHERE perms = 'marketplace:category:edit' AND parent_id = @menu_id);

-- 删除按钮
INSERT INTO sys_menu (title, name, path, sort, icon, type, component, perms, status, display, cache, link, remark, parent_id, created_time, updated_time)
SELECT '删除', 'DeleteMarketplaceCategory', NULL, 3, NULL, 2, NULL, 'marketplace:category:del', 1, 0, 1, '', NULL, @menu_id, NOW(), NULL
FROM DUAL WHERE NOT EXISTS (SELECT 1 FROM sys_menu WHERE perms = 'marketplace:category:del' AND parent_id = @menu_id);

-- 查看按钮
INSERT INTO sys_menu (title, name, path, sort, icon, type, component, perms, status, display, cache, link, remark, parent_id, created_time, updated_time)
SELECT '查看', 'ViewMarketplaceCategory', NULL, 4, NULL, 2, NULL, 'marketplace:category:get', 1, 0, 1, '', NULL, @menu_id, NOW(), NULL
FROM DUAL WHERE NOT EXISTS (SELECT 1 FROM sys_menu WHERE perms = 'marketplace:category:get' AND parent_id = @menu_id);

-- =====================================================
-- 菜单生成完成
-- =====================================================
