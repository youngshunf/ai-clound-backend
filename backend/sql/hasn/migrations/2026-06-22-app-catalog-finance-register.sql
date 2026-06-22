-- finance（金融数据，akshare 行情与投研，模块 24）catalog 注册 + 品牌图标。
-- 诉求（模块 24 doc §4C.1）：把 finance 注册为纯云端只读数据应用（cloud execution_mode），
--   14 个 hasn.finance.* 工具经 finance_provider → 独立部署的 finance-data-service 取数（唯一接触
--   akshare 处隔离独立服务）；install_policy=manual（行情非人人需要，工作台主动安装）。
--
-- catalog 是工作台展示 DB 权威（C2）；出厂源（app_catalog_service：_CATALOG_SORT_ORDER /
--   _CATALOG_AGENT_DEFAULTS['finance'] + app_catalog_registry 注册 build_finance_app）已同步同值，
--   新部署经 ensure_catalog_seeded 自动 INSERT 即得本值；本迁移为**存量 dev/prod** 显式建行（部署后立即可见，
--   不必等应用重启跑 seed），并回填 default_agent_type / icon_asset_uri。
-- 不设 config_json：finance 无 per-app 模型配置——数据服务地址/令牌走云端 env（FINANCE_SERVICE_URL/
--   FINANCE_SERVICE_TOKEN），不进 catalog config_json。
-- 幂等：
--   ① INSERT ... ON CONFLICT (app_id) DO NOTHING —— 已有 finance 行则跳过，绝不覆盖运营改动。
--   ② default_agent_type 仅在 IS NULL 时回填（不覆盖运营改动）。
--   ③ icon_asset_uri 出厂品牌资产无条件设定；若公共桶 finance.svg 未上传，webui 回落 icon token
--      'brand-finance'（AppBrandIcons 未知 token 再回落 lucide 单色），不影响功能。

-- ① 建 finance catalog 行（cloud / 行情看板 /apps/finance / 非默认挂载 / 免费 / analyst 默认承接）。
INSERT INTO hasn_app_catalog (
    app_id, name, icon, icon_asset_uri, description,
    source, status, execution_mode, scope, collaboration_mode,
    entry_route, sort_order, default_mount, requires_role,
    access_type, min_tier, price_amount, price_unit, billing_cycle, trial_days, sku_ref,
    manifest_present, default_agent_type, work_session_system_prompt, config_json
) VALUES (
    'finance',
    '金融数据',
    'brand-finance',
    'http://hasn-pub-cdn.dcfuture.cn/huanxing/app-icons/finance.svg',
    '让分身/你随时查 A股·港美股·基金·期货·债券·指数行情与宏观数据——只读看人，数据仅供参考，不构成投资建议。',
    'builtin',
    'published',
    'cloud',
    '["personal", "enterprise"]'::jsonb,
    'workspace_shared',
    '/apps/finance',
    70,
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
    'analyst',
    '你是主人的投研分析师：用 hasn.finance.* 工具查 A股/港美股/基金/期货/债券/指数行情与宏观数据，为主人做有数据支撑的研判；所有数据仅供参考、不构成投资建议，引用须标注口径与日期，取不到就如实说，零 fake、失败如实报错。',
    '{}'::jsonb
)
ON CONFLICT (app_id) DO NOTHING;

-- ② 回填 default_agent_type（存量 finance 行未绑定时，沿用 IS NULL 守卫不覆盖运营改动）。
UPDATE hasn_app_catalog
SET default_agent_type = 'analyst',
    work_session_system_prompt = '你是主人的投研分析师：用 hasn.finance.* 工具查 A股/港美股/基金/期货/债券/指数行情与宏观数据，为主人做有数据支撑的研判；所有数据仅供参考、不构成投资建议，引用须标注口径与日期，取不到就如实说，零 fake、失败如实报错。'
WHERE app_id = 'finance' AND default_agent_type IS NULL;

-- ③ 回填品牌图标（出厂品牌资产无条件设定；若公共桶 finance.svg 未传则 webui 回落 icon token）。
UPDATE hasn_app_catalog
SET icon = 'brand-finance',
    icon_asset_uri = 'http://hasn-pub-cdn.dcfuture.cn/huanxing/app-icons/finance.svg'
WHERE app_id = 'finance';
