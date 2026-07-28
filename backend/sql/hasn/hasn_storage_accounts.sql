-- 用户云存储账户投影：原始事实仍以物理对象汇总为准。
CREATE TABLE hasn_storage_accounts (
    id                         BIGSERIAL   PRIMARY KEY,
    owner_hasn_id              VARCHAR(40) NOT NULL,
    quota_bytes                BIGINT      NOT NULL DEFAULT 0,
    used_bytes                 BIGINT      NOT NULL DEFAULT 0,
    reserved_bytes             BIGINT      NOT NULL DEFAULT 0,
    quota_source               VARCHAR(32) NOT NULL DEFAULT 'free_policy',
    quota_version              VARCHAR(64) NOT NULL DEFAULT '',
    source_subscription_id     BIGINT,
    quota_valid_until          TIMESTAMPTZ,
    state                      VARCHAR(24) NOT NULL DEFAULT 'active',
    created_time               TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_time               TIMESTAMPTZ,
    CONSTRAINT ck_hasn_storage_accounts_counts
        CHECK (quota_bytes >= 0 AND used_bytes >= 0 AND reserved_bytes >= 0)
);

CREATE UNIQUE INDEX uq_hasn_storage_accounts_owner
    ON hasn_storage_accounts (owner_hasn_id);
CREATE INDEX idx_hasn_storage_accounts_quota_expiry
    ON hasn_storage_accounts (quota_valid_until)
    WHERE quota_valid_until IS NOT NULL;

COMMENT ON TABLE hasn_storage_accounts IS '用户云存储账户投影';
COMMENT ON COLUMN hasn_storage_accounts.owner_hasn_id IS '所属主人 hasn_id';
COMMENT ON COLUMN hasn_storage_accounts.quota_bytes IS '当前生效配额字节数';
COMMENT ON COLUMN hasn_storage_accounts.used_bytes IS '已确认计费对象字节数';
COMMENT ON COLUMN hasn_storage_accounts.reserved_bytes IS '上传中预占字节数';
COMMENT ON COLUMN hasn_storage_accounts.quota_source IS '配额来源 (free_policy:免费政策:blue/subscription:订阅合同:green/admin_override:管理覆盖:orange)';
COMMENT ON COLUMN hasn_storage_accounts.quota_version IS '免费政策或合同版本';
COMMENT ON COLUMN hasn_storage_accounts.source_subscription_id IS '付费合同来源';
COMMENT ON COLUMN hasn_storage_accounts.quota_valid_until IS '当前配额有效期终点';
COMMENT ON COLUMN hasn_storage_accounts.state IS '账户状态 (active:正常:green/over_quota:超额:orange/suspended:暂停:red)';
COMMENT ON COLUMN hasn_storage_accounts.created_time IS '创建时间';
COMMENT ON COLUMN hasn_storage_accounts.updated_time IS '更新时间';
