-- =====================================================
-- 平台项目（云端权威·业务容器·联邦挂靠聚合门面，doc38）管理 菜单初始化 SQL (PostgreSQL)
-- 自动生成于: 2026-07-16 10:02:56.077772
-- 支持幂等操作：已存在则更新，不存在则新增
-- =====================================================

DO $$
DECLARE
    v_parent_id INTEGER;
    v_menu_id INTEGER;
BEGIN
    -- 查找或创建父级目录菜单 (path = /hasn_project)
    SELECT id INTO v_parent_id FROM sys_menu 
    WHERE path = '/hasn_project' AND type = 0
    ORDER BY id LIMIT 1;
    
    IF v_parent_id IS NULL THEN
        INSERT INTO sys_menu (title, name, path, sort, icon, type, component, perms, status, display, cache, link, remark, parent_id, created_time, updated_time)
        VALUES ('Hasn Project', 'Hasn_project', '/hasn_project', 1, 'lucide:folder', 0, 'BasicLayout', NULL, 1, 1, 1, '', 'hasn_project模块', NULL, NOW(), NULL)
        RETURNING id INTO v_parent_id;
    END IF;

    -- 查找或创建主菜单 (path = /hasn_project/hasn_project)
    SELECT id INTO v_menu_id FROM sys_menu 
    WHERE path = '/hasn_project/hasn_project' AND type = 1
    ORDER BY id LIMIT 1;
    
    IF v_menu_id IS NULL THEN
        INSERT INTO sys_menu (title, name, path, sort, icon, type, component, perms, status, display, cache, link, remark, parent_id, created_time, updated_time)
        VALUES ('平台项目（云端权威·业务容器·联邦挂靠聚合门面，doc38）管理', 'HasnProject', '/hasn_project/hasn_project', 1, 'lucide:list', 1, '/hasn_project/hasn_project/index', NULL, 1, 1, 1, '', '平台项目（云端权威·业务容器·联邦挂靠聚合门面，doc38）', v_parent_id, NOW(), NULL)
        RETURNING id INTO v_menu_id;
    ELSE
        UPDATE sys_menu SET
            title = '平台项目（云端权威·业务容器·联邦挂靠聚合门面，doc38）管理',
            name = 'HasnProject',
            component = '/hasn_project/hasn_project/index',
            remark = '平台项目（云端权威·业务容器·联邦挂靠聚合门面，doc38）',
            parent_id = v_parent_id,
            updated_time = NOW()
        WHERE id = v_menu_id;
    END IF;

    -- 新增按钮（按 perms 判断）
    IF NOT EXISTS (SELECT 1 FROM sys_menu WHERE perms = 'hasn:project:add' AND parent_id = v_menu_id) THEN
        INSERT INTO sys_menu (title, name, path, sort, icon, type, component, perms, status, display, cache, link, remark, parent_id, created_time, updated_time)
        VALUES ('新增', 'AddHasnProject', NULL, 1, NULL, 2, NULL, 'hasn:project:add', 1, 0, 1, '', NULL, v_menu_id, NOW(), NULL);
    END IF;

    -- 编辑按钮
    IF NOT EXISTS (SELECT 1 FROM sys_menu WHERE perms = 'hasn:project:edit' AND parent_id = v_menu_id) THEN
        INSERT INTO sys_menu (title, name, path, sort, icon, type, component, perms, status, display, cache, link, remark, parent_id, created_time, updated_time)
        VALUES ('编辑', 'EditHasnProject', NULL, 2, NULL, 2, NULL, 'hasn:project:edit', 1, 0, 1, '', NULL, v_menu_id, NOW(), NULL);
    END IF;

    -- 删除按钮
    IF NOT EXISTS (SELECT 1 FROM sys_menu WHERE perms = 'hasn:project:del' AND parent_id = v_menu_id) THEN
        INSERT INTO sys_menu (title, name, path, sort, icon, type, component, perms, status, display, cache, link, remark, parent_id, created_time, updated_time)
        VALUES ('删除', 'DeleteHasnProject', NULL, 3, NULL, 2, NULL, 'hasn:project:del', 1, 0, 1, '', NULL, v_menu_id, NOW(), NULL);
    END IF;

    -- 查看按钮
    IF NOT EXISTS (SELECT 1 FROM sys_menu WHERE perms = 'hasn:project:get' AND parent_id = v_menu_id) THEN
        INSERT INTO sys_menu (title, name, path, sort, icon, type, component, perms, status, display, cache, link, remark, parent_id, created_time, updated_time)
        VALUES ('查看', 'ViewHasnProject', NULL, 4, NULL, 2, NULL, 'hasn:project:get', 1, 0, 1, '', NULL, v_menu_id, NOW(), NULL);
    END IF;
END $$;

-- =====================================================
-- 菜单生成完成
-- =====================================================
