-- design（矢量设计，源自 OpenPencil，模块 14 doc27）catalog 注册 + 平台级配置骨架 + 品牌图标。
-- 诉求（OP-P3-6，doc27 §5.4 + 实施 27 P3-B）：把 design 注册为 local_tool 本地 sidecar 应用
--   （install_policy=manual），并落 app_configs.design 平台默认配置（engine 分发骨架）+ 品牌图标，
--   供 daemon 镜像 catalog、注入 sidecar、webui 展示。
--
-- catalog 是工作台展示 DB 权威（C2）；出厂源（app_catalog_service：_CATALOG_SORT_ORDER /
--   _CATALOG_AGENT_DEFAULTS / _CATALOG_DEFAULT_CONFIG['design'] + app_catalog_registry 注册）已同步同值，
--   新部署经 ensure_catalog_seeded 自动 INSERT 即得本值；本迁移为**存量 dev/prod** 显式建行（部署后立即可见，
--   不必等应用重启跑 seed），并回填 config_json / icon_asset_uri / default_agent_type / work_session_system_prompt。
--
-- scope 铸造（design:read/:write/:codegen）走代码侧 DEFAULT_AGENT_SCOPES（agent_jwt.py，JWT scopes claim 唯一固定来源，
--   同 reel/film）+ scopes.py 展示元数据，**非 SQL 表**——本迁移不含 scope 行（对齐 reel register 迁移）。
--
-- TODO(OP-P3-8 entitlement，诚实延后)：本期 design 以 access_type='free' + install_policy='manual' 注册
--   （catalog 三闸门里「开通=手动免费、定价=无、权益=全开」的最简配置，与 reel/studio 同期一致）。更细的
--   权益分层（min_tier / 计量 / 企业开通自播种按角色裁剪，对齐 doc16 商业化 + GE 双模）随商业化排期落地，
--   非本期 OP-P3-B/C 范围；落地时新增 HasnAppEntitlement 行 + tier gating，不改本 catalog 行结构。
--
-- 幂等：
--   ① INSERT ... ON CONFLICT (app_id) DO NOTHING —— 已有 design 行（seed 已插）则跳过，绝不覆盖运营改动。
--   ② config_json 仅在为空（NULL/{}/缺 engine 键）时回填，**绝不覆盖**运营已填的引擎包 manifest。
--   ③ icon_asset_uri 出厂品牌资产无条件设定；若该公共桶资产尚未上传（TODO：scripts/upload_app_icons.py 传 design.svg），
--      webui 回落 icon token 'brand-design'（AppBrandIcons 未知 token 再回落 lucide 单色），不影响功能。

-- ① 建 design catalog 行（local_tool / 项目管理+派发台 /apps/design / 非默认挂载 / 免费 / designer 默认承接）。
INSERT INTO hasn_app_catalog (
    app_id, name, icon, icon_asset_uri, description,
    source, status, execution_mode, scope, collaboration_mode,
    entry_route, sort_order, default_mount, requires_role,
    access_type, min_tier, price_amount, price_unit, billing_cycle, trial_days, sku_ref,
    manifest_present, default_agent_type, work_session_system_prompt, config_json
) VALUES (
    'design',
    '矢量设计',
    'brand-design',
    'http://hasn-pub-cdn.dcfuture.cn/huanxing/app-icons/design.svg',
    'AI-native 矢量设计画布（Figma/Pencil 替代，源自 OpenPencil）——分身经 hasn.design.* 在主人画布上实时出设计（海报/UI 稿/插画/图形/Logo），本地 sidecar 出图、产物回流，源文件本地优先。',
    'builtin',
    'published',
    'local_tool',
    '["personal"]'::jsonb,
    'none',
    '/apps/design',
    78,
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
    'designer',
    '你是矢量设计应用的执行分身：在主人的设计画布上把需求做成专业的矢量设计（海报/UI 稿/插画/图形/Logo 排版）。先打开项目画布、用 get/get_selection 读现状；动手前先用 get_design_prompt 取设计知识自我增益。按需求选路径——简单图用 batch_design 一次成型，复杂稿走分层 skeleton→content→refine 逐步推进；用 insert/update/move/copy 与 set_variables/set_themes 精修。破坏性操作（delete/replace）先与主人确认。完成后用 export 导出并登记产物（hasn://asset）。只调用 hasn.design.* 工具，文案/品牌/方案定稿摊给主人确认，零 fake，失败如实报错。',
    '{
  "engine": {"version": "", "packages": {}, "bundled_deps": ["node"]}
}'::jsonb
)
ON CONFLICT (app_id) DO NOTHING;

-- ② 回填 config_json（存量 design 行 config_json 为空时；绝不覆盖运营已填的引擎包 manifest）。
UPDATE hasn_app_catalog
SET config_json = '{
  "engine": {"version": "", "packages": {}, "bundled_deps": ["node"]}
}'::jsonb
WHERE app_id = 'design'
  AND (config_json IS NULL OR config_json = '{}'::jsonb OR NOT (config_json ? 'engine'));

-- ③ 回填 default_agent_type + 业务提示词（存量 design 行未绑定时，沿用 IS NULL 守卫不覆盖运营改动）。
UPDATE hasn_app_catalog
SET default_agent_type = 'designer',
    work_session_system_prompt = '你是矢量设计应用的执行分身：在主人的设计画布上把需求做成专业的矢量设计（海报/UI 稿/插画/图形/Logo 排版）。先打开项目画布、用 get/get_selection 读现状；动手前先用 get_design_prompt 取设计知识自我增益。按需求选路径——简单图用 batch_design 一次成型，复杂稿走分层 skeleton→content→refine 逐步推进；用 insert/update/move/copy 与 set_variables/set_themes 精修。破坏性操作（delete/replace）先与主人确认。完成后用 export 导出并登记产物（hasn://asset）。只调用 hasn.design.* 工具，文案/品牌/方案定稿摊给主人确认，零 fake，失败如实报错。'
WHERE app_id = 'design' AND default_agent_type IS NULL;

-- ④ 回填品牌图标（出厂品牌资产无条件设定；若公共桶 design.svg 未传则 webui 回落 icon token）。
UPDATE hasn_app_catalog
SET icon = 'brand-design',
    icon_asset_uri = 'http://hasn-pub-cdn.dcfuture.cn/huanxing/app-icons/design.svg'
WHERE app_id = 'design';
