-- new-api 解耦改 API 迁移（D5/§5A，2026-06-15）
-- 为映射表 llm_newapi_user_mapping 增三列：
--   newapi_access_token     用户 new-api access_token（Fernet 密文）——建 relay token 必须以用户身份，
--                           解耦后无 DB 直写 tokens 表，改缓存 access_token 复用，避免每次重设密码+登录。
--   last_synced_used_quota  §5A 积分账本：上次同步时 new-api used_quota 游标（单调递增，差额回扣积分）。
--   last_synced_at          上次用量同步时间。
-- 幂等：IF NOT EXISTS；可重复执行。public schema。

ALTER TABLE llm_newapi_user_mapping
    ADD COLUMN IF NOT EXISTS newapi_access_token VARCHAR(512);
COMMENT ON COLUMN llm_newapi_user_mapping.newapi_access_token
    IS '用户 new-api access_token（Fernet 加密，建 relay token 复用）';

ALTER TABLE llm_newapi_user_mapping
    ADD COLUMN IF NOT EXISTS last_synced_used_quota BIGINT NOT NULL DEFAULT 0;
COMMENT ON COLUMN llm_newapi_user_mapping.last_synced_used_quota
    IS '上次同步时的 new-api used_quota 游标（增量回扣用）';

ALTER TABLE llm_newapi_user_mapping
    ADD COLUMN IF NOT EXISTS last_synced_at TIMESTAMPTZ;
COMMENT ON COLUMN llm_newapi_user_mapping.last_synced_at
    IS '上次用量同步时间';
