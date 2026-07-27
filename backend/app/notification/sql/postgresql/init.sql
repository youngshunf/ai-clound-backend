-- =====================================================
--  菜单初始化 SQL (PostgreSQL)
-- 自动生成于: 2026-07-27 15:26:11.866552+08:00
-- 支持幂等操作：已存在则更新，不存在则新增
-- =====================================================

DO $$
DECLARE
    v_parent_id INTEGER;
    v_menu_id INTEGER;
BEGIN
    -- 查找或创建父级目录菜单 (path = /notification)
    SELECT id INTO v_parent_id FROM sys_menu
    WHERE path = '/notification' AND type = 0
    ORDER BY id LIMIT 1;

    IF v_parent_id IS NULL THEN
        INSERT INTO sys_menu (title, name, path, sort, icon, type, component, perms, status, display, cache, link, remark, parent_id, created_time, updated_time)
        VALUES ('', 'Notification', '/notification', 1, 'lucide:folder', 0, 'BasicLayout', NULL, 1, 1, 1, '', 'notification模块', NULL, NOW(), NULL)
        RETURNING id INTO v_parent_id;
    END IF;

    -- 查找或创建主菜单 (path = /notification/hasn_notification_im_command_outbox)
    SELECT id INTO v_menu_id FROM sys_menu
    WHERE path = '/notification/hasn_notification_im_command_outbox' AND type = 1
    ORDER BY id LIMIT 1;

    IF v_menu_id IS NULL THEN
        INSERT INTO sys_menu (title, name, path, sort, icon, type, component, perms, status, display, cache, link, remark, parent_id, created_time, updated_time)
        VALUES ('', 'HasnNotificationImCommandOutbox', '/notification/hasn_notification_im_command_outbox', 1, 'lucide:list', 1, '/notification/hasn_notification_im_command_outbox/index', NULL, 1, 1, 1, '', '通知业务状态触发 IM 卡片的事务命令队列', v_parent_id, NOW(), NULL)
        RETURNING id INTO v_menu_id;
    ELSE
        UPDATE sys_menu SET
            title = '',
            name = 'HasnNotificationImCommandOutbox',
            component = '/notification/hasn_notification_im_command_outbox/index',
            remark = '通知业务状态触发 IM 卡片的事务命令队列',
            parent_id = v_parent_id,
            updated_time = NOW()
        WHERE id = v_menu_id;
    END IF;

    -- 新增按钮（按 perms 判断）
    IF NOT EXISTS (SELECT 1 FROM sys_menu WHERE perms = 'hasn:notification:im:command:outbox:add' AND parent_id = v_menu_id) THEN
        INSERT INTO sys_menu (title, name, path, sort, icon, type, component, perms, status, display, cache, link, remark, parent_id, created_time, updated_time)
        VALUES ('新增', 'AddHasnNotificationImCommandOutbox', NULL, 1, NULL, 2, NULL, 'hasn:notification:im:command:outbox:add', 1, 0, 1, '', NULL, v_menu_id, NOW(), NULL);
    END IF;

    -- 编辑按钮
    IF NOT EXISTS (SELECT 1 FROM sys_menu WHERE perms = 'hasn:notification:im:command:outbox:edit' AND parent_id = v_menu_id) THEN
        INSERT INTO sys_menu (title, name, path, sort, icon, type, component, perms, status, display, cache, link, remark, parent_id, created_time, updated_time)
        VALUES ('编辑', 'EditHasnNotificationImCommandOutbox', NULL, 2, NULL, 2, NULL, 'hasn:notification:im:command:outbox:edit', 1, 0, 1, '', NULL, v_menu_id, NOW(), NULL);
    END IF;

    -- 删除按钮
    IF NOT EXISTS (SELECT 1 FROM sys_menu WHERE perms = 'hasn:notification:im:command:outbox:del' AND parent_id = v_menu_id) THEN
        INSERT INTO sys_menu (title, name, path, sort, icon, type, component, perms, status, display, cache, link, remark, parent_id, created_time, updated_time)
        VALUES ('删除', 'DeleteHasnNotificationImCommandOutbox', NULL, 3, NULL, 2, NULL, 'hasn:notification:im:command:outbox:del', 1, 0, 1, '', NULL, v_menu_id, NOW(), NULL);
    END IF;

    -- 查看按钮
    IF NOT EXISTS (SELECT 1 FROM sys_menu WHERE perms = 'hasn:notification:im:command:outbox:get' AND parent_id = v_menu_id) THEN
        INSERT INTO sys_menu (title, name, path, sort, icon, type, component, perms, status, display, cache, link, remark, parent_id, created_time, updated_time)
        VALUES ('查看', 'ViewHasnNotificationImCommandOutbox', NULL, 4, NULL, 2, NULL, 'hasn:notification:im:command:outbox:get', 1, 0, 1, '', NULL, v_menu_id, NOW(), NULL);
    END IF;
END $$;

-- =====================================================
-- 菜单生成完成
-- =====================================================
