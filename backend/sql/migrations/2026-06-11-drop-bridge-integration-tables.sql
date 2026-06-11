-- =====================================================
-- 删除已废弃的「外部桥接」(Bridge) /「集成管理」(Integration) 数据表
-- =====================================================
-- 背景:
--   Bridge(HExt-09 外部应用桥接) 与 Integration(第三方应用集成) 功能已废弃
--   （福仔 2026-06-11 确认，菜单已先行删除，相关后端模块/前端页面同步删除）。
--   - Bridge 后端无独立 app 模块，仅遗留 6 张表（建表由历史 codegen 直接执行，无 SQL 留档）。
--   - Integration 后端模块 app/integration 已整体删除（router 注册同步移除）。
--
-- 删除的表（删除前均为 0 行，integration_apps 仅 1 行测试数据，且无任何外键引用）:
--   bridge_api_keys / bridge_applications / bridge_audit_logs /
--   bridge_sso_tokens / bridge_webhooks / user_identity_mappings /
--   integration_apps / integration_credentials
--
-- 安全性: 8 张表均无被其它表外键引用，删除不影响任何现存业务。
-- 幂等: DROP TABLE IF EXISTS，可在 local(15432) 与生产(5432) 重复执行。
-- 关联: 2026-06-11-sys-menu-drop-bridge-integration.sql（先行删除两目录菜单）。
-- =====================================================

BEGIN;

DROP TABLE IF EXISTS bridge_api_keys CASCADE;
DROP TABLE IF EXISTS bridge_applications CASCADE;
DROP TABLE IF EXISTS bridge_audit_logs CASCADE;
DROP TABLE IF EXISTS bridge_sso_tokens CASCADE;
DROP TABLE IF EXISTS bridge_webhooks CASCADE;
DROP TABLE IF EXISTS user_identity_mappings CASCADE;
DROP TABLE IF EXISTS integration_apps CASCADE;
DROP TABLE IF EXISTS integration_credentials CASCADE;

COMMIT;
