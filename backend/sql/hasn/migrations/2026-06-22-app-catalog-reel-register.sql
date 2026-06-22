-- reel（短视频合成，源自 MoneyPrinterTurbo，模块 14 doc19）catalog 注册 + 平台级配置骨架 + 品牌图标。
-- 诉求（福仔 2026-06-22，doc19 §3/§6.4 + 实施 11 P2/P4）：把 reel 注册为第二个 downloadable_local 应用
--   （本期与 film 同以 local_tool + install_policy=manual 注册），并落 app_configs.reel 平台默认配置 +
--   品牌图标，供 daemon 镜像 catalog、注入 sidecar、webui 展示。
--
-- catalog 是工作台展示 DB 权威（C2）；出厂源（app_catalog_service：_CATALOG_SORT_ORDER /
--   _CATALOG_AGENT_DEFAULTS / _CATALOG_DEFAULT_CONFIG['reel'] + workbench_app_registry 注册）已同步同值，
--   新部署经 ensure_catalog_seeded 自动 INSERT 即得本值；本迁移为**存量 dev/prod** 显式建行（部署后立即可见，
--   不必等应用重启跑 seed），并回填 config_json / icon_asset_uri / default_agent_type。
-- 幂等：
--   ① INSERT ... ON CONFLICT (app_id) DO NOTHING —— 已有 reel 行（seed 已插）则跳过，绝不覆盖运营改动。
--   ② config_json 仅在为空（NULL/{}/缺 models 键）时回填，**绝不覆盖**运营/主人已填的模型/素材 key。
--   ③ icon_asset_uri 出厂品牌资产无条件设定（同 2026-06-22-app-catalog-icon-asset-uri.sql 范式）；
--      若该公共桶资产尚未上传（TODO：backend/scripts/upload_app_icons.py 传 reel.svg），webui 回落 icon token
--      'brand-reel'（AppBrandIcons 未知 token 再回落 lucide 单色），不影响功能。

-- ① 建 reel catalog 行（local_tool / 瘦引擎页 /apps/reel / 非默认挂载 / 免费 / content_operator 默认承接）。
INSERT INTO hasn_app_catalog (
    app_id, name, icon, icon_asset_uri, description,
    source, status, execution_mode, scope, collaboration_mode,
    entry_route, sort_order, default_mount, requires_role,
    access_type, min_tier, price_amount, price_unit, billing_cycle, trial_days, sku_ref,
    manifest_present, default_agent_type, work_session_system_prompt, config_json
) VALUES (
    'reel',
    '短视频合成',
    'brand-reel',
    'http://hasn-pub-cdn.dcfuture.cn/huanxing/app-icons/reel.svg',
    '合成式短视频引擎——文案→配音→字幕→库存素材自动拼接出片（口播/带货/资讯量产），分身在创作运营内调用出片，成片本地优先。',
    'builtin',
    'published',
    'local_tool',
    '["personal"]'::jsonb,
    'none',
    '/apps/reel',
    57,
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
    '你是短视频合成应用的执行分身：在创作运营的内容流水线里，把脚本/主题（取自内容项）配上素材（取自创作运营素材库，自带优先、不足才补库存）、配音与字幕，用 hasn.reel.* 工具本地合成出口播/带货/资讯类短视频；成片本地优先、重资产不自动上云，归属与发布走创作运营。只调用 hasn.reel.* 工具，文案与成片在确认点摊给主人，零 fake，失败如实报错。',
    '{
  "models": {"llm": ["gpt-5"], "tts": ["edge"], "stt": []},
  "tts": {"tts_provider": "edge", "voice_name": "zh-CN-XiaoxiaoNeural", "voice_rate": 1.0},
  "subtitle": {"subtitle_provider": "edge", "whisper_model_size": "large-v3"},
  "material": {"video_source": "pexels", "platform_keys": {"pexels": [], "pixabay": []}},
  "video": {
    "video_aspect": "9:16",
    "video_clip_duration": 5,
    "subtitle_style": {"font_name": "", "font_size": 60, "stroke_color": "#000000", "text_color": "#FFFFFF", "position": "bottom"},
    "bgm": {"bgm_type": "random", "bgm_volume": 0.2},
    "video_count": 1
  },
  "engine": {"version": "", "packages": {}, "bundled_deps": ["ffmpeg", "imagemagick"]}
}'::jsonb
)
ON CONFLICT (app_id) DO NOTHING;

-- ② 回填 config_json（存量 reel 行 config_json 为空时——含早于本 seed 被 INSERT 的行；绝不覆盖已填值）。
UPDATE hasn_app_catalog
SET config_json = '{
  "models": {"llm": ["gpt-5"], "tts": ["edge"], "stt": []},
  "tts": {"tts_provider": "edge", "voice_name": "zh-CN-XiaoxiaoNeural", "voice_rate": 1.0},
  "subtitle": {"subtitle_provider": "edge", "whisper_model_size": "large-v3"},
  "material": {"video_source": "pexels", "platform_keys": {"pexels": [], "pixabay": []}},
  "video": {
    "video_aspect": "9:16",
    "video_clip_duration": 5,
    "subtitle_style": {"font_name": "", "font_size": 60, "stroke_color": "#000000", "text_color": "#FFFFFF", "position": "bottom"},
    "bgm": {"bgm_type": "random", "bgm_volume": 0.2},
    "video_count": 1
  },
  "engine": {"version": "", "packages": {}, "bundled_deps": ["ffmpeg", "imagemagick"]}
}'::jsonb
WHERE app_id = 'reel'
  AND (config_json IS NULL OR config_json = '{}'::jsonb OR NOT (config_json ? 'models'));

-- ③ 回填 default_agent_type（存量 reel 行未绑定时，沿用 IS NULL 守卫不覆盖运营改动）。
UPDATE hasn_app_catalog
SET default_agent_type = 'content_operator',
    work_session_system_prompt = '你是短视频合成应用的执行分身：在创作运营的内容流水线里，把脚本/主题（取自内容项）配上素材（取自创作运营素材库，自带优先、不足才补库存）、配音与字幕，用 hasn.reel.* 工具本地合成出口播/带货/资讯类短视频；成片本地优先、重资产不自动上云，归属与发布走创作运营。只调用 hasn.reel.* 工具，文案与成片在确认点摊给主人，零 fake，失败如实报错。'
WHERE app_id = 'reel' AND default_agent_type IS NULL;

-- ④ 回填品牌图标（出厂品牌资产无条件设定；若公共桶 reel.svg 未传则 webui 回落 icon token）。
UPDATE hasn_app_catalog
SET icon = 'brand-reel',
    icon_asset_uri = 'http://hasn-pub-cdn.dcfuture.cn/huanxing/app-icons/reel.svg'
WHERE app_id = 'reel';
