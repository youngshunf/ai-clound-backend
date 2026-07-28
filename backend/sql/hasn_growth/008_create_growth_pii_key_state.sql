-- Growth PII 写入密钥版本栅栏；业务写入版本只允许单调递增。

SET search_path TO hasn_growth, public;

CREATE TABLE IF NOT EXISTS growth_pii_key_state (
    id smallint PRIMARY KEY,
    min_encryption_write_version integer NOT NULL,
    min_hmac_write_version integer NOT NULL,
    created_time timestamptz NOT NULL DEFAULT now(),
    updated_time timestamptz DEFAULT now(),
    CONSTRAINT ck_growth_pii_key_state_singleton CHECK (id = 1),
    CONSTRAINT ck_growth_pii_key_state_versions CHECK (
        min_encryption_write_version >= 1 AND min_hmac_write_version >= 1
    )
);

COMMENT ON TABLE growth_pii_key_state IS 'Growth PII 写入密钥版本单例栅栏';
COMMENT ON COLUMN growth_pii_key_state.id IS '固定为 1 的单例主键';
COMMENT ON COLUMN growth_pii_key_state.min_encryption_write_version IS '允许写入的最低加密密钥版本';
COMMENT ON COLUMN growth_pii_key_state.min_hmac_write_version IS '允许写入的最低 HMAC 密钥版本';

INSERT INTO growth_pii_key_state (
    id,
    min_encryption_write_version,
    min_hmac_write_version
)
VALUES (1, 1, 1)
ON CONFLICT (id) DO NOTHING;
