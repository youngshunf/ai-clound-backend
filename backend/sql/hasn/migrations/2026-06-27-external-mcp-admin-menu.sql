-- =====================================================
-- 第三方 MCP 网关 · 平台管理端菜单（PostgreSQL，幂等）
-- P7-D admin 管理面：平台运营配置 system-origin 平台 MCP server（如企查查 qcc）——
--   注册 remote_service / 写·轮换·撤销平台 key / 配 per-owner 配额限流 / 自省 / 启停 / 删除。
--   后端 admin 路由：/api/v1/external_mcp/admin/servers（RBAC external_mcp:server:add/edit/del）
--   前端视图：apps/web-antdv-next/src/views/hasn/external_mcp/index.vue（route name=ExternalMcpAdmin）
-- 幂等键：菜单按 name/perms 唯一定位。
-- 事实源：docs/hasn-node设计文档/MCP统一工具体系/10-MCP网关与第三方MCP接入.md §7.2
--         + 实施/99-第三方MCP网关接入(P7)实施清单.md P7-D
-- =====================================================

DO $$
DECLARE
    v_parent_id INTEGER;  -- /hasn 顶级目录
    v_menu_id   INTEGER;  -- 第三方 MCP 网关 菜单
BEGIN
    -- 1) 定位 HASN 顶级目录（不存在则兜底创建）
    SELECT id INTO v_parent_id FROM sys_menu WHERE path = '/hasn' AND type = 0 ORDER BY id LIMIT 1;
    IF v_parent_id IS NULL THEN
        INSERT INTO sys_menu (title, name, path, sort, icon, type, component, perms, status, display, cache, link, remark, parent_id, created_time, updated_time)
        VALUES ('Hasn', 'Hasn', '/hasn', 1, 'lucide:folder', 0, 'BasicLayout', NULL, 1, 1, 1, '', 'hasn模块', NULL, NOW(), NULL)
        RETURNING id INTO v_parent_id;
    END IF;

    -- 2) 第三方 MCP 网关 菜单（name=ExternalMcpAdmin 幂等）
    SELECT id INTO v_menu_id FROM sys_menu WHERE name = 'ExternalMcpAdmin' ORDER BY id LIMIT 1;
    IF v_menu_id IS NULL THEN
        INSERT INTO sys_menu (title, name, path, sort, icon, type, component, perms, status, display, cache, link, remark, parent_id, created_time, updated_time)
        VALUES ('第三方 MCP 网关', 'ExternalMcpAdmin', '/hasn/external_mcp', 14, 'lucide:plug', 1, '/hasn/external_mcp/index', NULL, 1, 1, 1, '', '平台 MCP server 目录 + 平台 key + per-owner 配额（P7-D）', v_parent_id, NOW(), NULL)
        RETURNING id INTO v_menu_id;
    ELSE
        UPDATE sys_menu SET title = '第三方 MCP 网关', path = '/hasn/external_mcp', icon = 'lucide:plug',
                            type = 1, component = '/hasn/external_mcp/index', parent_id = v_parent_id, updated_time = NOW()
        WHERE id = v_menu_id;
    END IF;

    -- 3) 按钮级权限（与云端 admin RBAC RequestPermission 同口径）
    IF NOT EXISTS (SELECT 1 FROM sys_menu WHERE perms = 'external_mcp:server:add' AND parent_id = v_menu_id) THEN
        INSERT INTO sys_menu (title, name, path, sort, icon, type, component, perms, status, display, cache, link, remark, parent_id, created_time, updated_time)
        VALUES ('注册', 'AddExternalMcpServer', NULL, 1, NULL, 2, NULL, 'external_mcp:server:add', 1, 0, 1, '', NULL, v_menu_id, NOW(), NULL);
    END IF;

    IF NOT EXISTS (SELECT 1 FROM sys_menu WHERE perms = 'external_mcp:server:edit' AND parent_id = v_menu_id) THEN
        INSERT INTO sys_menu (title, name, path, sort, icon, type, component, perms, status, display, cache, link, remark, parent_id, created_time, updated_time)
        VALUES ('编辑（凭据/配额/启停/自省）', 'EditExternalMcpServer', NULL, 2, NULL, 2, NULL, 'external_mcp:server:edit', 1, 0, 1, '', NULL, v_menu_id, NOW(), NULL);
    END IF;

    IF NOT EXISTS (SELECT 1 FROM sys_menu WHERE perms = 'external_mcp:server:del' AND parent_id = v_menu_id) THEN
        INSERT INTO sys_menu (title, name, path, sort, icon, type, component, perms, status, display, cache, link, remark, parent_id, created_time, updated_time)
        VALUES ('删除', 'DeleteExternalMcpServer', NULL, 3, NULL, 2, NULL, 'external_mcp:server:del', 1, 0, 1, '', NULL, v_menu_id, NOW(), NULL);
    END IF;
END $$;

-- =====================================================
-- 完成：菜单「HASN / 第三方 MCP 网关」+ 三按钮权限
-- =====================================================
