-- quant（量化交易，NautilusTrader 引擎接入，模块 14 doc23）catalog 注册 + 品牌图标。
-- 诉求（doc23 §3/§7）：把 quant 注册为 cloud-brokered 量化工作台（cloud execution_mode），
--   5 个回测线 hasn.quant.* 工具经 quant_service → quant_engine_provider → 独立部署的 quant-engine-service
--   跑真回测（唯一接触 NautilusTrader 处隔离独立服务）；install_policy=manual（量化是专业能力，工作台主动安装）。
--   本期 P0–P5 只做回测研究（零资金风险）；实盘线（deploy_live/submit_order/resume，scope quant:trade/quant:deploy）
--   受 P0-闸1 产品/法务硬闸 + 真钱 gated，本期不接、不在 manifest 暴露。
--
-- catalog 是工作台展示 DB 权威（C2）；出厂源（app_catalog_service：_CATALOG_SORT_ORDER /
--   _CATALOG_AGENT_DEFAULTS['quant'] + app_catalog_registry 注册 build_quant_app）已同步同值，
--   新部署经 ensure_catalog_seeded 自动 INSERT 即得本值；本迁移为**存量 dev/prod** 显式建行（部署后立即可见，
--   不必等应用重启跑 seed），并回填 default_agent_type / icon_asset_uri。
-- 不设 config_json：quant 无 per-app 模型配置——引擎服务地址/令牌走云端 env（QUANT_ENGINE_URL/
--   QUANT_ENGINE_TOKEN/QUANT_ENGINE_TIMEOUT），不进 catalog config_json。
-- 幂等：
--   ① INSERT ... ON CONFLICT (app_id) DO NOTHING —— 已有 quant 行则跳过，绝不覆盖运营改动。
--   ② default_agent_type 仅在 IS NULL 时回填（不覆盖运营改动）。
--   ③ icon_asset_uri 出厂品牌资产无条件设定；若公共桶 quant.svg 未上传，webui 回落 icon token
--      'brand-quant'（AppBrandIcons 未知 token 再回落 lucide 单色），不影响功能。

-- ① 建 quant catalog 行（cloud / 量化工作台 /apps/quant / 个人模式 / 非默认挂载 / 免费 / quant_trader 默认承接）。
INSERT INTO hasn_app_catalog (
    app_id, name, icon, icon_asset_uri, description,
    source, status, execution_mode, scope, collaboration_mode,
    entry_route, sort_order, default_mount, requires_role,
    access_type, min_tier, price_amount, price_unit, billing_cycle, trial_days, sku_ref,
    manifest_present, default_agent_type, work_session_system_prompt, config_json
) VALUES (
    'quant',
    '量化交易',
    'brand-quant',
    'http://hasn-pub-cdn.dcfuture.cn/huanxing/app-icons/quant.svg',
    'AI 量化研究工作台——分身写策略、云端跑回测、出绩效报告、迭代优化（回测零资金风险；实盘真钱强闸）。',
    'builtin',
    'published',
    'cloud',
    '["personal"]'::jsonb,
    'none',
    '/apps/quant',
    75,
    false,
    NULL,
    'free',
    NULL,
    NULL,
    'cny',
    'once',
    0,
    NULL,
    true,
    'quant_trader',
    '你是主人的量化交易官：用 hasn.quant.* 工具写量化策略、提交历史回测、读绩效报告并迭代优化；回测只花算力、不动钱，可大胆假设小心求证。所有绩效来自引擎真实回测、绝不臆造数字（零 fake）；回测表现不代表实盘收益，不构成投资建议；实盘部署/下单等动真钱动作须经主人审批，取不到/跑不通就如实报错，尊重主人最终决定权。',
    '{}'::jsonb
)
ON CONFLICT (app_id) DO NOTHING;

-- ② 回填 default_agent_type（存量 quant 行未绑定时，沿用 IS NULL 守卫不覆盖运营改动）。
UPDATE hasn_app_catalog
SET default_agent_type = 'quant_trader',
    work_session_system_prompt = '你是主人的量化交易官：用 hasn.quant.* 工具写量化策略、提交历史回测、读绩效报告并迭代优化；回测只花算力、不动钱，可大胆假设小心求证。所有绩效来自引擎真实回测、绝不臆造数字（零 fake）；回测表现不代表实盘收益，不构成投资建议；实盘部署/下单等动真钱动作须经主人审批，取不到/跑不通就如实报错，尊重主人最终决定权。'
WHERE app_id = 'quant' AND default_agent_type IS NULL;

-- ③ 回填品牌图标（出厂品牌资产无条件设定；若公共桶 quant.svg 未传则 webui 回落 icon token）。
UPDATE hasn_app_catalog
SET icon = 'brand-quant',
    icon_asset_uri = 'http://hasn-pub-cdn.dcfuture.cn/huanxing/app-icons/quant.svg'
WHERE app_id = 'quant';
