-- FILMCFG（实施 AC-P6 续）：film（视频生成）catalog 行回填「平台级配置默认骨架」。
-- 诉求（福仔 2026-06-20）：管理端「编辑配置 - 视频生成」弹框应开箱给出**完整结构**（5 类模型 +
--   引擎 manifest），运营/主人**只改模型名**即可，不必从 {} 空对象手写整份 JSON。
-- 骨架与 app_catalog_service._CATALOG_DEFAULT_CONFIG['film'] 同源（示例名 gpt-5 / gpt-image-2 /
--   kling-1 是占位模板，务必换成各自 new-api 已开通的真实模型；视频需先开通视频渠道）。
-- 列已由 2026-06-20-app-catalog-config-json.sql 建好；INSERT-only seed 跳过已存在行，故需此 UPDATE
--   回填存量行（含早于 FILMCFG seed 建成 {} 的 film 行）。
-- 幂等：仅在 config_json 为空（NULL / {} / 缺 models 键）时回填，**绝不覆盖**运营/主人已填的模型名。

UPDATE hasn_app_catalog
SET config_json = '{
  "models": {
    "llm": ["gpt-5"],
    "vlm": ["gpt-5"],
    "image_t2i": ["gpt-image-2"],
    "image_it2i": ["gpt-image-2"],
    "video": ["kling-1"]
  },
  "engine": {"version": "", "packages": {}}
}'::jsonb
WHERE app_id = 'film'
  AND (config_json IS NULL OR config_json = '{}'::jsonb OR NOT (config_json ? 'models'));
