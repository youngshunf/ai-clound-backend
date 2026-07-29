-- 用户云存储物理对象表：一行代表一份真实占用空间的字节。
CREATE TABLE hasn_storage_objects (
    id                    BIGSERIAL     PRIMARY KEY,
    object_id             VARCHAR(40)   NOT NULL,
    owner_hasn_id         VARCHAR(40),
    storage_id            BIGINT        NOT NULL,
    object_key            VARCHAR(1024) NOT NULL,
    key_layout            VARCHAR(16)   NOT NULL DEFAULT 'owner_scoped',
    access                VARCHAR(16)   NOT NULL DEFAULT 'private',
    size_bytes            BIGINT        NOT NULL DEFAULT 0,
    sha256                CHAR(64),
    billable_to_owner     BOOLEAN       NOT NULL DEFAULT TRUE,
    ref_count             INTEGER       NOT NULL DEFAULT 0,
    state                 VARCHAR(24)   NOT NULL DEFAULT 'pending',
    created_time          TIMESTAMPTZ   NOT NULL DEFAULT NOW(),
    updated_time          TIMESTAMPTZ,
    CONSTRAINT ck_hasn_storage_objects_counts
        CHECK (size_bytes >= 0 AND ref_count >= 0),
    CONSTRAINT ck_hasn_storage_objects_owner
        CHECK (NOT billable_to_owner OR owner_hasn_id IS NOT NULL)
);

CREATE UNIQUE INDEX uq_hasn_storage_objects_object_id
    ON hasn_storage_objects (object_id);
CREATE UNIQUE INDEX uq_hasn_storage_objects_owner_sha256
    ON hasn_storage_objects (owner_hasn_id, sha256, access)
    WHERE billable_to_owner AND state <> 'deleted' AND sha256 IS NOT NULL;
CREATE UNIQUE INDEX uq_hasn_storage_objects_owner_key
    ON hasn_storage_objects (storage_id, object_key)
    WHERE key_layout = 'owner_scoped';
CREATE INDEX idx_hasn_storage_objects_location
    ON hasn_storage_objects (storage_id, object_key);
CREATE INDEX idx_hasn_storage_objects_owner_state
    ON hasn_storage_objects (owner_hasn_id, state);

COMMENT ON TABLE hasn_storage_objects IS '用户云存储物理对象表';
COMMENT ON COLUMN hasn_storage_objects.object_id IS '物理对象稳定 ID';
COMMENT ON COLUMN hasn_storage_objects.owner_hasn_id IS '所属主人 hasn_id；平台资产为空';
COMMENT ON COLUMN hasn_storage_objects.storage_id IS '对象存储配置 ID';
COMMENT ON COLUMN hasn_storage_objects.object_key IS '对象键（相对存储根）';
COMMENT ON COLUMN hasn_storage_objects.key_layout IS '对象键布局 (owner_scoped:主人隔离:blue/legacy:存量兼容:orange/platform:平台资产:green)';
COMMENT ON COLUMN hasn_storage_objects.access IS '访问类型 (private:私有:orange/public:公开:green)';
COMMENT ON COLUMN hasn_storage_objects.size_bytes IS '服务端校准后的权威字节数';
COMMENT ON COLUMN hasn_storage_objects.sha256 IS '服务端计算的 SHA-256';
COMMENT ON COLUMN hasn_storage_objects.billable_to_owner IS '是否计入主人配额';
COMMENT ON COLUMN hasn_storage_objects.ref_count IS '非删除逻辑资产引用数';
COMMENT ON COLUMN hasn_storage_objects.state IS '对象状态 (pending:待确认:orange/active:可用:green/deleting:删除中:orange/deleted:已删除:gray/missing:对象缺失:red/error:异常:red)';
COMMENT ON COLUMN hasn_storage_objects.created_time IS '创建时间';
COMMENT ON COLUMN hasn_storage_objects.updated_time IS '更新时间';
