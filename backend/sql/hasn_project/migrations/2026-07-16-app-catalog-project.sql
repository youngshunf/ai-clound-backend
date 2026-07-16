-- 项目管理（project，平台项目·联邦挂靠一级应用，模块 14 doc38）catalog 注册。
-- 诉求（doc38 §PJ-P1 / 实施清单 U1）：把「项目管理」注册为第三条轴「为了哪件事」的聚合门面应用，
--   cloud 执行、默认挂载（install_policy=auto → default_mount=true）、free 开箱、通用助理承接派发。
--   catalog 是工作台展示 DB 权威（C2）；出厂源已同步同值（app_catalog_service：_CATALOG_SORT_ORDER['project']=5
--   / _CATALOG_AGENT_DEFAULTS['project'] + app_catalog_registry 注册 build_project_app），新部署经
--   ensure_catalog_seeded 自动 INSERT 即得本值；本迁移为**存量 dev/prod** 显式建行（部署后立即可见）。
-- 幂等：INSERT ... ON CONFLICT (app_id) DO NOTHING —— 已有 project 行（seed 已插）则跳过，绝不覆盖运营改动。
--
-- ⚠️ 无专有分身——建项目/在项目内派发承接「全能助理」内置分身（default_agent_type=assistant，
--   用户派发可改选任意分身）。图标 brand-project（webui AppBrandIcons 未知 token 回落 lucide 单色）；
--   若后续上传公共桶 project.svg，再补 icon_asset_uri（同 imagelab 范式）。

INSERT INTO hasn_app_catalog (
    app_id, name, icon, description,
    source, status, execution_mode, scope, collaboration_mode,
    entry_route, sort_order, default_mount, requires_role,
    access_type, manifest_present, default_agent_type, work_session_system_prompt
) VALUES (
    'project',
    '项目管理',
    'brand-project',
    '把散在各应用的活儿收进一个个「项目」——为了哪件事，就把知识库/获客/图坊/站点/产物/分身工作都挂到它下面，一页看全进展。',
    'builtin',
    'published',
    'cloud',
    '["personal", "enterprise"]'::jsonb,
    'none',
    '/apps/project',
    5,
    true,
    NULL,
    'free',
    true,
    'assistant',
    '你是项目管理应用的执行分身：帮主人把「为了哪件事」的活儿收进项目——建项目（一句话目标 → hasn.project.create）、把已有资源挂靠进来（hasn.project.link 知识库/获客项目/图坊项目/站点/deck…）、按里程碑推进（hasn.project.milestone.*）、在项目内派发分身干活。项目只回答「为了哪件事」——不替代各应用（知识库/获客/图坊仍在各自应用里操作），只做聚合与推进。只调用 hasn.project.* 工具，挂靠/摘出统一经工具（不擅自跨应用改数据），零 fake，失败如实报错。'
)
ON CONFLICT (app_id) DO NOTHING;

-- 回填 default_agent_type + work_session_system_prompt（存量 project 行未绑定时；沿用 IS NULL 守卫不覆盖运营改动）。
-- 覆盖两种存量：① 早于 _CATALOG_AGENT_DEFAULTS['project'] 落地被 ensure_catalog_seeded 提前 INSERT 的行；
--   ② 手动/旧脚本建的裸行。绑「全能助理（assistant）」+ 项目管理业务提示词。
UPDATE hasn_app_catalog
SET default_agent_type = 'assistant',
    work_session_system_prompt = '你是项目管理应用的执行分身：帮主人把「为了哪件事」的活儿收进项目——建项目（一句话目标 → hasn.project.create）、把已有资源挂靠进来（hasn.project.link 知识库/获客项目/图坊项目/站点/deck…）、按里程碑推进（hasn.project.milestone.*）、在项目内派发分身干活。项目只回答「为了哪件事」——不替代各应用（知识库/获客/图坊仍在各自应用里操作），只做聚合与推进。只调用 hasn.project.* 工具，挂靠/摘出统一经工具（不擅自跨应用改数据），零 fake，失败如实报错。'
WHERE app_id = 'project' AND default_agent_type IS NULL;
