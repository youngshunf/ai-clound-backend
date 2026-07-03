-- =====================================================
-- 平台运维授予源（Admin-only·G1管理 菜单初始化 SQL (PostgreSQL)
-- 自动生成于: 2026-07-02 09:29:19.297787
-- 支持幂等操作：已存在则更新，不存在则新增
-- =====================================================

DO $$
DECLARE
    v_parent_id INTEGER;
    v_menu_id INTEGER;
BEGIN
    -- 查找或创建父级目录菜单 (path = /hasn)
    SELECT id INTO v_parent_id FROM sys_menu 
    WHERE path = '/hasn' AND type = 0
    ORDER BY id LIMIT 1;
    
    IF v_parent_id IS NULL THEN
        INSERT INTO sys_menu (title, name, path, sort, icon, type, component, perms, status, display, cache, link, remark, parent_id, created_time, updated_time)
        VALUES ('Hasn', 'Hasn', '/hasn', 1, 'lucide:folder', 0, 'BasicLayout', NULL, 1, 1, 1, '', 'hasn模块', NULL, NOW(), NULL)
        RETURNING id INTO v_parent_id;
    END IF;

    -- 查找或创建主菜单 (path = /hasn/hasn_platform_operator_grants)
    SELECT id INTO v_menu_id FROM sys_menu 
    WHERE path = '/hasn/hasn_platform_operator_grants' AND type = 1
    ORDER BY id LIMIT 1;
    
    IF v_menu_id IS NULL THEN
        INSERT INTO sys_menu (title, name, path, sort, icon, type, component, perms, status, display, cache, link, remark, parent_id, created_time, updated_time)
        VALUES ('平台运维授予源（Admin-only·G1管理', 'HasnPlatformOperatorGrants', '/hasn/hasn_platform_operator_grants', 1, 'lucide:list', 1, '/hasn/hasn_platform_operator_grants/index', NULL, 1, 1, 1, '', '平台运维授予源（Admin-only·G1 特权门）', v_parent_id, NOW(), NULL)
        RETURNING id INTO v_menu_id;
    ELSE
        UPDATE sys_menu SET
            title = '平台运维授予源（Admin-only·G1管理',
            name = 'HasnPlatformOperatorGrants',
            component = '/hasn/hasn_platform_operator_grants/index',
            remark = '平台运维授予源（Admin-only·G1 特权门）',
            parent_id = v_parent_id,
            updated_time = NOW()
        WHERE id = v_menu_id;
    END IF;

    -- 新增按钮（按 perms 判断）
    IF NOT EXISTS (SELECT 1 FROM sys_menu WHERE perms = 'hasn:platform:operator:grants:add' AND parent_id = v_menu_id) THEN
        INSERT INTO sys_menu (title, name, path, sort, icon, type, component, perms, status, display, cache, link, remark, parent_id, created_time, updated_time)
        VALUES ('新增', 'AddHasnPlatformOperatorGrants', NULL, 1, NULL, 2, NULL, 'hasn:platform:operator:grants:add', 1, 0, 1, '', NULL, v_menu_id, NOW(), NULL);
    END IF;

    -- 编辑按钮
    IF NOT EXISTS (SELECT 1 FROM sys_menu WHERE perms = 'hasn:platform:operator:grants:edit' AND parent_id = v_menu_id) THEN
        INSERT INTO sys_menu (title, name, path, sort, icon, type, component, perms, status, display, cache, link, remark, parent_id, created_time, updated_time)
        VALUES ('编辑', 'EditHasnPlatformOperatorGrants', NULL, 2, NULL, 2, NULL, 'hasn:platform:operator:grants:edit', 1, 0, 1, '', NULL, v_menu_id, NOW(), NULL);
    END IF;

    -- 删除按钮
    IF NOT EXISTS (SELECT 1 FROM sys_menu WHERE perms = 'hasn:platform:operator:grants:del' AND parent_id = v_menu_id) THEN
        INSERT INTO sys_menu (title, name, path, sort, icon, type, component, perms, status, display, cache, link, remark, parent_id, created_time, updated_time)
        VALUES ('删除', 'DeleteHasnPlatformOperatorGrants', NULL, 3, NULL, 2, NULL, 'hasn:platform:operator:grants:del', 1, 0, 1, '', NULL, v_menu_id, NOW(), NULL);
    END IF;

    -- 查看按钮
    IF NOT EXISTS (SELECT 1 FROM sys_menu WHERE perms = 'hasn:platform:operator:grants:get' AND parent_id = v_menu_id) THEN
        INSERT INTO sys_menu (title, name, path, sort, icon, type, component, perms, status, display, cache, link, remark, parent_id, created_time, updated_time)
        VALUES ('查看', 'ViewHasnPlatformOperatorGrants', NULL, 4, NULL, 2, NULL, 'hasn:platform:operator:grants:get', 1, 0, 1, '', NULL, v_menu_id, NOW(), NULL);
    END IF;
END $$;

-- =====================================================
-- 菜单生成完成
-- =====================================================
