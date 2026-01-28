-- =====================================================
-- 积分交易记录管理 菜单初始化 SQL (PostgreSQL)
-- 自动生成于: 2026-01-28 17:24:09.105106
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

    -- 查找或创建主菜单 (path = /user_tier/credit_transaction)
    SELECT id INTO v_menu_id FROM sys_menu 
    WHERE path = '/user_tier/credit_transaction' AND type = 1
    ORDER BY id LIMIT 1;
    
    IF v_menu_id IS NULL THEN
        INSERT INTO sys_menu (title, name, path, sort, icon, type, component, perms, status, display, cache, link, remark, parent_id, created_time, updated_time)
        VALUES ('积分交易记录管理', 'CreditTransaction', '/user_tier/credit_transaction', 1, 'lucide:list', 1, '/user_tier/credit_transaction/index', NULL, 1, 1, 1, '', '积分交易记录表 - 审计所有积分变动', v_parent_id, NOW(), NULL)
        RETURNING id INTO v_menu_id;
    ELSE
        UPDATE sys_menu SET
            title = '积分交易记录管理',
            name = 'CreditTransaction',
            component = '/user_tier/credit_transaction/index',
            remark = '积分交易记录表 - 审计所有积分变动',
            parent_id = v_parent_id,
            updated_time = NOW()
        WHERE id = v_menu_id;
    END IF;

    -- 新增按钮（按 perms 判断）
    IF NOT EXISTS (SELECT 1 FROM sys_menu WHERE perms = 'credit:transaction:add' AND parent_id = v_menu_id) THEN
        INSERT INTO sys_menu (title, name, path, sort, icon, type, component, perms, status, display, cache, link, remark, parent_id, created_time, updated_time)
        VALUES ('新增', 'AddCreditTransaction', NULL, 1, NULL, 2, NULL, 'credit:transaction:add', 1, 0, 1, '', NULL, v_menu_id, NOW(), NULL);
    END IF;

    -- 编辑按钮
    IF NOT EXISTS (SELECT 1 FROM sys_menu WHERE perms = 'credit:transaction:edit' AND parent_id = v_menu_id) THEN
        INSERT INTO sys_menu (title, name, path, sort, icon, type, component, perms, status, display, cache, link, remark, parent_id, created_time, updated_time)
        VALUES ('编辑', 'EditCreditTransaction', NULL, 2, NULL, 2, NULL, 'credit:transaction:edit', 1, 0, 1, '', NULL, v_menu_id, NOW(), NULL);
    END IF;

    -- 删除按钮
    IF NOT EXISTS (SELECT 1 FROM sys_menu WHERE perms = 'credit:transaction:del' AND parent_id = v_menu_id) THEN
        INSERT INTO sys_menu (title, name, path, sort, icon, type, component, perms, status, display, cache, link, remark, parent_id, created_time, updated_time)
        VALUES ('删除', 'DeleteCreditTransaction', NULL, 3, NULL, 2, NULL, 'credit:transaction:del', 1, 0, 1, '', NULL, v_menu_id, NOW(), NULL);
    END IF;

    -- 查看按钮
    IF NOT EXISTS (SELECT 1 FROM sys_menu WHERE perms = 'credit:transaction:get' AND parent_id = v_menu_id) THEN
        INSERT INTO sys_menu (title, name, path, sort, icon, type, component, perms, status, display, cache, link, remark, parent_id, created_time, updated_time)
        VALUES ('查看', 'ViewCreditTransaction', NULL, 4, NULL, 2, NULL, 'credit:transaction:get', 1, 0, 1, '', NULL, v_menu_id, NOW(), NULL);
    END IF;
END $$;

-- =====================================================
-- 菜单生成完成
-- =====================================================
