-- =====================================================
-- 设计系统下游消费登记（换系统重渲染追踪）管理 菜单初始化 SQL (PostgreSQL)
-- 自动生成于: 2026-06-18 10:19:40.658844
-- 支持幂等操作：已存在则更新，不存在则新增
-- =====================================================

DO $$
DECLARE
    v_parent_id INTEGER;
    v_menu_id INTEGER;
BEGIN
    -- 查找或创建父级目录菜单 (path = /hasn_designsystem)
    SELECT id INTO v_parent_id FROM sys_menu 
    WHERE path = '/hasn_designsystem' AND type = 0
    ORDER BY id LIMIT 1;
    
    IF v_parent_id IS NULL THEN
        INSERT INTO sys_menu (title, name, path, sort, icon, type, component, perms, status, display, cache, link, remark, parent_id, created_time, updated_time)
        VALUES ('Hasn Designsystem', 'Hasn_designsystem', '/hasn_designsystem', 1, 'lucide:folder', 0, 'BasicLayout', NULL, 1, 1, 1, '', 'hasn_designsystem模块', NULL, NOW(), NULL)
        RETURNING id INTO v_parent_id;
    END IF;

    -- 查找或创建主菜单 (path = /hasn_designsystem/consumer_link)
    SELECT id INTO v_menu_id FROM sys_menu 
    WHERE path = '/hasn_designsystem/consumer_link' AND type = 1
    ORDER BY id LIMIT 1;
    
    IF v_menu_id IS NULL THEN
        INSERT INTO sys_menu (title, name, path, sort, icon, type, component, perms, status, display, cache, link, remark, parent_id, created_time, updated_time)
        VALUES ('设计系统下游消费登记（换系统重渲染追踪）管理', 'ConsumerLink', '/hasn_designsystem/consumer_link', 1, 'lucide:list', 1, '/hasn_designsystem/consumer_link/index', NULL, 1, 1, 1, '', '设计系统下游消费登记（换系统重渲染追踪）', v_parent_id, NOW(), NULL)
        RETURNING id INTO v_menu_id;
    ELSE
        UPDATE sys_menu SET
            title = '设计系统下游消费登记（换系统重渲染追踪）管理',
            name = 'ConsumerLink',
            component = '/hasn_designsystem/consumer_link/index',
            remark = '设计系统下游消费登记（换系统重渲染追踪）',
            parent_id = v_parent_id,
            updated_time = NOW()
        WHERE id = v_menu_id;
    END IF;

    -- 新增按钮（按 perms 判断）
    IF NOT EXISTS (SELECT 1 FROM sys_menu WHERE perms = 'consumer:link:add' AND parent_id = v_menu_id) THEN
        INSERT INTO sys_menu (title, name, path, sort, icon, type, component, perms, status, display, cache, link, remark, parent_id, created_time, updated_time)
        VALUES ('新增', 'AddConsumerLink', NULL, 1, NULL, 2, NULL, 'consumer:link:add', 1, 0, 1, '', NULL, v_menu_id, NOW(), NULL);
    END IF;

    -- 编辑按钮
    IF NOT EXISTS (SELECT 1 FROM sys_menu WHERE perms = 'consumer:link:edit' AND parent_id = v_menu_id) THEN
        INSERT INTO sys_menu (title, name, path, sort, icon, type, component, perms, status, display, cache, link, remark, parent_id, created_time, updated_time)
        VALUES ('编辑', 'EditConsumerLink', NULL, 2, NULL, 2, NULL, 'consumer:link:edit', 1, 0, 1, '', NULL, v_menu_id, NOW(), NULL);
    END IF;

    -- 删除按钮
    IF NOT EXISTS (SELECT 1 FROM sys_menu WHERE perms = 'consumer:link:del' AND parent_id = v_menu_id) THEN
        INSERT INTO sys_menu (title, name, path, sort, icon, type, component, perms, status, display, cache, link, remark, parent_id, created_time, updated_time)
        VALUES ('删除', 'DeleteConsumerLink', NULL, 3, NULL, 2, NULL, 'consumer:link:del', 1, 0, 1, '', NULL, v_menu_id, NOW(), NULL);
    END IF;

    -- 查看按钮
    IF NOT EXISTS (SELECT 1 FROM sys_menu WHERE perms = 'consumer:link:get' AND parent_id = v_menu_id) THEN
        INSERT INTO sys_menu (title, name, path, sort, icon, type, component, perms, status, display, cache, link, remark, parent_id, created_time, updated_time)
        VALUES ('查看', 'ViewConsumerLink', NULL, 4, NULL, 2, NULL, 'consumer:link:get', 1, 0, 1, '', NULL, v_menu_id, NOW(), NULL);
    END IF;
END $$;

-- =====================================================
-- 菜单生成完成
-- =====================================================
