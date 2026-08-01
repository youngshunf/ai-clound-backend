-- =====================================================
-- 云端节点设备授权码管理 菜单初始化 SQL (PostgreSQL)
-- 自动生成于: 2026-07-31 13:17:58.844854
-- 支持幂等操作：已存在则更新，不存在则新增
-- =====================================================

DO $$
DECLARE
    v_parent_id INTEGER;
    v_menu_id INTEGER;
BEGIN
    -- 查找或创建父级目录菜单 (path = /hasn_hosting)
    SELECT id INTO v_parent_id FROM sys_menu 
    WHERE path = '/hasn_hosting' AND type = 0
    ORDER BY id LIMIT 1;
    
    IF v_parent_id IS NULL THEN
        INSERT INTO sys_menu (title, name, path, sort, icon, type, component, perms, status, display, cache, link, remark, parent_id, created_time, updated_time)
        VALUES ('Hasn Hosting', 'Hasn_hosting', '/hasn_hosting', 1, 'lucide:folder', 0, 'BasicLayout', NULL, 1, 1, 1, '', 'hasn_hosting模块', NULL, NOW(), NULL)
        RETURNING id INTO v_parent_id;
    END IF;

    -- 查找或创建主菜单 (path = /hasn_hosting/hasn_node_authorization_codes)
    SELECT id INTO v_menu_id FROM sys_menu 
    WHERE path = '/hasn_hosting/hasn_node_authorization_codes' AND type = 1
    ORDER BY id LIMIT 1;
    
    IF v_menu_id IS NULL THEN
        INSERT INTO sys_menu (title, name, path, sort, icon, type, component, perms, status, display, cache, link, remark, parent_id, created_time, updated_time)
        VALUES ('云端节点设备授权码管理', 'HasnNodeAuthorizationCodes', '/hasn_hosting/hasn_node_authorization_codes', 1, 'lucide:list', 1, '/hasn_hosting/hasn_node_authorization_codes/index', NULL, 1, 1, 1, '', '云端节点设备授权码', v_parent_id, NOW(), NULL)
        RETURNING id INTO v_menu_id;
    ELSE
        UPDATE sys_menu SET
            title = '云端节点设备授权码管理',
            name = 'HasnNodeAuthorizationCodes',
            component = '/hasn_hosting/hasn_node_authorization_codes/index',
            remark = '云端节点设备授权码',
            parent_id = v_parent_id,
            updated_time = NOW()
        WHERE id = v_menu_id;
    END IF;

    -- 新增按钮（按 perms 判断）
    IF NOT EXISTS (SELECT 1 FROM sys_menu WHERE perms = 'hasn:node:authorization:codes:add' AND parent_id = v_menu_id) THEN
        INSERT INTO sys_menu (title, name, path, sort, icon, type, component, perms, status, display, cache, link, remark, parent_id, created_time, updated_time)
        VALUES ('新增', 'AddHasnNodeAuthorizationCodes', NULL, 1, NULL, 2, NULL, 'hasn:node:authorization:codes:add', 1, 0, 1, '', NULL, v_menu_id, NOW(), NULL);
    END IF;

    -- 编辑按钮
    IF NOT EXISTS (SELECT 1 FROM sys_menu WHERE perms = 'hasn:node:authorization:codes:edit' AND parent_id = v_menu_id) THEN
        INSERT INTO sys_menu (title, name, path, sort, icon, type, component, perms, status, display, cache, link, remark, parent_id, created_time, updated_time)
        VALUES ('编辑', 'EditHasnNodeAuthorizationCodes', NULL, 2, NULL, 2, NULL, 'hasn:node:authorization:codes:edit', 1, 0, 1, '', NULL, v_menu_id, NOW(), NULL);
    END IF;

    -- 删除按钮
    IF NOT EXISTS (SELECT 1 FROM sys_menu WHERE perms = 'hasn:node:authorization:codes:del' AND parent_id = v_menu_id) THEN
        INSERT INTO sys_menu (title, name, path, sort, icon, type, component, perms, status, display, cache, link, remark, parent_id, created_time, updated_time)
        VALUES ('删除', 'DeleteHasnNodeAuthorizationCodes', NULL, 3, NULL, 2, NULL, 'hasn:node:authorization:codes:del', 1, 0, 1, '', NULL, v_menu_id, NOW(), NULL);
    END IF;

    -- 查看按钮
    IF NOT EXISTS (SELECT 1 FROM sys_menu WHERE perms = 'hasn:node:authorization:codes:get' AND parent_id = v_menu_id) THEN
        INSERT INTO sys_menu (title, name, path, sort, icon, type, component, perms, status, display, cache, link, remark, parent_id, created_time, updated_time)
        VALUES ('查看', 'ViewHasnNodeAuthorizationCodes', NULL, 4, NULL, 2, NULL, 'hasn:node:authorization:codes:get', 1, 0, 1, '', NULL, v_menu_id, NOW(), NULL);
    END IF;
END $$;

-- =====================================================
-- 菜单生成完成
-- =====================================================
