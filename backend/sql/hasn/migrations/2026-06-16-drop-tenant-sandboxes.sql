-- =====================================================
-- 退役 hasn_tenant_sandboxes（public.hasn_tenant_sandboxes）
--
-- 背景：S3 多租户沙箱（tenant sandbox）功能从未建设——onboarding 仅有一处只读
--   stub（get_sandbox_summary，try/except 兜底恒返 None），无任何写入路径，
--   表恒空。本次随 model/crud/service/api 一并退役；onboarding 改 sandbox=None
--   （响应字段保留兼容 daemon SandboxSummary 解析，hasn-node 零改）。
--
-- ⚠️ 生产执行须经福仔停机窗口授权（与其它 schema 切换同一约定）。本机 dev 库已先行 DROP 验证。
--
-- 幂等：IF EXISTS + CASCADE（确认全库无外键指向后再放行；CASCADE 仅兜底）。
-- =====================================================

DROP TABLE IF EXISTS "public"."hasn_tenant_sandboxes" CASCADE;
