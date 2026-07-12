-- 内置分身收敛为 3 个（2026-07-12，福仔拍板）：全能助理 assistant / 创作专家 content_operator / 分析专家 analyst。
-- 原 8 个 builtin 分身里的 5 个退为「可选市场模板」（不再 builtin，模板本身保留、用户仍可手动创建）；
-- 其对应应用的默认承接分身改指 3 个内置之一。work_session_system_prompt（每应用业务提示词）不动——
-- 只换承接分身，不改每应用的执行提示词。出厂源 _CATALOG_AGENT_DEFAULTS 已同步（app_catalog_service.py）。
--
--   退 builtin → 应用改指承接：
--     planner        → plan          改指 assistant
--     meeting_copilot→ copilot       改指 assistant
--     sales_advisor  → growth        改指 assistant（当商务助理）
--     developer      → publish       改指 content_operator
--     designer       → designsystem / design 改指 content_operator
--
-- 幂等 + 保护运营改动：catalog 仅在当前仍是旧值时改（运营若已自定义则不覆盖）。
-- 安全：reconcile_builtin_agents 是 INSERT-only 且按 marketplace_template.builtin 过滤——
-- 去掉这 5 个模板的 builtin 标记后，新用户 onboarding 只建 3 个内置；已存在的分身（存量用户名下的
-- 规划参谋/设计专家等）按各自 builtin_agent_key 独立保留，不随本迁移消失。

-- ① marketplace_template：5 个模板去 builtin 标记（退为可选市场模板）。
--    github_app_sync 后续再同步 hub yaml（已删 builtin）会保持一致；此处让存量 prod 库即时生效。
UPDATE hasn_marketplace.marketplace_template
SET builtin = FALSE, builtin_key = NULL
WHERE template_id IN (
    'huanxing/agent/planner',
    'huanxing/agent/meeting-copilot',
    'huanxing/agent/designer',
    'huanxing/agent/developer',
    'huanxing/agent/sales-advisor'
);

-- ② hasn_app_catalog：6 应用默认承接分身重指 3 个内置（仅当仍是旧值时改，保护运营自定义）。
UPDATE hasn_app_catalog SET default_agent_type = 'assistant'
WHERE app_id = 'plan'         AND default_agent_type = 'planner';

UPDATE hasn_app_catalog SET default_agent_type = 'assistant'
WHERE app_id = 'copilot'      AND default_agent_type = 'meeting_copilot';

UPDATE hasn_app_catalog SET default_agent_type = 'assistant'
WHERE app_id = 'growth'       AND default_agent_type = 'sales_advisor';

UPDATE hasn_app_catalog SET default_agent_type = 'content_operator'
WHERE app_id = 'publish'      AND default_agent_type = 'developer';

UPDATE hasn_app_catalog SET default_agent_type = 'content_operator'
WHERE app_id = 'designsystem' AND default_agent_type = 'designer';

UPDATE hasn_app_catalog SET default_agent_type = 'content_operator'
WHERE app_id = 'design'       AND default_agent_type = 'designer';
