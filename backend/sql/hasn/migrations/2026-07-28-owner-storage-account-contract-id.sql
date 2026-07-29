-- 存储账户引用现有订阅合同的 BIGINT 主键，禁止以 UUID 类型造成无法投影。
ALTER TABLE hasn_storage_accounts
    ALTER COLUMN source_subscription_id TYPE BIGINT
    USING NULL;
