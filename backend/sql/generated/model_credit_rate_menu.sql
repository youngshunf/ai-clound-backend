-- =====================================================
-- 模型积分费率管理 菜单初始化 SQL (PostgreSQL)
-- 自动生成于: 2026-01-28 15:41:20.955243
-- 支持幂等操作：已存在则更新，不存在则新增
-- =====================================================

DO $$
DECLARE
    v_parent_id INTEGER;
    v_menu_id INTEGER;
BEGIN
    -- 查找或创建父级目录菜单 (path = /llm)
    SELECT id INTO v_parent_id FROM sys_menu 
    WHERE path = '/llm' AND type = 0
    ORDER BY id LIMIT 1;
    
    IF v_parent_id IS NULL THEN
        INSERT INTO sys_menu (title, name, path, sort, icon, type, component, perms, status, display, cache, link, remark, parent_id, created_time, updated_time)
        VALUES ('Llm', 'Llm', '/llm', 1, 'lucide:folder', 0, 'BasicLayout', NULL, 1, 1, 1, '', 'llm模块', NULL, NOW(), NULL)
        RETURNING id INTO v_parent_id;
    END IF;

    -- 查找或创建主菜单 (path = /llm/model_credit_rate)
    SELECT id INTO v_menu_id FROM sys_menu 
    WHERE path = '/llm/model_credit_rate' AND type = 1
    ORDER BY id LIMIT 1;
    
    IF v_menu_id IS NULL THEN
        INSERT INTO sys_menu (title, name, path, sort, icon, type, component, perms, status, display, cache, link, remark, parent_id, created_time, updated_time)
        VALUES ('模型积分费率管理', 'ModelCreditRate', '/llm/model_credit_rate', 1, 'lucide:list', 1, '/llm/model_credit_rate/index', NULL, 1, 1, 1, '', '模型积分费率表 - 定义不同模型的积分消耗规则', v_parent_id, NOW(), NULL)
        RETURNING id INTO v_menu_id;
    ELSE
        UPDATE sys_menu SET
            title = '模型积分费率管理',
            name = 'ModelCreditRate',
            component = '/llm/model_credit_rate/index',
            remark = '模型积分费率表 - 定义不同模型的积分消耗规则',
            parent_id = v_parent_id,
            updated_time = NOW()
        WHERE id = v_menu_id;
    END IF;

    -- 新增按钮（按 perms 判断）
    IF NOT EXISTS (SELECT 1 FROM sys_menu WHERE perms = 'model:credit:rate:add' AND parent_id = v_menu_id) THEN
        INSERT INTO sys_menu (title, name, path, sort, icon, type, component, perms, status, display, cache, link, remark, parent_id, created_time, updated_time)
        VALUES ('新增', 'AddModelCreditRate', NULL, 1, NULL, 2, NULL, 'model:credit:rate:add', 1, 0, 1, '', NULL, v_menu_id, NOW(), NULL);
    END IF;

    -- 编辑按钮
    IF NOT EXISTS (SELECT 1 FROM sys_menu WHERE perms = 'model:credit:rate:edit' AND parent_id = v_menu_id) THEN
        INSERT INTO sys_menu (title, name, path, sort, icon, type, component, perms, status, display, cache, link, remark, parent_id, created_time, updated_time)
        VALUES ('编辑', 'EditModelCreditRate', NULL, 2, NULL, 2, NULL, 'model:credit:rate:edit', 1, 0, 1, '', NULL, v_menu_id, NOW(), NULL);
    END IF;

    -- 删除按钮
    IF NOT EXISTS (SELECT 1 FROM sys_menu WHERE perms = 'model:credit:rate:del' AND parent_id = v_menu_id) THEN
        INSERT INTO sys_menu (title, name, path, sort, icon, type, component, perms, status, display, cache, link, remark, parent_id, created_time, updated_time)
        VALUES ('删除', 'DeleteModelCreditRate', NULL, 3, NULL, 2, NULL, 'model:credit:rate:del', 1, 0, 1, '', NULL, v_menu_id, NOW(), NULL);
    END IF;

    -- 查看按钮
    IF NOT EXISTS (SELECT 1 FROM sys_menu WHERE perms = 'model:credit:rate:get' AND parent_id = v_menu_id) THEN
        INSERT INTO sys_menu (title, name, path, sort, icon, type, component, perms, status, display, cache, link, remark, parent_id, created_time, updated_time)
        VALUES ('查看', 'ViewModelCreditRate', NULL, 4, NULL, 2, NULL, 'model:credit:rate:get', 1, 0, 1, '', NULL, v_menu_id, NOW(), NULL);
    END IF;
END $$;

-- =====================================================
-- 菜单生成完成
-- =====================================================
