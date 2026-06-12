-- =====================================================
--  菜单初始化 SQL (MySQL)
-- 自动生成于: 2026-05-10 21:06:33.393184+08:00
-- 支持幂等操作：已存在则更新，不存在则新增
-- =====================================================

-- 查找父级目录菜单 (path = /lead_automation)
SET @parent_id = (SELECT id FROM sys_menu WHERE path = '/lead_automation' AND type = 0 ORDER BY id LIMIT 1);

-- 如果父级目录不存在，创建它
INSERT INTO sys_menu (title, name, path, sort, icon, type, component, perms, status, display, cache, link, remark, parent_id, created_time, updated_time)
SELECT '', 'Lead_automation', '/lead_automation', 1, 'lucide:folder', 0, 'BasicLayout', NULL, 1, 1, 1, '', 'lead_automation模块', NULL, NOW(), NULL
FROM DUAL WHERE @parent_id IS NULL;

-- 重新获取父级目录 ID
SET @parent_id = COALESCE(@parent_id, LAST_INSERT_ID());

-- 查找主菜单 (path = /lead_automation/lead_source_config)
SET @menu_id = (SELECT id FROM sys_menu WHERE path = '/lead_automation/lead_source_config' AND type = 1 ORDER BY id LIMIT 1);

-- 如果主菜单不存在，创建它
INSERT INTO sys_menu (title, name, path, sort, icon, type, component, perms, status, display, cache, link, remark, parent_id, created_time, updated_time)
SELECT '', 'LeadSourceConfig', '/lead_automation/lead_source_config', 1, 'lucide:list', 1, '/lead_automation/lead_source_config/index', NULL, 1, 1, 1, '', 'AI lead automation source configuration', @parent_id, NOW(), NULL
FROM DUAL WHERE @menu_id IS NULL;

-- 如果已存在，更新它
UPDATE sys_menu SET
    title = '',
    name = 'LeadSourceConfig',
    component = '/lead_automation/lead_source_config/index',
    remark = 'AI lead automation source configuration',
    parent_id = @parent_id,
    updated_time = NOW()
WHERE id = @menu_id AND @menu_id IS NOT NULL;

-- 重新获取菜单 ID
SET @menu_id = COALESCE(@menu_id, LAST_INSERT_ID());

-- 新增按钮（不存在则插入）
INSERT INTO sys_menu (title, name, path, sort, icon, type, component, perms, status, display, cache, link, remark, parent_id, created_time, updated_time)
SELECT '新增', 'AddLeadSourceConfig', NULL, 1, NULL, 2, NULL, 'lead:source:config:add', 1, 0, 1, '', NULL, @menu_id, NOW(), NULL
FROM DUAL WHERE NOT EXISTS (SELECT 1 FROM sys_menu WHERE perms = 'lead:source:config:add' AND parent_id = @menu_id);

-- 编辑按钮
INSERT INTO sys_menu (title, name, path, sort, icon, type, component, perms, status, display, cache, link, remark, parent_id, created_time, updated_time)
SELECT '编辑', 'EditLeadSourceConfig', NULL, 2, NULL, 2, NULL, 'lead:source:config:edit', 1, 0, 1, '', NULL, @menu_id, NOW(), NULL
FROM DUAL WHERE NOT EXISTS (SELECT 1 FROM sys_menu WHERE perms = 'lead:source:config:edit' AND parent_id = @menu_id);

-- 删除按钮
INSERT INTO sys_menu (title, name, path, sort, icon, type, component, perms, status, display, cache, link, remark, parent_id, created_time, updated_time)
SELECT '删除', 'DeleteLeadSourceConfig', NULL, 3, NULL, 2, NULL, 'lead:source:config:del', 1, 0, 1, '', NULL, @menu_id, NOW(), NULL
FROM DUAL WHERE NOT EXISTS (SELECT 1 FROM sys_menu WHERE perms = 'lead:source:config:del' AND parent_id = @menu_id);

-- 查看按钮
INSERT INTO sys_menu (title, name, path, sort, icon, type, component, perms, status, display, cache, link, remark, parent_id, created_time, updated_time)
SELECT '查看', 'ViewLeadSourceConfig', NULL, 4, NULL, 2, NULL, 'lead:source:config:get', 1, 0, 1, '', NULL, @menu_id, NOW(), NULL
FROM DUAL WHERE NOT EXISTS (SELECT 1 FROM sys_menu WHERE perms = 'lead:source:config:get' AND parent_id = @menu_id);

-- =====================================================
-- 菜单生成完成
-- =====================================================
