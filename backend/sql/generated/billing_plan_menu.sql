-- =====================================================
-- 商品档位（价格+配额快照+试用/宽限策略）管理 菜单初始化 SQL (PostgreSQL)
-- 自动生成于: 2026-07-10 04:43:22.676260
-- 支持幂等操作：已存在则更新，不存在则新增
-- =====================================================

DO $$
DECLARE
    v_parent_id INTEGER;
    v_menu_id INTEGER;
BEGIN
    -- 查找或创建父级目录菜单 (path = /billing)
    SELECT id INTO v_parent_id FROM sys_menu 
    WHERE path = '/billing' AND type = 0
    ORDER BY id LIMIT 1;
    
    IF v_parent_id IS NULL THEN
        INSERT INTO sys_menu (title, name, path, sort, icon, type, component, perms, status, display, cache, link, remark, parent_id, created_time, updated_time)
        VALUES ('Billing', 'Billing', '/billing', 1, 'lucide:folder', 0, 'BasicLayout', NULL, 1, 1, 1, '', 'billing模块', NULL, NOW(), NULL)
        RETURNING id INTO v_parent_id;
    END IF;

    -- 查找或创建主菜单 (path = /billing/billing_plan)
    SELECT id INTO v_menu_id FROM sys_menu 
    WHERE path = '/billing/billing_plan' AND type = 1
    ORDER BY id LIMIT 1;
    
    IF v_menu_id IS NULL THEN
        INSERT INTO sys_menu (title, name, path, sort, icon, type, component, perms, status, display, cache, link, remark, parent_id, created_time, updated_time)
        VALUES ('商品档位（价格+配额快照+试用/宽限策略）管理', 'BillingPlan', '/billing/billing_plan', 1, 'lucide:list', 1, '/billing/billing_plan/index', NULL, 1, 1, 1, '', '商品档位（价格+配额快照+试用/宽限策略）', v_parent_id, NOW(), NULL)
        RETURNING id INTO v_menu_id;
    ELSE
        UPDATE sys_menu SET
            title = '商品档位（价格+配额快照+试用/宽限策略）管理',
            name = 'BillingPlan',
            component = '/billing/billing_plan/index',
            remark = '商品档位（价格+配额快照+试用/宽限策略）',
            parent_id = v_parent_id,
            updated_time = NOW()
        WHERE id = v_menu_id;
    END IF;

    -- 新增按钮（按 perms 判断）
    IF NOT EXISTS (SELECT 1 FROM sys_menu WHERE perms = 'billing:plan:add' AND parent_id = v_menu_id) THEN
        INSERT INTO sys_menu (title, name, path, sort, icon, type, component, perms, status, display, cache, link, remark, parent_id, created_time, updated_time)
        VALUES ('新增', 'AddBillingPlan', NULL, 1, NULL, 2, NULL, 'billing:plan:add', 1, 0, 1, '', NULL, v_menu_id, NOW(), NULL);
    END IF;

    -- 编辑按钮
    IF NOT EXISTS (SELECT 1 FROM sys_menu WHERE perms = 'billing:plan:edit' AND parent_id = v_menu_id) THEN
        INSERT INTO sys_menu (title, name, path, sort, icon, type, component, perms, status, display, cache, link, remark, parent_id, created_time, updated_time)
        VALUES ('编辑', 'EditBillingPlan', NULL, 2, NULL, 2, NULL, 'billing:plan:edit', 1, 0, 1, '', NULL, v_menu_id, NOW(), NULL);
    END IF;

    -- 删除按钮
    IF NOT EXISTS (SELECT 1 FROM sys_menu WHERE perms = 'billing:plan:del' AND parent_id = v_menu_id) THEN
        INSERT INTO sys_menu (title, name, path, sort, icon, type, component, perms, status, display, cache, link, remark, parent_id, created_time, updated_time)
        VALUES ('删除', 'DeleteBillingPlan', NULL, 3, NULL, 2, NULL, 'billing:plan:del', 1, 0, 1, '', NULL, v_menu_id, NOW(), NULL);
    END IF;

    -- 查看按钮
    IF NOT EXISTS (SELECT 1 FROM sys_menu WHERE perms = 'billing:plan:get' AND parent_id = v_menu_id) THEN
        INSERT INTO sys_menu (title, name, path, sort, icon, type, component, perms, status, display, cache, link, remark, parent_id, created_time, updated_time)
        VALUES ('查看', 'ViewBillingPlan', NULL, 4, NULL, 2, NULL, 'billing:plan:get', 1, 0, 1, '', NULL, v_menu_id, NOW(), NULL);
    END IF;
END $$;

-- =====================================================
-- 菜单生成完成
-- =====================================================
