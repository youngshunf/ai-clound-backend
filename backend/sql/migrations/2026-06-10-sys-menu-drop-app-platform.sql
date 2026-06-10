-- =====================================================
-- 管理端 sys_menu 清理：删除「应用平台」整目录（已废弃功能）
-- =====================================================
-- 背景:
--   「应用平台」(App_platform, path=/app_platform) 是早期 HExt-08 AI-Native 应用平台
--   的管理端目录，功能已废弃（福仔 2026-06-10 确认）。子树含 1 目录 + 17 页面 + 68 按钮，
--   其中部分页面标题还残留 codegen 未替换的占位符（如「应用权限定义表（{domain}.*」）。
--
-- 处理: 删除 /app_platform 整个子树 + 清理 sys_role_menu 悬空引用。
--
-- 安全性: 子树未被任何角色(sys_role_menu)引用（超管 is_superuser 绕过 RBAC）。
--          删除仅影响管理端导航展示，不影响后端 API / 数据。
-- 幂等: 按 path 匹配，可在 local(15432) 与生产(5432) 重复执行。
-- 关联: 同日 2026-06-10-sys-menu-cleanup.sql（修 Deck 路由名冲突等）。
-- =====================================================

BEGIN;

WITH RECURSIVE root AS (
    SELECT id FROM sys_menu
    WHERE parent_id IS NULL AND type = 0 AND path = '/app_platform'
),
subtree AS (
    SELECT id FROM sys_menu WHERE id IN (SELECT id FROM root)
    UNION ALL
    SELECT m.id FROM sys_menu m JOIN subtree s ON m.parent_id = s.id
)
DELETE FROM sys_menu WHERE id IN (SELECT id FROM subtree);

-- 清理 sys_role_menu 中指向已删除菜单的悬空引用
DELETE FROM sys_role_menu rm
WHERE NOT EXISTS (SELECT 1 FROM sys_menu m WHERE m.id = rm.menu_id);

COMMIT;
