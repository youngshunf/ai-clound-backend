-- =====================================================
-- 管理端 sys_menu 清理：删除「外部桥接」「集成管理」整目录（已废弃功能）
-- =====================================================
-- 背景:
--   「外部桥接」(Bridge, /bridge) 与「集成管理」(Integration, /integration) 为早期
--   HExt-09 外部应用桥接 / 第三方集成的管理端目录，功能已废弃（福仔 2026-06-11 确认）。
--   - Bridge      子树: 1 目录 + 6 页面 + 24 按钮（含未替换占位符标题）
--   - Integration 子树: 1 目录 + 2 页面 + 8 按钮
--
-- 处理: 删除 /bridge、/integration 整子树 + 清理 sys_role_menu 悬空引用。
--
-- 安全性: 两子树均未被任何角色(sys_role_menu)引用（超管 is_superuser 绕过 RBAC）。
--          删除仅影响管理端导航展示，不影响后端 API / 数据。
-- 幂等: 按 path 匹配，可在 local(15432) 与生产(5432) 重复执行。
-- 关联: 2026-06-10-sys-menu-cleanup.sql、2026-06-10-sys-menu-drop-app-platform.sql。
-- =====================================================

BEGIN;

WITH RECURSIVE roots AS (
    SELECT id FROM sys_menu
    WHERE parent_id IS NULL AND type = 0 AND path IN ('/bridge', '/integration')
),
subtree AS (
    SELECT id FROM sys_menu WHERE id IN (SELECT id FROM roots)
    UNION ALL
    SELECT m.id FROM sys_menu m JOIN subtree s ON m.parent_id = s.id
)
DELETE FROM sys_menu WHERE id IN (SELECT id FROM subtree);

-- 清理 sys_role_menu 中指向已删除菜单的悬空引用
DELETE FROM sys_role_menu rm
WHERE NOT EXISTS (SELECT 1 FROM sys_menu m WHERE m.id = rm.menu_id);

COMMIT;
