-- =====================================================
-- 一次短视频创作（分身工作流管理 菜单初始化 SQL (PostgreSQL)
-- 自动生成于: 2026-06-27 09:34:09.527835
-- 支持幂等操作：已存在则更新，不存在则新增
-- =====================================================

DO $$
DECLARE
    v_parent_id INTEGER;
    v_menu_id INTEGER;
BEGIN
    -- 查找或创建父级目录菜单 (path = /hasn_reel)
    SELECT id INTO v_parent_id FROM sys_menu 
    WHERE path = '/hasn_reel' AND type = 0
    ORDER BY id LIMIT 1;
    
    IF v_parent_id IS NULL THEN
        INSERT INTO sys_menu (title, name, path, sort, icon, type, component, perms, status, display, cache, link, remark, parent_id, created_time, updated_time)
        VALUES ('Hasn Reel', 'Hasn_reel', '/hasn_reel', 1, 'lucide:folder', 0, 'BasicLayout', NULL, 1, 1, 1, '', 'hasn_reel模块', NULL, NOW(), NULL)
        RETURNING id INTO v_parent_id;
    END IF;

    -- 查找或创建主菜单 (path = /hasn_reel/reel_creation)
    SELECT id INTO v_menu_id FROM sys_menu 
    WHERE path = '/hasn_reel/reel_creation' AND type = 1
    ORDER BY id LIMIT 1;
    
    IF v_menu_id IS NULL THEN
        INSERT INTO sys_menu (title, name, path, sort, icon, type, component, perms, status, display, cache, link, remark, parent_id, created_time, updated_time)
        VALUES ('一次短视频创作（分身工作流管理', 'ReelCreation', '/hasn_reel/reel_creation', 1, 'lucide:list', 1, '/hasn_reel/reel_creation/index', NULL, 1, 1, 1, '', '一次短视频创作（分身工作流 + 进度 + 产物 + 历史；引擎/会话权威态经 daemon 同步落库）', v_parent_id, NOW(), NULL)
        RETURNING id INTO v_menu_id;
    ELSE
        UPDATE sys_menu SET
            title = '一次短视频创作（分身工作流管理',
            name = 'ReelCreation',
            component = '/hasn_reel/reel_creation/index',
            remark = '一次短视频创作（分身工作流 + 进度 + 产物 + 历史；引擎/会话权威态经 daemon 同步落库）',
            parent_id = v_parent_id,
            updated_time = NOW()
        WHERE id = v_menu_id;
    END IF;

    -- 新增按钮（按 perms 判断）
    IF NOT EXISTS (SELECT 1 FROM sys_menu WHERE perms = 'reel:creation:add' AND parent_id = v_menu_id) THEN
        INSERT INTO sys_menu (title, name, path, sort, icon, type, component, perms, status, display, cache, link, remark, parent_id, created_time, updated_time)
        VALUES ('新增', 'AddReelCreation', NULL, 1, NULL, 2, NULL, 'reel:creation:add', 1, 0, 1, '', NULL, v_menu_id, NOW(), NULL);
    END IF;

    -- 编辑按钮
    IF NOT EXISTS (SELECT 1 FROM sys_menu WHERE perms = 'reel:creation:edit' AND parent_id = v_menu_id) THEN
        INSERT INTO sys_menu (title, name, path, sort, icon, type, component, perms, status, display, cache, link, remark, parent_id, created_time, updated_time)
        VALUES ('编辑', 'EditReelCreation', NULL, 2, NULL, 2, NULL, 'reel:creation:edit', 1, 0, 1, '', NULL, v_menu_id, NOW(), NULL);
    END IF;

    -- 删除按钮
    IF NOT EXISTS (SELECT 1 FROM sys_menu WHERE perms = 'reel:creation:del' AND parent_id = v_menu_id) THEN
        INSERT INTO sys_menu (title, name, path, sort, icon, type, component, perms, status, display, cache, link, remark, parent_id, created_time, updated_time)
        VALUES ('删除', 'DeleteReelCreation', NULL, 3, NULL, 2, NULL, 'reel:creation:del', 1, 0, 1, '', NULL, v_menu_id, NOW(), NULL);
    END IF;

    -- 查看按钮
    IF NOT EXISTS (SELECT 1 FROM sys_menu WHERE perms = 'reel:creation:get' AND parent_id = v_menu_id) THEN
        INSERT INTO sys_menu (title, name, path, sort, icon, type, component, perms, status, display, cache, link, remark, parent_id, created_time, updated_time)
        VALUES ('查看', 'ViewReelCreation', NULL, 4, NULL, 2, NULL, 'reel:creation:get', 1, 0, 1, '', NULL, v_menu_id, NOW(), NULL);
    END IF;
END $$;

-- =====================================================
-- 菜单生成完成
-- =====================================================
