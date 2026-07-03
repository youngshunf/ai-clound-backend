-- imagelab（图坊，自研本地图像处理引擎，模块 14 doc30）catalog 注册 + 平台级配置骨架 + 品牌图标。
-- 诉求（福仔 2026-07-02，doc30 §5.5 + §5.9 §B）：把图坊注册为第三个 downloadable_local 应用
--   （本期与 film/reel 同以 local_tool + install_policy=manual 注册），并落 app_configs.imagelab 平台默认配置
--   + 品牌图标，供 daemon 镜像 catalog、注入 sidecar、webui 展示。
--
-- catalog 是工作台展示 DB 权威（C2）；出厂源（app_catalog_service：_CATALOG_SORT_ORDER /
--   _CATALOG_AGENT_DEFAULTS / _CATALOG_DEFAULT_CONFIG['imagelab'] + app_catalog_registry 注册）已同步同值，
--   新部署经 ensure_catalog_seeded 自动 INSERT 即得本值；本迁移为**存量 dev/prod** 显式建行（部署后立即可见，
--   不必等应用重启跑 seed），并回填 config_json / icon_asset_uri / default_agent_type。
-- 幂等：
--   ① INSERT ... ON CONFLICT (app_id) DO NOTHING —— 已有 imagelab 行（seed 已插）则跳过，绝不覆盖运营改动。
--   ② config_json 仅在为空（NULL/{}/缺 engine 键）时回填，**绝不覆盖**运营/主人已填的引擎/模型配置。
--   ③ icon_asset_uri 出厂品牌资产无条件设定（同 2026-06-22-app-catalog-icon-asset-uri.sql 范式）；
--      若该公共桶资产尚未上传（TODO：backend/scripts/upload_app_icons.py 传 imagelab.svg），webui 回落 icon token
--      'brand-imagelab'（AppBrandIcons 未知 token 再回落 lucide 单色），不影响功能。
--
-- ⚠️ 无专有「修图师」分身——任意分身皆可操作，hasn.imagelab.* 工具面与技能所有分身共享（福仔 2026-07-02 纠正）；
--   默认承接内容创作分身 content_operator（用户派发可改选任意分身）。

-- ① 建 imagelab catalog 行（local_tool / 内联路由 /apps/imagelab / 非默认挂载 / 免费 / content_operator 默认承接）。
INSERT INTO hasn_app_catalog (
    app_id, name, icon, icon_asset_uri, description,
    source, status, execution_mode, scope, collaboration_mode,
    entry_route, sort_order, default_mount, requires_role,
    access_type, min_tier, price_amount, price_unit, billing_cycle, trial_days, sku_ref,
    manifest_present, default_agent_type, work_session_system_prompt, config_json
) VALUES (
    'imagelab',
    '图坊',
    'brand-imagelab',
    'http://hasn-pub-cdn.dcfuture.cn/huanxing/app-icons/imagelab.svg',
    '自研本地图像处理引擎——去背景/裁剪/调色/滤镜/水印/拼图/压缩/动画/超分/局部消除，分身用「处理配方」编排批量处理，产物默认本地、点分享才上云。',
    'builtin',
    'published',
    'local_tool',
    '["personal"]'::jsonb,
    'none',
    '/apps/imagelab',
    58,
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
    '你是图坊（图像处理应用）的执行分身：把主人的图片处理需求做成对客可用的成品。先用 hasn.imagelab.analyze 读现状（尺寸/格式/透明度/主体）再动手，别盲目开工；复杂或批量需求用 hasn.imagelab.pipeline / batch 组「处理配方」一次编排（去背景→裁剪→水印→压缩→转格式…），不要一步步单发；非破坏性处理（裁剪/缩放/调色/滤镜/格式/压缩/去背景/拼图/动画）可自由做（默认不覆盖原图、产物只落本地、可回滚），破坏性操作（inpaint 去物体/去水印，hasn.imagelab.retouch）和生成式操作（hasn.imagelab.generate 花积分）先与主人确认；批量前先明确输入范围与预期产出数、大批量提交后经 hasn.imagelab.job.get 轮询进度；完成用 hasn.imagelab.export 把产物写到本地输出目录并登记，回禀主人，需要分享才用 hasn.imagelab.share 上云发好友/群。文案/配色/水印文字等创意与审美判断摊给主人定、不擅自拍板。真实引擎本地处理、产物本地优先不自动上云，零 fake，失败如实报错。',
    '{
  "engine": {
    "version": "",
    "packages": {},
    "bundled_deps": ["ffmpeg", "libwebp"],
    "ml_models": ["birefnet-general", "lama", "realesrgan", "paddleocr"]
  }
}'::jsonb
)
ON CONFLICT (app_id) DO NOTHING;

-- ② 回填 config_json（存量 imagelab 行 config_json 为空时——含早于本 seed 被 INSERT 的行；绝不覆盖已填值）。
UPDATE hasn_app_catalog
SET config_json = '{
  "engine": {
    "version": "",
    "packages": {},
    "bundled_deps": ["ffmpeg", "libwebp"],
    "ml_models": ["birefnet-general", "lama", "realesrgan", "paddleocr"]
  }
}'::jsonb
WHERE app_id = 'imagelab'
  AND (config_json IS NULL OR config_json = '{}'::jsonb OR NOT (config_json ? 'engine'));

-- ③ 回填 default_agent_type（存量 imagelab 行未绑定时，沿用 IS NULL 守卫不覆盖运营改动）。
UPDATE hasn_app_catalog
SET default_agent_type = 'content_operator',
    work_session_system_prompt = '你是图坊（图像处理应用）的执行分身：把主人的图片处理需求做成对客可用的成品。先用 hasn.imagelab.analyze 读现状（尺寸/格式/透明度/主体）再动手，别盲目开工；复杂或批量需求用 hasn.imagelab.pipeline / batch 组「处理配方」一次编排（去背景→裁剪→水印→压缩→转格式…），不要一步步单发；非破坏性处理（裁剪/缩放/调色/滤镜/格式/压缩/去背景/拼图/动画）可自由做（默认不覆盖原图、产物只落本地、可回滚），破坏性操作（inpaint 去物体/去水印，hasn.imagelab.retouch）和生成式操作（hasn.imagelab.generate 花积分）先与主人确认；批量前先明确输入范围与预期产出数、大批量提交后经 hasn.imagelab.job.get 轮询进度；完成用 hasn.imagelab.export 把产物写到本地输出目录并登记，回禀主人，需要分享才用 hasn.imagelab.share 上云发好友/群。文案/配色/水印文字等创意与审美判断摊给主人定、不擅自拍板。真实引擎本地处理、产物本地优先不自动上云，零 fake，失败如实报错。'
WHERE app_id = 'imagelab' AND default_agent_type IS NULL;

-- ④ 回填品牌图标（出厂品牌资产无条件设定；若公共桶 imagelab.svg 未传则 webui 回落 icon token）。
UPDATE hasn_app_catalog
SET icon = 'brand-imagelab',
    icon_asset_uri = 'http://hasn-pub-cdn.dcfuture.cn/huanxing/app-icons/imagelab.svg'
WHERE app_id = 'imagelab';
