-- studio（统一视频引擎，OpenMontage 引擎接入，模块 14 doc22）catalog 注册 + 品牌图标。
-- 诉求（doc22 §3/§3.6）：把 studio 注册为 cloud-brokered 视频工作台（cloud execution_mode），
--   hasn.studio.* 工具（render/export/share 出厂 ask）经 studio_service → montage_engine_provider → 独立部署的
--   montage-engine-service 跑渲染/出片（唯一接触 OpenMontage 处隔离独立服务）；install_policy=manual
--   （统一视频引擎是专业能力，工作台主动安装）。
--   本期 P2 只做云端数据层 + 目录/scope/manifest 骨架（4 表 hasn_studio.* + 本 catalog 行 + 5 scope）；
--   分身工具面随 P3 service + 云端 handler 落地，本期 manifest 不暴露 tools/capabilities。
--
-- catalog 是工作台展示 DB 权威（C2）；出厂源（app_catalog_service：_CATALOG_SORT_ORDER /
--   _CATALOG_AGENT_DEFAULTS['studio'] + app_catalog_registry 注册 build_studio_app）已同步同值，
--   新部署经 ensure_catalog_seeded 自动 INSERT 即得本值；本迁移为**存量 dev/prod** 显式建行（部署后立即可见，
--   不必等应用重启跑 seed），并回填 default_agent_type / icon_asset_uri。
-- 不设 config_json：studio 无 per-app 模型配置——引擎服务地址/令牌走云端 env（MONTAGE_ENGINE_URL/
--   MONTAGE_ENGINE_TOKEN/MONTAGE_ENGINE_TIMEOUT），不进 catalog config_json。
-- 幂等：
--   ① INSERT ... ON CONFLICT (app_id) DO NOTHING —— 已有 studio 行则跳过，绝不覆盖运营改动。
--   ② default_agent_type 仅在 IS NULL 时回填（不覆盖运营改动）。
--   ③ icon_asset_uri 出厂品牌资产无条件设定；若公共桶 studio.svg 未上传，webui 回落 icon token
--      'brand-studio'（AppBrandIcons 未知 token 再回落 lucide 单色），不影响功能。

-- ① 建 studio catalog 行（cloud / 视频工作台 /apps/studio / 个人模式 / 非默认挂载 / 免费 / content_operator 默认承接）。
INSERT INTO hasn_app_catalog (
    app_id, name, icon, icon_asset_uri, description,
    source, status, execution_mode, scope, collaboration_mode,
    entry_route, sort_order, default_mount, requires_role,
    access_type, min_tier, price_amount, price_unit, billing_cycle, trial_days, sku_ref,
    manifest_present, default_agent_type, work_session_system_prompt, config_json
) VALUES (
    'studio',
    '视频引擎',
    'brand-studio',
    'http://hasn-pub-cdn.dcfuture.cn/huanxing/app-icons/studio.svg',
    '统一视频引擎工作台——主人挑管线、派分身出片（脚本→分镜→配音→合成），成品库一键管理（cloud-brokered，算力按量计费）。',
    'builtin',
    'published',
    'cloud',
    '["personal"]'::jsonb,
    'none',
    '/apps/studio',
    76,
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
    'content_operator',
    '你是主人的视频内容运营官：用 hasn.studio.* 管线与工具把创意做成完整视频，按脚本→分镜→配音→合成的流水线推进、迭代精修；提交渲染/出片、导出成片、分享发布等花算力或外发的动作须经主人审批。所有成片来自引擎真实渲染、绝不伪造产物（零 fake），取不到/跑不通就如实报错，尊重主人最终决定权。',
    '{}'::jsonb
)
ON CONFLICT (app_id) DO NOTHING;

-- ② 回填 default_agent_type（存量 studio 行未绑定时，沿用 IS NULL 守卫不覆盖运营改动）。
UPDATE hasn_app_catalog
SET default_agent_type = 'content_operator',
    work_session_system_prompt = '你是主人的视频内容运营官：用 hasn.studio.* 管线与工具把创意做成完整视频，按脚本→分镜→配音→合成的流水线推进、迭代精修；提交渲染/出片、导出成片、分享发布等花算力或外发的动作须经主人审批。所有成片来自引擎真实渲染、绝不伪造产物（零 fake），取不到/跑不通就如实报错，尊重主人最终决定权。'
WHERE app_id = 'studio' AND default_agent_type IS NULL;

-- ③ 回填品牌图标（出厂品牌资产无条件设定；若公共桶 studio.svg 未传则 webui 回落 icon token）。
UPDATE hasn_app_catalog
SET icon = 'brand-studio',
    icon_asset_uri = 'http://hasn-pub-cdn.dcfuture.cn/huanxing/app-icons/studio.svg'
WHERE app_id = 'studio';
