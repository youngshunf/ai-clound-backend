-- =====================================================
-- 平台默认配置 菜单初始化 SQL (PostgreSQL)
-- 运营 Admin 编辑页：node.media 三组模型 + agent_runtime 四槽默认，保存即覆盖式 PUT，
--   server 重算 revision，桌面端 daemon + Agent 运行时通过同步自动应用。
-- 路由：/hasn/platform_default_config → views/hasn/platform_default_config/index.vue
-- 权限：编辑/保存按 hasn:platform:config:edit（与云端 Admin PUT 的 RBAC 同口径）
-- 支持幂等操作：已存在则更新，不存在则新增
-- 设计事实源：docs/hasn-node设计文档/运行时配置下发/01-平台默认配置下发机制.md
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

    -- 查找或创建主菜单 (path = /hasn/platform_default_config)
    SELECT id INTO v_menu_id FROM sys_menu
    WHERE path = '/hasn/platform_default_config' AND type = 1
    ORDER BY id LIMIT 1;

    IF v_menu_id IS NULL THEN
        INSERT INTO sys_menu (title, name, path, sort, icon, type, component, perms, status, display, cache, link, remark, parent_id, created_time, updated_time)
        VALUES ('平台默认配置', 'HasnPlatformDefaultConfig', '/hasn/platform_default_config', 2, 'lucide:settings', 1, '/hasn/platform_default_config/index', NULL, 1, 1, 1, '', '平台默认模型配置（云端权威下发）', v_parent_id, NOW(), NULL)
        RETURNING id INTO v_menu_id;
    ELSE
        UPDATE sys_menu SET
            title = '平台默认配置',
            name = 'HasnPlatformDefaultConfig',
            component = '/hasn/platform_default_config/index',
            remark = '平台默认模型配置（云端权威下发）',
            parent_id = v_parent_id,
            updated_time = NOW()
        WHERE id = v_menu_id;
    END IF;

    -- 编辑/保存按钮（与云端 Admin PUT 的 RBAC 同口径）
    IF NOT EXISTS (SELECT 1 FROM sys_menu WHERE perms = 'hasn:platform:config:edit' AND parent_id = v_menu_id) THEN
        INSERT INTO sys_menu (title, name, path, sort, icon, type, component, perms, status, display, cache, link, remark, parent_id, created_time, updated_time)
        VALUES ('编辑', 'EditHasnPlatformDefaultConfig', NULL, 1, NULL, 2, NULL, 'hasn:platform:config:edit', 1, 0, 1, '', NULL, v_menu_id, NOW(), NULL);
    END IF;
END $$;

-- =====================================================
-- 菜单生成完成
-- =====================================================
