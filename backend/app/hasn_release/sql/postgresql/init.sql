-- =====================================================
--  菜单初始化 SQL (PostgreSQL)
-- 自动生成于: 2026-07-05 12:30:00.826078+08:00
-- 支持幂等操作：已存在则更新，不存在则新增
-- =====================================================

DO $$
DECLARE
    v_parent_id INTEGER;
    v_menu_id INTEGER;
BEGIN
    -- 查找或创建父级目录菜单 (path = /hasn_release)
    SELECT id INTO v_parent_id FROM sys_menu 
    WHERE path = '/hasn_release' AND type = 0
    ORDER BY id LIMIT 1;
    
    IF v_parent_id IS NULL THEN
        INSERT INTO sys_menu (title, name, path, sort, icon, type, component, perms, status, display, cache, link, remark, parent_id, created_time, updated_time)
        VALUES ('', 'Hasn_release', '/hasn_release', 1, 'lucide:folder', 0, 'BasicLayout', NULL, 1, 1, 1, '', 'hasn_release模块', NULL, NOW(), NULL)
        RETURNING id INTO v_parent_id;
    END IF;

    -- 查找或创建主菜单 (path = /hasn_release/app_release)
    SELECT id INTO v_menu_id FROM sys_menu 
    WHERE path = '/hasn_release/app_release' AND type = 1
    ORDER BY id LIMIT 1;
    
    IF v_menu_id IS NULL THEN
        INSERT INTO sys_menu (title, name, path, sort, icon, type, component, perms, status, display, cache, link, remark, parent_id, created_time, updated_time)
        VALUES ('', 'AppRelease', '/hasn_release/app_release', 1, 'lucide:list', 1, '/hasn_release/app_release/index', NULL, 1, 1, 1, '', '桌面端发布版本（版本级·携 changelog + 状态 + is_latest 指针）', v_parent_id, NOW(), NULL)
        RETURNING id INTO v_menu_id;
    ELSE
        UPDATE sys_menu SET
            title = '',
            name = 'AppRelease',
            component = '/hasn_release/app_release/index',
            remark = '桌面端发布版本（版本级·携 changelog + 状态 + is_latest 指针）',
            parent_id = v_parent_id,
            updated_time = NOW()
        WHERE id = v_menu_id;
    END IF;

    -- 新增按钮（按 perms 判断）
    IF NOT EXISTS (SELECT 1 FROM sys_menu WHERE perms = 'app:release:add' AND parent_id = v_menu_id) THEN
        INSERT INTO sys_menu (title, name, path, sort, icon, type, component, perms, status, display, cache, link, remark, parent_id, created_time, updated_time)
        VALUES ('新增', 'AddAppRelease', NULL, 1, NULL, 2, NULL, 'app:release:add', 1, 0, 1, '', NULL, v_menu_id, NOW(), NULL);
    END IF;

    -- 编辑按钮
    IF NOT EXISTS (SELECT 1 FROM sys_menu WHERE perms = 'app:release:edit' AND parent_id = v_menu_id) THEN
        INSERT INTO sys_menu (title, name, path, sort, icon, type, component, perms, status, display, cache, link, remark, parent_id, created_time, updated_time)
        VALUES ('编辑', 'EditAppRelease', NULL, 2, NULL, 2, NULL, 'app:release:edit', 1, 0, 1, '', NULL, v_menu_id, NOW(), NULL);
    END IF;

    -- 删除按钮
    IF NOT EXISTS (SELECT 1 FROM sys_menu WHERE perms = 'app:release:del' AND parent_id = v_menu_id) THEN
        INSERT INTO sys_menu (title, name, path, sort, icon, type, component, perms, status, display, cache, link, remark, parent_id, created_time, updated_time)
        VALUES ('删除', 'DeleteAppRelease', NULL, 3, NULL, 2, NULL, 'app:release:del', 1, 0, 1, '', NULL, v_menu_id, NOW(), NULL);
    END IF;

    -- 查看按钮
    IF NOT EXISTS (SELECT 1 FROM sys_menu WHERE perms = 'app:release:get' AND parent_id = v_menu_id) THEN
        INSERT INTO sys_menu (title, name, path, sort, icon, type, component, perms, status, display, cache, link, remark, parent_id, created_time, updated_time)
        VALUES ('查看', 'ViewAppRelease', NULL, 4, NULL, 2, NULL, 'app:release:get', 1, 0, 1, '', NULL, v_menu_id, NOW(), NULL);
    END IF;
END $$;

-- =====================================================
-- 菜单生成完成
-- =====================================================
