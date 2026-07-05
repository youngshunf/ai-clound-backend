-- =====================================================
-- 桌面端发布管理（管理端控制台）菜单 + 权限点 初始化 SQL (PostgreSQL)
-- 对应前端页面 /hasn_release/release_console/index，后端 API /api/v1/release/admin/*。
-- 4 个权限点对齐 admin/release.py 的 RequestPermission 声明：
--   release:publish（发布/手动登记）· release:edit（编辑changelog/设为最新）
--   release:del（删除版本）· release:build（触发 GitHub 构建）
-- 支持幂等：已存在则更新，不存在则新增。
-- =====================================================

DO $$
DECLARE
    v_parent_id INTEGER;
    v_menu_id INTEGER;
BEGIN
    -- 父级目录（path = /hasn_release）
    SELECT id INTO v_parent_id FROM sys_menu
    WHERE path = '/hasn_release' AND type = 0
    ORDER BY id LIMIT 1;

    IF v_parent_id IS NULL THEN
        INSERT INTO sys_menu (title, name, path, sort, icon, type, component, perms, status, display, cache, link, remark, parent_id, created_time, updated_time)
        VALUES ('桌面端发布', 'Hasn_release', '/hasn_release', 20, 'lucide:package', 0, 'BasicLayout', NULL, 1, 1, 1, '', '桌面端发布与自动更新', NULL, NOW(), NULL)
        RETURNING id INTO v_parent_id;
    END IF;

    -- 主菜单（发布管理控制台）
    SELECT id INTO v_menu_id FROM sys_menu
    WHERE path = '/hasn_release/release_console' AND type = 1
    ORDER BY id LIMIT 1;

    IF v_menu_id IS NULL THEN
        INSERT INTO sys_menu (title, name, path, sort, icon, type, component, perms, status, display, cache, link, remark, parent_id, created_time, updated_time)
        VALUES ('发布管理', 'ReleaseConsole', '/hasn_release/release_console', 1, 'lucide:rocket', 1, '/hasn_release/release_console/index', NULL, 1, 1, 1, '', '桌面端版本发布管理：版本列表/GitHub 构建/手动登记/设为最新/编辑 changelog/下线/删除 + 构建任务进度', v_parent_id, NOW(), NULL)
        RETURNING id INTO v_menu_id;
    ELSE
        UPDATE sys_menu SET
            title = '发布管理',
            name = 'ReleaseConsole',
            component = '/hasn_release/release_console/index',
            icon = 'lucide:rocket',
            remark = '桌面端版本发布管理：版本列表/GitHub 构建/手动登记/设为最新/编辑 changelog/下线/删除 + 构建任务进度',
            parent_id = v_parent_id,
            updated_time = NOW()
        WHERE id = v_menu_id;
    END IF;

    -- 权限点：发布 / 手动登记（release:publish）
    IF NOT EXISTS (SELECT 1 FROM sys_menu WHERE perms = 'release:publish' AND parent_id = v_menu_id) THEN
        INSERT INTO sys_menu (title, name, path, sort, icon, type, component, perms, status, display, cache, link, remark, parent_id, created_time, updated_time)
        VALUES ('发布/手动登记', 'ReleasePublish', NULL, 1, NULL, 2, NULL, 'release:publish', 1, 0, 1, '', '手动上传发布新版本（回传七牛资产元数据 + .sig）', v_menu_id, NOW(), NULL);
    END IF;

    -- 权限点：编辑 changelog / 设为最新（release:edit）
    IF NOT EXISTS (SELECT 1 FROM sys_menu WHERE perms = 'release:edit' AND parent_id = v_menu_id) THEN
        INSERT INTO sys_menu (title, name, path, sort, icon, type, component, perms, status, display, cache, link, remark, parent_id, created_time, updated_time)
        VALUES ('编辑/设为最新', 'ReleaseEdit', NULL, 2, NULL, 2, NULL, 'release:edit', 1, 0, 1, '', '编辑 changelog/状态、置为当前 channel 最新（回滚/切换）', v_menu_id, NOW(), NULL);
    END IF;

    -- 权限点：删除版本（release:del）
    IF NOT EXISTS (SELECT 1 FROM sys_menu WHERE perms = 'release:del' AND parent_id = v_menu_id) THEN
        INSERT INTO sys_menu (title, name, path, sort, icon, type, component, perms, status, display, cache, link, remark, parent_id, created_time, updated_time)
        VALUES ('删除版本', 'ReleaseDel', NULL, 3, NULL, 2, NULL, 'release:del', 1, 0, 1, '', '删除版本（级联删资产）', v_menu_id, NOW(), NULL);
    END IF;

    -- 权限点：触发 GitHub 构建（release:build）
    IF NOT EXISTS (SELECT 1 FROM sys_menu WHERE perms = 'release:build' AND parent_id = v_menu_id) THEN
        INSERT INTO sys_menu (title, name, path, sort, icon, type, component, perms, status, display, cache, link, remark, parent_id, created_time, updated_time)
        VALUES ('GitHub 构建', 'ReleaseBuild', NULL, 4, NULL, 2, NULL, 'release:build', 1, 0, 1, '', '从 GitHub 自动构建（触发 workflow_dispatch）', v_menu_id, NOW(), NULL);
    END IF;
END $$;
