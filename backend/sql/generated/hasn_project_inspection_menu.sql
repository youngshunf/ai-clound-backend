-- =====================================================
-- 平台项目巡检建议（项目经理分身发布的权威建议记录，doc38管理 菜单初始化 SQL (PostgreSQL)
-- 自动生成于: 2026-07-27 00:46:10.853318
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

    -- 查找或创建主菜单 (path = /hasn_project/hasn_project_inspection)
    SELECT id INTO v_menu_id FROM sys_menu
    WHERE path = '/hasn_project/hasn_project_inspection' AND type = 1
    ORDER BY id LIMIT 1;

    IF v_menu_id IS NULL THEN
        INSERT INTO sys_menu (title, name, path, sort, icon, type, component, perms, status, display, cache, link, remark, parent_id, created_time, updated_time)
        VALUES ('平台项目巡检建议（项目经理分身发布的权威建议记录，doc38管理', 'HasnProjectInspection', '/hasn_project/hasn_project_inspection', 1, 'lucide:list', 1, '/hasn_project/hasn_project_inspection/index', NULL, 1, 1, 1, '', '平台项目巡检建议（项目经理分身发布的权威建议记录，doc38 C11）', v_parent_id, NOW(), NULL)
        RETURNING id INTO v_menu_id;
    ELSE
        UPDATE sys_menu SET
            title = '平台项目巡检建议（项目经理分身发布的权威建议记录，doc38管理',
            name = 'HasnProjectInspection',
            component = '/hasn_project/hasn_project_inspection/index',
            remark = '平台项目巡检建议（项目经理分身发布的权威建议记录，doc38 C11）',
            parent_id = v_parent_id,
            updated_time = NOW()
        WHERE id = v_menu_id;
    END IF;

    -- 新增按钮（按 perms 判断）
    IF NOT EXISTS (SELECT 1 FROM sys_menu WHERE perms = 'hasn:project:inspection:add' AND parent_id = v_menu_id) THEN
        INSERT INTO sys_menu (title, name, path, sort, icon, type, component, perms, status, display, cache, link, remark, parent_id, created_time, updated_time)
        VALUES ('新增', 'AddHasnProjectInspection', NULL, 1, NULL, 2, NULL, 'hasn:project:inspection:add', 1, 0, 1, '', NULL, v_menu_id, NOW(), NULL);
    END IF;

    -- 编辑按钮
    IF NOT EXISTS (SELECT 1 FROM sys_menu WHERE perms = 'hasn:project:inspection:edit' AND parent_id = v_menu_id) THEN
        INSERT INTO sys_menu (title, name, path, sort, icon, type, component, perms, status, display, cache, link, remark, parent_id, created_time, updated_time)
        VALUES ('编辑', 'EditHasnProjectInspection', NULL, 2, NULL, 2, NULL, 'hasn:project:inspection:edit', 1, 0, 1, '', NULL, v_menu_id, NOW(), NULL);
    END IF;

    -- 删除按钮
    IF NOT EXISTS (SELECT 1 FROM sys_menu WHERE perms = 'hasn:project:inspection:del' AND parent_id = v_menu_id) THEN
        INSERT INTO sys_menu (title, name, path, sort, icon, type, component, perms, status, display, cache, link, remark, parent_id, created_time, updated_time)
        VALUES ('删除', 'DeleteHasnProjectInspection', NULL, 3, NULL, 2, NULL, 'hasn:project:inspection:del', 1, 0, 1, '', NULL, v_menu_id, NOW(), NULL);
    END IF;

    -- 查看按钮
    IF NOT EXISTS (SELECT 1 FROM sys_menu WHERE perms = 'hasn:project:inspection:get' AND parent_id = v_menu_id) THEN
        INSERT INTO sys_menu (title, name, path, sort, icon, type, component, perms, status, display, cache, link, remark, parent_id, created_time, updated_time)
        VALUES ('查看', 'ViewHasnProjectInspection', NULL, 4, NULL, 2, NULL, 'hasn:project:inspection:get', 1, 0, 1, '', NULL, v_menu_id, NOW(), NULL);
    END IF;
END $$;

-- =====================================================
-- 菜单生成完成
-- =====================================================
