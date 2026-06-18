-- 放宽唤星映射表里的 new-api token key 长度。
-- new-api tokens.key 当前可到 VARCHAR(128)，本地映射表若仍是 VARCHAR(48)，
-- 清理本地用户/映射但保留 new-api 历史 token 后，登录补映射会因长 key 写入失败。

ALTER TABLE llm_newapi_user_mapping
    ALTER COLUMN newapi_token_key TYPE VARCHAR(128);
