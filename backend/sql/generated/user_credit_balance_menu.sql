-- =====================================================
-- 用户积分余额管理 菜单初始化 SQL (PostgreSQL)
-- 自动生成于: 2026-01-29 16:30:02.126334
-- 支持幂等操作：已存在则更新，不存在则新增
-- =====================================================

DO $$
DECLARE
    v_parent_id INTEGER;
    v_menu_id INTEGER;
BEGIN
    -- 查找或创建父级目录菜单 (path = /user_tier)
    SELECT id INTO v_parent_id FROM sys_menu 
    WHERE path = '/user_tier' AND type = 0
    ORDER BY id LIMIT 1;
    
    IF v_parent_id IS NULL THEN
        INSERT INTO sys_menu (title, name, path, sort, icon, type, component, perms, status, display, cache, link, remark, parent_id, created_time, updated_time)
        VALUES ('User Tier', 'User_tier', '/user_tier', 1, 'lucide:folder', 0, 'BasicLayout', NULL, 1, 1, 1, '', 'user_tier模块', NULL, NOW(), NULL)
        RETURNING id INTO v_parent_id;
    END IF;

    -- 查找或创建主菜单 (path = /user_tier/user_credit_balance)
    SELECT id INTO v_menu_id FROM sys_menu 
    WHERE path = '/user_tier/user_credit_balance' AND type = 1
    ORDER BY id LIMIT 1;
    
    IF v_menu_id IS NULL THEN
        INSERT INTO sys_menu (title, name, path, sort, icon, type, component, perms, status, display, cache, link, remark, parent_id, created_time, updated_time)
        VALUES ('用户积分余额管理', 'UserCreditBalance', '/user_tier/user_credit_balance', 1, 'lucide:list', 1, '/user_tier/user_credit_balance/index', NULL, 1, 1, 1, '', '用户积分余额', v_parent_id, NOW(), NULL)
        RETURNING id INTO v_menu_id;
    ELSE
        UPDATE sys_menu SET
            title = '用户积分余额管理',
            name = 'UserCreditBalance',
            component = '/user_tier/user_credit_balance/index',
            remark = '用户积分余额',
            parent_id = v_parent_id,
            updated_time = NOW()
        WHERE id = v_menu_id;
    END IF;

    -- 新增按钮（按 perms 判断）
    IF NOT EXISTS (SELECT 1 FROM sys_menu WHERE perms = 'user:credit:balance:add' AND parent_id = v_menu_id) THEN
        INSERT INTO sys_menu (title, name, path, sort, icon, type, component, perms, status, display, cache, link, remark, parent_id, created_time, updated_time)
        VALUES ('新增', 'AddUserCreditBalance', NULL, 1, NULL, 2, NULL, 'user:credit:balance:add', 1, 0, 1, '', NULL, v_menu_id, NOW(), NULL);
    END IF;

    -- 编辑按钮
    IF NOT EXISTS (SELECT 1 FROM sys_menu WHERE perms = 'user:credit:balance:edit' AND parent_id = v_menu_id) THEN
        INSERT INTO sys_menu (title, name, path, sort, icon, type, component, perms, status, display, cache, link, remark, parent_id, created_time, updated_time)
        VALUES ('编辑', 'EditUserCreditBalance', NULL, 2, NULL, 2, NULL, 'user:credit:balance:edit', 1, 0, 1, '', NULL, v_menu_id, NOW(), NULL);
    END IF;

    -- 删除按钮
    IF NOT EXISTS (SELECT 1 FROM sys_menu WHERE perms = 'user:credit:balance:del' AND parent_id = v_menu_id) THEN
        INSERT INTO sys_menu (title, name, path, sort, icon, type, component, perms, status, display, cache, link, remark, parent_id, created_time, updated_time)
        VALUES ('删除', 'DeleteUserCreditBalance', NULL, 3, NULL, 2, NULL, 'user:credit:balance:del', 1, 0, 1, '', NULL, v_menu_id, NOW(), NULL);
    END IF;

    -- 查看按钮
    IF NOT EXISTS (SELECT 1 FROM sys_menu WHERE perms = 'user:credit:balance:get' AND parent_id = v_menu_id) THEN
        INSERT INTO sys_menu (title, name, path, sort, icon, type, component, perms, status, display, cache, link, remark, parent_id, created_time, updated_time)
        VALUES ('查看', 'ViewUserCreditBalance', NULL, 4, NULL, 2, NULL, 'user:credit:balance:get', 1, 0, 1, '', NULL, v_menu_id, NOW(), NULL);
    END IF;
END $$;

-- =====================================================
-- 菜单生成完成
-- =====================================================
