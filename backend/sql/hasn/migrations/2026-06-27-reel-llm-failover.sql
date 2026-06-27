-- reel 文案模型加 failover 兜底：agnes-2.0-flash 单渠道偶发 503/超时，单元素列表无兜底 → 一抽风就硬失败。
-- 背景（福仔 2026-06-27）：reel 出片报 `reel llm model agnes-2.0-flash attempt 1/5 failed (retryable): HTTP 503`。
--   已用真实 owner token 复现 reel 确切调用（公网网关 https://llm.dcfuture.cn/v1 + agnes-2.0-flash）→ **HTTP 200**，
--   证明 LLM 配置正确落到了 sidecar、网关/模型/凭据全对。503 真因：agnes-2.0-flash 的唯一渠道（new-api 渠道 19
--   agnes-ai，vllm 自建上游 ~10s/次）偶发抽风/超时，而 reel 的 models.llm 只有 ["agnes-2.0-flash"] 一个、无兜底，
--   sidecar 5 次重试全打同一个挂掉的上游 → `failover exhausted` 硬失败。
--   修法：models.llm 改成 failover 列表，agnes 为主 + deepseek-v4-pro / qwen3.7-plus 兜底（均为 new-api 已开通的
--   真实文案模型，DeepSeek ~1s、qwen 稳定）。agnes 抽风时 sidecar 自动切到健康兜底，不再硬失败。
--
-- 幂等 + 不覆盖运营改动（关键）：仅当 models.llm **恰为单元素 ["agnes-2.0-flash"]**（= 尚无兜底）时才替换；
--   运营已在管理端「编辑配置」改成别的列表则 WHERE 不命中、绝不覆盖。重复执行第二次因值已是三元素列表而自然 no-op。
-- 只动 models.llm 一条路径（jsonb_set），tts/stt/material/video/engine 等其余配置原样保留。
-- platform_config revision 内容寻址：config_json 一变，云端下次 node-scope 拉取自动算出新 revision → daemon 重拉重注入，
--   无需显式 sync_bump。

UPDATE hasn_app_catalog
SET config_json = jsonb_set(
        config_json,
        '{models,llm}',
        '["agnes-2.0-flash", "deepseek-v4-pro", "qwen3.7-plus"]'::jsonb,
        true
    )
WHERE app_id = 'reel'
  AND config_json #> '{models,llm}' = '["agnes-2.0-flash"]'::jsonb;
