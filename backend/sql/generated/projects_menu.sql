-- =====================================================
-- 项目管理 菜单初始化 SQL (PostgreSQL)
-- 自动生成于: 2026-01-28 14:12:49.571376
-- =====================================================

-- 菜单
INSERT INTO sys_menu (title, name, path, sort, icon, type, component, perms, status, display, cache, link, remark, parent_id, created_time, updated_time)
VALUES
('项目管理', 'Projects', '/projects/projects', 1, 'lucide:list', 1, '#/views/projects/projects/index.vue', NULL, 1, 1, 1, '', '项目表 - 工作区的核心上下文', NULL, NOW(), NULL)
RETURNING id AS menu_id;

-- 获取刚插入的菜单 ID（存储到变量中用于后续按钮菜单）
DO $$
DECLARE
    v_menu_id INTEGER;
BEGIN
    -- 获取刚插入的菜单 ID
    SELECT id INTO v_menu_id FROM sys_menu 
    WHERE name = 'Projects' AND path = '/projects/projects'
    ORDER BY id DESC LIMIT 1;

    -- 新增按钮
    INSERT INTO sys_menu (title, name, path, sort, icon, type, component, perms, status, display, cache, link, remark, parent_id, created_time, updated_time)
    VALUES
    ('新增', 'AddProjects', NULL, 1, NULL, 2, NULL, 'projects:add', 1, 0, 1, '', NULL, v_menu_id, NOW(), NULL);

    -- 编辑按钮
    INSERT INTO sys_menu (title, name, path, sort, icon, type, component, perms, status, display, cache, link, remark, parent_id, created_time, updated_time)
    VALUES
    ('编辑', 'EditProjects', NULL, 2, NULL, 2, NULL, 'projects:edit', 1, 0, 1, '', NULL, v_menu_id, NOW(), NULL);

    -- 删除按钮
    INSERT INTO sys_menu (title, name, path, sort, icon, type, component, perms, status, display, cache, link, remark, parent_id, created_time, updated_time)
    VALUES
    ('删除', 'DeleteProjects', NULL, 3, NULL, 2, NULL, 'projects:del', 1, 0, 1, '', NULL, v_menu_id, NOW(), NULL);

    -- 查看按钮
    INSERT INTO sys_menu (title, name, path, sort, icon, type, component, perms, status, display, cache, link, remark, parent_id, created_time, updated_time)
    VALUES
    ('查看', 'ViewProjects', NULL, 4, NULL, 2, NULL, 'projects:get', 1, 0, 1, '', NULL, v_menu_id, NOW(), NULL);
END $$;

-- =====================================================
-- 菜单生成完成
-- =====================================================
