-- =====================================================
--  菜单初始化 SQL (MySQL)
-- 自动生成于: 2026-01-28 17:37:49.661745+08:00
-- 支持幂等操作：已存在则更新，不存在则新增
-- =====================================================

-- 查找父级目录菜单 (path = /user_tier)
SET @parent_id = (SELECT id FROM sys_menu WHERE path = '/user_tier' AND type = 0 ORDER BY id LIMIT 1);

-- 如果父级目录不存在，创建它
INSERT INTO sys_menu (title, name, path, sort, icon, type, component, perms, status, display, cache, link, remark, parent_id, created_time, updated_time)
SELECT '', 'User_tier', '/user_tier', 1, 'lucide:folder', 0, 'BasicLayout', NULL, 1, 1, 1, '', 'user_tier模块', NULL, NOW(), NULL
FROM DUAL WHERE @parent_id IS NULL;

-- 重新获取父级目录 ID
SET @parent_id = COALESCE(@parent_id, LAST_INSERT_ID());

-- 查找主菜单 (path = /user_tier/model_credit_rate)
SET @menu_id = (SELECT id FROM sys_menu WHERE path = '/user_tier/model_credit_rate' AND type = 1 ORDER BY id LIMIT 1);

-- 如果主菜单不存在，创建它
INSERT INTO sys_menu (title, name, path, sort, icon, type, component, perms, status, display, cache, link, remark, parent_id, created_time, updated_time)
SELECT '', 'ModelCreditRate', '/user_tier/model_credit_rate', 1, 'lucide:list', 1, '/user_tier/model_credit_rate/index', NULL, 1, 1, 1, '', '模型积分费率', @parent_id, NOW(), NULL
FROM DUAL WHERE @menu_id IS NULL;

-- 如果已存在，更新它
UPDATE sys_menu SET
    title = '',
    name = 'ModelCreditRate',
    component = '/user_tier/model_credit_rate/index',
    remark = '模型积分费率',
    parent_id = @parent_id,
    updated_time = NOW()
WHERE id = @menu_id AND @menu_id IS NOT NULL;

-- 重新获取菜单 ID
SET @menu_id = COALESCE(@menu_id, LAST_INSERT_ID());

-- 新增按钮（不存在则插入）
INSERT INTO sys_menu (title, name, path, sort, icon, type, component, perms, status, display, cache, link, remark, parent_id, created_time, updated_time)
SELECT '新增', 'AddModelCreditRate', NULL, 1, NULL, 2, NULL, 'model:credit:rate:add', 1, 0, 1, '', NULL, @menu_id, NOW(), NULL
FROM DUAL WHERE NOT EXISTS (SELECT 1 FROM sys_menu WHERE perms = 'model:credit:rate:add' AND parent_id = @menu_id);

-- 编辑按钮
INSERT INTO sys_menu (title, name, path, sort, icon, type, component, perms, status, display, cache, link, remark, parent_id, created_time, updated_time)
SELECT '编辑', 'EditModelCreditRate', NULL, 2, NULL, 2, NULL, 'model:credit:rate:edit', 1, 0, 1, '', NULL, @menu_id, NOW(), NULL
FROM DUAL WHERE NOT EXISTS (SELECT 1 FROM sys_menu WHERE perms = 'model:credit:rate:edit' AND parent_id = @menu_id);

-- 删除按钮
INSERT INTO sys_menu (title, name, path, sort, icon, type, component, perms, status, display, cache, link, remark, parent_id, created_time, updated_time)
SELECT '删除', 'DeleteModelCreditRate', NULL, 3, NULL, 2, NULL, 'model:credit:rate:del', 1, 0, 1, '', NULL, @menu_id, NOW(), NULL
FROM DUAL WHERE NOT EXISTS (SELECT 1 FROM sys_menu WHERE perms = 'model:credit:rate:del' AND parent_id = @menu_id);

-- 查看按钮
INSERT INTO sys_menu (title, name, path, sort, icon, type, component, perms, status, display, cache, link, remark, parent_id, created_time, updated_time)
SELECT '查看', 'ViewModelCreditRate', NULL, 4, NULL, 2, NULL, 'model:credit:rate:get', 1, 0, 1, '', NULL, @menu_id, NOW(), NULL
FROM DUAL WHERE NOT EXISTS (SELECT 1 FROM sys_menu WHERE perms = 'model:credit:rate:get' AND parent_id = @menu_id);

-- =====================================================
-- 菜单生成完成
-- =====================================================
