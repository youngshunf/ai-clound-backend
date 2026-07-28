-- 用户云存储并发上传预占记录。
CREATE TABLE hasn_storage_reservations (
    id                 BIGSERIAL     PRIMARY KEY,
    reservation_id     VARCHAR(40)   NOT NULL,
    owner_hasn_id      VARCHAR(40)   NOT NULL,
    object_id          VARCHAR(40)   NOT NULL,
    result_asset_id    VARCHAR(40),
    idempotency_key    VARCHAR(128)  NOT NULL,
    request_fingerprint VARCHAR(64),
    reserved_bytes     BIGINT        NOT NULL,
    status             VARCHAR(24)   NOT NULL DEFAULT 'reserved',
    expires_time       TIMESTAMPTZ   NOT NULL,
    created_time       TIMESTAMPTZ   NOT NULL DEFAULT NOW(),
    updated_time       TIMESTAMPTZ,
    CONSTRAINT ck_hasn_storage_reservations_bytes CHECK (reserved_bytes >= 0)
);

CREATE UNIQUE INDEX uq_hasn_storage_reservations_id
    ON hasn_storage_reservations (reservation_id);
CREATE UNIQUE INDEX uq_hasn_storage_reservations_owner_idempotency
    ON hasn_storage_reservations (owner_hasn_id, idempotency_key);
CREATE UNIQUE INDEX uq_hasn_storage_reservations_object
    ON hasn_storage_reservations (object_id);
CREATE INDEX idx_hasn_storage_reservations_expiry
    ON hasn_storage_reservations (expires_time)
    WHERE status = 'reserved';

COMMENT ON TABLE hasn_storage_reservations IS '用户云存储上传预占记录';
COMMENT ON COLUMN hasn_storage_reservations.reservation_id IS '预占稳定 ID';
COMMENT ON COLUMN hasn_storage_reservations.owner_hasn_id IS '所属主人 hasn_id';
COMMENT ON COLUMN hasn_storage_reservations.object_id IS '预生成物理对象 ID';
COMMENT ON COLUMN hasn_storage_reservations.result_asset_id IS '成功提交后的逻辑资产 ID';
COMMENT ON COLUMN hasn_storage_reservations.idempotency_key IS '主人范围内的调用幂等键';
COMMENT ON COLUMN hasn_storage_reservations.request_fingerprint IS '服务端计算的请求载荷 SHA-256 指纹';
COMMENT ON COLUMN hasn_storage_reservations.reserved_bytes IS '当前预占字节数';
COMMENT ON COLUMN hasn_storage_reservations.status IS '预占状态 (reserved:已预占:orange/committed:已提交:green/released:已释放:gray/expired:已过期:red)';
COMMENT ON COLUMN hasn_storage_reservations.expires_time IS '预占过期时间';
COMMENT ON COLUMN hasn_storage_reservations.created_time IS '创建时间';
COMMENT ON COLUMN hasn_storage_reservations.updated_time IS '更新时间';
