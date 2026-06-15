-- =====================================================
-- new-api 解耦迁移 P6：删除自建 LLM 网关遗留表 + 模型积分费率（D3）+ 管理端菜单
-- =====================================================
-- 背景:
--   2026-06-15 new-api 解耦：删除自建 LLM 网关 app/llm（路由/服务/模型全部下线，
--   /api/v1/llm/* 的「存活」路径 usage/summary + models/available 已由 app/newapi 接管），
--   并删除仅服务于网关计费的模型积分费率 model_credit_rate（D3，扣费改 new-api 用量对账）。
--
-- 删除范围:
--   public 网关表 8 张: llm_model_provider / llm_model_config / llm_model_alias /
--     llm_model_group / llm_rate_limit_config / llm_usage_log / llm_compress_usage_log / llm_media_task
--   hasn_billing.model_credit_rate（D3）
--   管理端菜单: /llm 下 provider/model/model-group/rate-limit/usage/model-alias 子树
--     + user_tier 下 model_credit_rate 子树（含按钮）
--
-- 明确保留（不在删除范围）:
--   表 llm_user_api_key（D1 自建 API Key，已迁 app/newapi/apikey，openclaw/marketplace/billing 仍用）
--   表 llm_newapi_user_mapping（D5 唤星↔new-api 用户映射，仍用）
--   菜单 /llm 父级 + LlmApiKey 子树（D1）+ LlmNewapiUserMapping 子树（D5）
--
-- 安全性: 删除表之间无外键、无被任何保留表引用（已核 information_schema FK 图，0 条边）。
--          菜单子树先清 sys_role_menu 悬空引用再删，超管 is_superuser 绕过 RBAC 不受影响。
-- 幂等: 表 DROP IF EXISTS；菜单按 name 匹配，可在 local(15432) 与生产(5432) 重复执行。
-- 关联: app/llm 整目录删除 + D3 model/crud/service/api + db.py 第二引擎删除（同一提交）。
-- =====================================================

BEGIN;

-- 1) 网关遗留表（public schema；无外键依赖，CASCADE 兜底清理附属索引/序列）
DROP TABLE IF EXISTS llm_compress_usage_log CASCADE;
DROP TABLE IF EXISTS llm_media_task CASCADE;
DROP TABLE IF EXISTS llm_usage_log CASCADE;
DROP TABLE IF EXISTS llm_rate_limit_config CASCADE;
DROP TABLE IF EXISTS llm_model_alias CASCADE;
DROP TABLE IF EXISTS llm_model_group CASCADE;
DROP TABLE IF EXISTS llm_model_config CASCADE;
DROP TABLE IF EXISTS llm_model_provider CASCADE;

-- 2) 模型积分费率表（D3，hasn_billing schema）
DROP TABLE IF EXISTS hasn_billing.model_credit_rate CASCADE;

-- 3) 管理端菜单：先清 sys_role_menu 对目标菜单的引用，再删菜单子树
WITH targets AS (
    SELECT id FROM sys_menu WHERE name IN (
        -- 网关页面 + 按钮
        'LlmProvider', 'AddLlmProvider', 'EditLlmProvider', 'DeleteLlmProvider',
        'LlmModel', 'AddLlmModel', 'EditLlmModel', 'DeleteLlmModel',
        'LlmModelGroup', 'AddLlmModelGroup', 'EditLlmModelGroup', 'DeleteLlmModelGroup',
        'LlmRateLimit', 'AddLlmRateLimit', 'EditLlmRateLimit', 'DeleteLlmRateLimit',
        'LlmUsage', 'ViewLlmUsage',
        'LlmModelAlias', 'AddLlmModelAlias', 'EditLlmModelAlias', 'DeleteLlmModelAlias',
        -- D3 model_credit_rate
        'ModelCreditRate', 'AddModelCreditRate', 'EditModelCreditRate', 'DeleteModelCreditRate', 'ViewModelCreditRate'
    )
)
DELETE FROM sys_role_menu WHERE menu_id IN (SELECT id FROM targets);

-- 先删按钮（叶子），再删页面（父）；按 name 幂等
DELETE FROM sys_menu WHERE name IN (
    'AddLlmProvider', 'EditLlmProvider', 'DeleteLlmProvider',
    'AddLlmModel', 'EditLlmModel', 'DeleteLlmModel',
    'AddLlmModelGroup', 'EditLlmModelGroup', 'DeleteLlmModelGroup',
    'AddLlmRateLimit', 'EditLlmRateLimit', 'DeleteLlmRateLimit',
    'ViewLlmUsage',
    'AddLlmModelAlias', 'EditLlmModelAlias', 'DeleteLlmModelAlias',
    'AddModelCreditRate', 'EditModelCreditRate', 'DeleteModelCreditRate', 'ViewModelCreditRate'
);
DELETE FROM sys_menu WHERE name IN (
    'LlmProvider', 'LlmModel', 'LlmModelGroup', 'LlmRateLimit', 'LlmUsage', 'LlmModelAlias', 'ModelCreditRate'
);

-- 4) 兜底清理 sys_role_menu 悬空引用
DELETE FROM sys_role_menu rm WHERE NOT EXISTS (SELECT 1 FROM sys_menu m WHERE m.id = rm.menu_id);

COMMIT;
