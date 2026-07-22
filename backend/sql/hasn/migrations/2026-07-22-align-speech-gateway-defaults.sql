-- 将旧版 PDC 语音网关出厂模型升级为当前 New API 模型链。
-- 只匹配完整旧默认列表，运营自定义配置不迁移。
-- 生效 revision 由 PlatformDefaultConfigService 按完整下发配置实时计算；
-- 行内 revision 清空，等待下一次 Admin 覆盖保存时按当前算法重建。
WITH normalized AS (
  SELECT
    id,
    CASE
      WHEN config_json #> '{node,media,stt_models}' = '["whisper-1"]'::jsonb
      THEN jsonb_set(
        CASE
          WHEN config_json #> '{node,media,tts_models}' = '["tts-1", "tts-1-hd"]'::jsonb
          THEN jsonb_set(
            config_json,
            '{node,media,tts_models}',
            '["qwen3-tts-flash", "qwen3-tts-instruct-flash"]'::jsonb
          )
          ELSE config_json
        END,
        '{node,media,stt_models}',
        '["qwen3-asr-flash"]'::jsonb
      )
      WHEN config_json #> '{node,media,tts_models}' = '["tts-1", "tts-1-hd"]'::jsonb
      THEN jsonb_set(
        config_json,
        '{node,media,tts_models}',
        '["qwen3-tts-flash", "qwen3-tts-instruct-flash"]'::jsonb
      )
      ELSE config_json
    END AS config_json
  FROM public.hasn_platform_default_config
  WHERE config_key = 'global'
)
UPDATE public.hasn_platform_default_config AS target
SET
  config_json = normalized.config_json,
  revision = '',
  updated_time = now()
FROM normalized
WHERE target.id = normalized.id
  AND target.config_json IS DISTINCT FROM normalized.config_json;
