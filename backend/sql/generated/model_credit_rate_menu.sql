-- =====================================================
-- ModelCreditRate 菜单初始化 SQL (PostgreSQL)
-- 自动生成于: 2026-01-27 19:22:05.486571
-- =====================================================

-- 父级菜单
INSERT INTO sys_menu (title, name, path, sort, icon, type, component, perms, status, display, cache, link, remark, parent_id, created_time, updated_time)
VALUES
('ModelCreditRate', 'ModelCreditRate', '/llm', 1, 'lucide:list', 1, '#/views/llm/index.vue', NULL, 1, 1, 1, '', 'ModelCreditRate管理', NULL, NOW(), NULL)
RETURNING id AS parent_menu_id;

-- 获取刚插入的父菜单 ID（存储到变量中用于后续按钮菜单）
DO $$
DECLARE
    v_parent_menu_id INTEGER;
BEGIN
    -- 获取刚插入的父菜单 ID
    SELECT id INTO v_parent_menu_id FROM sys_menu 
    WHERE name = 'ModelCreditRate' AND path = '/llm'
    ORDER BY id DESC LIMIT 1;

    -- 新增按钮
    INSERT INTO sys_menu (title, name, path, sort, icon, type, component, perms, status, display, cache, link, remark, parent_id, created_time, updated_time)
    VALUES
    ('新增', 'AddModelCreditRate', NULL, 1, NULL, 2, NULL, 'model:credit:rate:add', 1, 0, 1, '', NULL, v_parent_menu_id, NOW(), NULL);

    -- 编辑按钮
    INSERT INTO sys_menu (title, name, path, sort, icon, type, component, perms, status, display, cache, link, remark, parent_id, created_time, updated_time)
    VALUES
    ('编辑', 'EditModelCreditRate', NULL, 2, NULL, 2, NULL, 'model:credit:rate:edit', 1, 0, 1, '', NULL, v_parent_menu_id, NOW(), NULL);

    -- 删除按钮
    INSERT INTO sys_menu (title, name, path, sort, icon, type, component, perms, status, display, cache, link, remark, parent_id, created_time, updated_time)
    VALUES
    ('删除', 'DeleteModelCreditRate', NULL, 3, NULL, 2, NULL, 'model:credit:rate:del', 1, 0, 1, '', NULL, v_parent_menu_id, NOW(), NULL);

    -- 查看按钮
    INSERT INTO sys_menu (title, name, path, sort, icon, type, component, perms, status, display, cache, link, remark, parent_id, created_time, updated_time)
    VALUES
    ('查看', 'ViewModelCreditRate', NULL, 4, NULL, 2, NULL, 'model:credit:rate:get', 1, 0, 1, '', NULL, v_parent_menu_id, NOW(), NULL);
END $$;

-- =====================================================
-- 菜单生成完成
-- =====================================================
