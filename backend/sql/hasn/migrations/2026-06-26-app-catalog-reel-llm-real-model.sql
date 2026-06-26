-- reel 文案模型：把占位模板 gpt-5 换成 new-api 已开通的真实模型 agnes-2.0-flash。
-- 背景（福仔 2026-06-26）：reel 出片报 `reel llm model gpt-5 attempt 5/5 failed (retryable): HTTP 503`
--   → `reel llm failover exhausted`，根因是出厂 _CATALOG_DEFAULT_CONFIG['reel'].models.llm 的示例名
--   gpt-5 只是占位模板、new-api 网关并未开通该模型，sidecar 经网关请求恒 503。
--   出厂源已改默认 agnes-2.0-flash（app_catalog_service._CATALOG_DEFAULT_CONFIG['reel']），但 seed 是
--   INSERT-only，**存量 dev/prod 的 reel 行仍是 gpt-5**，故本迁移把存量占位值就地纠正。
--
-- 幂等 + 不覆盖运营改动（关键）：仅当 models.llm **恰为占位 ["gpt-5"]** 时才替换；
--   运营已在管理端「编辑配置」改成别的模型（含已改成 agnes-2.0-flash）则 WHERE 不命中、绝不覆盖。
--   重复执行第二次因值已非 ["gpt-5"] 而自然 no-op。
-- 只动 models.llm 一条路径（jsonb_set），tts/stt/material/video/engine 等其余配置原样保留。

UPDATE hasn_app_catalog
SET config_json = jsonb_set(config_json, '{models,llm}', '["agnes-2.0-flash"]'::jsonb, true)
WHERE app_id = 'reel'
  AND config_json #> '{models,llm}' = '["gpt-5"]'::jsonb;
