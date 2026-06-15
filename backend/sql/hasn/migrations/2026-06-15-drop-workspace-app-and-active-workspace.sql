-- =====================================================
-- 应用平台 v3 P3：退役两张 workspace 旧表（设计 17 决策①②）
--
-- 决策①「应用一律开箱即用」→ 挂载概念废除 → 删 hasn_workspace_app。
-- 决策②「彻底废 workspace 重实体」→ 身份上下文收缩为
--   hasn_workbench.hasn_owner_workbench_pref.active_enterprise_id 瘦指针
--   （已由 P3-A 迁移 add-active-enterprise-id.sql 落地 + 回填）→ 删 hasn_user_active_workspace。
--
-- ⚠️ 生产执行须经福仔停机窗口授权（与 deck/billing/community/marketplace/workbench
--    schema 切换同一约定）。本机 dev 库已先行 DROP 验证。
--
-- 幂等：IF EXISTS + CASCADE（确认全库无外键指向这两表后再放行；CASCADE 仅兜底）。
-- =====================================================

DROP TABLE IF EXISTS "public"."hasn_workspace_app" CASCADE;
DROP TABLE IF EXISTS "public"."hasn_user_active_workspace" CASCADE;
