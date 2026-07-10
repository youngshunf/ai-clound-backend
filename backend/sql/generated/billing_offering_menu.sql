-- =====================================================
-- 商品目录（一切可售卖物：LLM档/积分包/应用/席位/应用内档位）管理 菜单初始化 SQL (PostgreSQL)
-- 自动生成于: 2026-07-10 04:41:45.317444
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

    -- 查找或创建主菜单 (path = /billing/billing_offering)
    SELECT id INTO v_menu_id FROM sys_menu 
    WHERE path = '/billing/billing_offering' AND type = 1
    ORDER BY id LIMIT 1;
    
    IF v_menu_id IS NULL THEN
        INSERT INTO sys_menu (title, name, path, sort, icon, type, component, perms, status, display, cache, link, remark, parent_id, created_time, updated_time)
        VALUES ('商品目录（一切可售卖物：LLM档/积分包/应用/席位/应用内档位）管理', 'BillingOffering', '/billing/billing_offering', 1, 'lucide:list', 1, '/billing/billing_offering/index', NULL, 1, 1, 1, '', '商品目录（一切可售卖物：LLM档/积分包/应用/席位/应用内档位）', v_parent_id, NOW(), NULL)
        RETURNING id INTO v_menu_id;
    ELSE
        UPDATE sys_menu SET
            title = '商品目录（一切可售卖物：LLM档/积分包/应用/席位/应用内档位）管理',
            name = 'BillingOffering',
            component = '/billing/billing_offering/index',
            remark = '商品目录（一切可售卖物：LLM档/积分包/应用/席位/应用内档位）',
            parent_id = v_parent_id,
            updated_time = NOW()
        WHERE id = v_menu_id;
    END IF;

    -- 新增按钮（按 perms 判断）
    IF NOT EXISTS (SELECT 1 FROM sys_menu WHERE perms = 'billing:offering:add' AND parent_id = v_menu_id) THEN
        INSERT INTO sys_menu (title, name, path, sort, icon, type, component, perms, status, display, cache, link, remark, parent_id, created_time, updated_time)
        VALUES ('新增', 'AddBillingOffering', NULL, 1, NULL, 2, NULL, 'billing:offering:add', 1, 0, 1, '', NULL, v_menu_id, NOW(), NULL);
    END IF;

    -- 编辑按钮
    IF NOT EXISTS (SELECT 1 FROM sys_menu WHERE perms = 'billing:offering:edit' AND parent_id = v_menu_id) THEN
        INSERT INTO sys_menu (title, name, path, sort, icon, type, component, perms, status, display, cache, link, remark, parent_id, created_time, updated_time)
        VALUES ('编辑', 'EditBillingOffering', NULL, 2, NULL, 2, NULL, 'billing:offering:edit', 1, 0, 1, '', NULL, v_menu_id, NOW(), NULL);
    END IF;

    -- 删除按钮
    IF NOT EXISTS (SELECT 1 FROM sys_menu WHERE perms = 'billing:offering:del' AND parent_id = v_menu_id) THEN
        INSERT INTO sys_menu (title, name, path, sort, icon, type, component, perms, status, display, cache, link, remark, parent_id, created_time, updated_time)
        VALUES ('删除', 'DeleteBillingOffering', NULL, 3, NULL, 2, NULL, 'billing:offering:del', 1, 0, 1, '', NULL, v_menu_id, NOW(), NULL);
    END IF;

    -- 查看按钮
    IF NOT EXISTS (SELECT 1 FROM sys_menu WHERE perms = 'billing:offering:get' AND parent_id = v_menu_id) THEN
        INSERT INTO sys_menu (title, name, path, sort, icon, type, component, perms, status, display, cache, link, remark, parent_id, created_time, updated_time)
        VALUES ('查看', 'ViewBillingOffering', NULL, 4, NULL, 2, NULL, 'billing:offering:get', 1, 0, 1, '', NULL, v_menu_id, NOW(), NULL);
    END IF;
END $$;

-- =====================================================
-- 菜单生成完成
-- =====================================================
