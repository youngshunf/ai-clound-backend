-- =====================================================
-- 项目管理 菜单初始化 SQL (PostgreSQL)
-- 自动生成于: 2026-01-28 14:19:03.743840
-- =====================================================

DO $$
DECLARE
    v_parent_id INTEGER;
    v_menu_id INTEGER;
BEGIN
    -- 查找或创建父级目录菜单 (path = /projects)
    SELECT id INTO v_parent_id FROM sys_menu 
    WHERE path = '/projects' AND type = 0
    ORDER BY id LIMIT 1;
    
    IF v_parent_id IS NULL THEN
        -- 创建父级目录菜单
        INSERT INTO sys_menu (title, name, path, sort, icon, type, component, perms, status, display, cache, link, remark, parent_id, created_time, updated_time)
        VALUES
        ('Projects', 'Projects', '/projects', 1, 'lucide:folder', 0, 'BasicLayout', NULL, 1, 1, 1, '', 'projects模块', NULL, NOW(), NULL)
        RETURNING id INTO v_parent_id;
    END IF;

    -- 插入菜单
    INSERT INTO sys_menu (title, name, path, sort, icon, type, component, perms, status, display, cache, link, remark, parent_id, created_time, updated_time)
    VALUES
    ('项目管理', 'Projects', '/projects/projects', 1, 'lucide:list', 1, '#/views/projects/projects/index.vue', NULL, 1, 1, 1, '', '项目表 - 工作区的核心上下文', v_parent_id, NOW(), NULL)
    RETURNING id INTO v_menu_id;

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
