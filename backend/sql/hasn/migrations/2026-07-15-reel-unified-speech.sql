-- Reel 配音迁入 daemon 统一 SpeechService：应用配置不得再选择 edge/platform provider、
-- TTS/STT 模型或字幕 provider；只保留统一 voice catalog/profile 的 voice_id。
-- 保留运营自定义的 models.llm、素材、视频和引擎配置；已有 voice_id 也不覆盖。

UPDATE hasn_app_catalog
SET config_json = jsonb_set(
        config_json
            #- '{models,tts}'
            #- '{models,stt}'
            #- '{subtitle}'
            #- '{tts}',
        '{tts}',
        jsonb_build_object(
            'voice_id',
            COALESCE(config_json #> '{tts,voice_id}', '"Cherry"'::jsonb)
        ),
        true
    )
WHERE app_id = 'reel';
