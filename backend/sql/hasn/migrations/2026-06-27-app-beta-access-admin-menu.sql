-- =====================================================
-- AI-Native 应用：灰度内测访问 管理端菜单（PostgreSQL，幂等）
-- APPBETA-2 管理面：审批用户内测申请 / 主动邀请用户 / 撤销访问。
--   后端 admin 路由：/api/v1/app-beta-access（list/invite/{pk}/approve|reject/delete，admin scope）
--   前端视图：apps/web-antdv-next/src/views/hasn/hasn_app_beta_access/index.vue
-- 幂等键：菜单按 name 唯一定位（route name = 视图 defineOptions name = HasnAppBetaAccess）。
-- 说明：codegen 生成的 hasn_app_beta_access_menu.sql 标题为通用「AI-Native管理」且平铺到 /hasn，
--       本迁移收口到既有「AI-Native 应用」子目录（同 2026-06-10-ai-native-app-admin-menu.sql 的 catalog/entitlement）。
-- =====================================================

DO $$
DECLARE
    v_hasn_id  INTEGER;  -- /hasn 顶级目录
    v_group_id INTEGER;  -- AI-Native 应用 子目录
    v_menu_id  INTEGER;  -- 灰度内测访问 菜单
BEGIN
    -- 1) 定位 HASN 顶级目录（不存在则兜底创建）
    SELECT id INTO v_hasn_id FROM sys_menu WHERE path = '/hasn' AND type = 0 ORDER BY id LIMIT 1;
    IF v_hasn_id IS NULL THEN
        INSERT INTO sys_menu (title, name, path, sort, icon, type, component, perms, status, display, cache, link, remark, parent_id, created_time, updated_time)
        VALUES ('HASN社交网络', 'Hasn', '/hasn', 80, 'lucide:network', 0, 'BasicLayout', NULL, 1, 1, 1, '', 'HASN 模块', NULL, NOW(), NULL)
        RETURNING id INTO v_hasn_id;
    END IF;

    -- 2) AI-Native 应用 子目录（按 name 幂等；与 catalog/entitlement 同组）
    SELECT id INTO v_group_id FROM sys_menu WHERE name = 'HasnAiNativeApp' ORDER BY id LIMIT 1;
    IF v_group_id IS NULL THEN
        INSERT INTO sys_menu (title, name, path, sort, icon, type, component, perms, status, display, cache, link, remark, parent_id, created_time, updated_time)
        VALUES ('AI-Native 应用', 'HasnAiNativeApp', '/hasn/ai_native', 13, 'lucide:layout-grid', 0, NULL, NULL, 1, 1, 1, '', 'AI-Native 应用目录与商业化管理', v_hasn_id, NOW(), NULL)
        RETURNING id INTO v_group_id;
    END IF;

    -- 3) 灰度内测访问 菜单（name=HasnAppBetaAccess 幂等）
    SELECT id INTO v_menu_id FROM sys_menu WHERE name = 'HasnAppBetaAccess' ORDER BY id LIMIT 1;
    IF v_menu_id IS NULL THEN
        INSERT INTO sys_menu (title, name, path, sort, icon, type, component, perms, status, display, cache, link, remark, parent_id, created_time, updated_time)
        VALUES ('灰度内测', 'HasnAppBetaAccess', '/hasn/hasn_app_beta_access', 3, 'lucide:flask-conical', 1, '/hasn/hasn_app_beta_access/index', NULL, 1, 1, 1, '', 'AI-Native 应用灰度内测访问（审批 / 邀请）', v_group_id, NOW(), NULL)
        RETURNING id INTO v_menu_id;
    ELSE
        UPDATE sys_menu SET title = '灰度内测', path = '/hasn/hasn_app_beta_access', icon = 'lucide:flask-conical',
                            type = 1, component = '/hasn/hasn_app_beta_access/index', parent_id = v_group_id, updated_time = NOW()
        WHERE id = v_menu_id;
    END IF;
END $$;

-- =====================================================
-- 完成：菜单「AI-Native 应用 / 灰度内测」
-- =====================================================
