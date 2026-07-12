-- =====================================================
-- 素材站目录 · 平台管理端菜单（PostgreSQL，幂等）
-- A-P2-0 admin 管理面：平台运营配置素材站（pexels/pixabay/coverr/…）——
--   注册 / 写·轮换·清空 api_key（明文只进不出）/ 启停 / 配 failover priority / 维护下载白名单域名。
--   后端 admin 路由：/api/v1/hasn_stock/providers（RBAC hasn_stock:provider:add/edit/del）
--   前端视图：apps/web-antdv-next/src/views/hasn/hasn_stock_providers/index.vue（route name=HasnStockProviders）
-- 幂等键：菜单按 name/perms 唯一定位。
-- 事实源：docs/Agent产物系统/01-分身资源检索与素材站工具设计.md §4.5/§7
-- =====================================================

DO $$
DECLARE
    v_parent_id INTEGER;  -- /hasn 顶级目录
    v_menu_id   INTEGER;  -- 素材站目录 菜单
BEGIN
    -- 1) 定位 HASN 顶级目录（不存在则兜底创建）
    SELECT id INTO v_parent_id FROM sys_menu WHERE path = '/hasn' AND type = 0 ORDER BY id LIMIT 1;
    IF v_parent_id IS NULL THEN
        INSERT INTO sys_menu (title, name, path, sort, icon, type, component, perms, status, display, cache, link, remark, parent_id, created_time, updated_time)
        VALUES ('Hasn', 'Hasn', '/hasn', 1, 'lucide:folder', 0, 'BasicLayout', NULL, 1, 1, 1, '', 'hasn模块', NULL, NOW(), NULL)
        RETURNING id INTO v_parent_id;
    END IF;

    -- 2) 素材站目录 菜单（name=HasnStockProviders 幂等）
    SELECT id INTO v_menu_id FROM sys_menu WHERE name = 'HasnStockProviders' ORDER BY id LIMIT 1;
    IF v_menu_id IS NULL THEN
        INSERT INTO sys_menu (title, name, path, sort, icon, type, component, perms, status, display, cache, link, remark, parent_id, created_time, updated_time)
        VALUES ('素材站目录', 'HasnStockProviders', '/hasn/hasn_stock_providers', 16, 'lucide:image', 1, '/hasn/hasn_stock_providers/index', NULL, 1, 1, 1, '', '素材站目录（provider/media_types/api_key/下载白名单/failover 优先级）', v_parent_id, NOW(), NULL)
        RETURNING id INTO v_menu_id;
    ELSE
        UPDATE sys_menu SET title = '素材站目录', path = '/hasn/hasn_stock_providers', icon = 'lucide:image',
                            type = 1, component = '/hasn/hasn_stock_providers/index', parent_id = v_parent_id, updated_time = NOW()
        WHERE id = v_menu_id;
    END IF;

    -- 3) 按钮级权限（与云端 admin RBAC RequestPermission 同口径）
    IF NOT EXISTS (SELECT 1 FROM sys_menu WHERE perms = 'hasn_stock:provider:add' AND parent_id = v_menu_id) THEN
        INSERT INTO sys_menu (title, name, path, sort, icon, type, component, perms, status, display, cache, link, remark, parent_id, created_time, updated_time)
        VALUES ('新增', 'AddHasnStockProvider', NULL, 1, NULL, 2, NULL, 'hasn_stock:provider:add', 1, 0, 1, '', NULL, v_menu_id, NOW(), NULL);
    END IF;

    IF NOT EXISTS (SELECT 1 FROM sys_menu WHERE perms = 'hasn_stock:provider:edit' AND parent_id = v_menu_id) THEN
        INSERT INTO sys_menu (title, name, path, sort, icon, type, component, perms, status, display, cache, link, remark, parent_id, created_time, updated_time)
        VALUES ('编辑（api_key/白名单/启停/优先级）', 'EditHasnStockProvider', NULL, 2, NULL, 2, NULL, 'hasn_stock:provider:edit', 1, 0, 1, '', NULL, v_menu_id, NOW(), NULL);
    END IF;

    IF NOT EXISTS (SELECT 1 FROM sys_menu WHERE perms = 'hasn_stock:provider:del' AND parent_id = v_menu_id) THEN
        INSERT INTO sys_menu (title, name, path, sort, icon, type, component, perms, status, display, cache, link, remark, parent_id, created_time, updated_time)
        VALUES ('删除', 'DeleteHasnStockProvider', NULL, 3, NULL, 2, NULL, 'hasn_stock:provider:del', 1, 0, 1, '', NULL, v_menu_id, NOW(), NULL);
    END IF;
END $$;

-- =====================================================
-- 完成：菜单「HASN / 素材站目录」+ 三按钮权限（新增/编辑/删除）
-- =====================================================
