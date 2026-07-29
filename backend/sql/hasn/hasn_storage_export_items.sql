-- 存储导出逐资产快照，冻结导出创建时的目录、业务来源与物理校验信息。
CREATE TABLE hasn_storage_export_items (
    id                   BIGSERIAL      PRIMARY KEY,
    item_id              VARCHAR(40)    NOT NULL,
    job_id               VARCHAR(40)    NOT NULL,
    owner_hasn_id        VARCHAR(40)    NOT NULL,
    asset_id             VARCHAR(40)    NOT NULL,
    logical_path         TEXT           NOT NULL,
    original_name        VARCHAR(500)   NOT NULL DEFAULT '',
    mime                 VARCHAR(200)   NOT NULL,
    source_app           VARCHAR(80),
    access               VARCHAR(16)    NOT NULL,
    asset_created_time   TIMESTAMPTZ    NOT NULL,
    lifecycle_status     VARCHAR(20)    NOT NULL,
    bindings             JSONB          NOT NULL DEFAULT '[]'::JSONB,
    object_id            VARCHAR(40)    NOT NULL,
    storage_id           BIGINT         NOT NULL,
    object_key           VARCHAR(1024)  NOT NULL,
    size_bytes           BIGINT         NOT NULL,
    sha256               CHAR(64),
    verify_status        VARCHAR(24)     NOT NULL DEFAULT 'pending',
    error_code           VARCHAR(64),
    created_time         TIMESTAMPTZ     NOT NULL DEFAULT NOW(),
    updated_time         TIMESTAMPTZ,
    CONSTRAINT fk_hasn_storage_export_items_job
        FOREIGN KEY (job_id) REFERENCES hasn_storage_jobs(job_id) ON DELETE CASCADE,
    CONSTRAINT ck_hasn_storage_export_items_size CHECK (size_bytes >= 0)
);

CREATE UNIQUE INDEX uq_hasn_storage_export_items_id
    ON hasn_storage_export_items (item_id);
CREATE UNIQUE INDEX uq_hasn_storage_export_items_job_asset
    ON hasn_storage_export_items (job_id, asset_id);
CREATE INDEX idx_hasn_storage_export_items_owner_job
    ON hasn_storage_export_items (owner_hasn_id, job_id, verify_status);

COMMENT ON TABLE hasn_storage_export_items IS '用户云存储导出逐资产不可变快照';
COMMENT ON COLUMN hasn_storage_export_items.item_id IS '导出明细稳定 ID';
COMMENT ON COLUMN hasn_storage_export_items.job_id IS '所属导出作业 ID';
COMMENT ON COLUMN hasn_storage_export_items.owner_hasn_id IS '所属主人 hasn_id';
COMMENT ON COLUMN hasn_storage_export_items.asset_id IS '快照中的逻辑资产 ID';
COMMENT ON COLUMN hasn_storage_export_items.logical_path IS '创建导出时冻结的逻辑路径';
COMMENT ON COLUMN hasn_storage_export_items.original_name IS '创建导出时冻结的原始文件名';
COMMENT ON COLUMN hasn_storage_export_items.mime IS 'MIME 类型';
COMMENT ON COLUMN hasn_storage_export_items.source_app IS '业务来源应用';
COMMENT ON COLUMN hasn_storage_export_items.access IS '对象访问级别';
COMMENT ON COLUMN hasn_storage_export_items.asset_created_time IS '资产原始创建时间';
COMMENT ON COLUMN hasn_storage_export_items.lifecycle_status IS '创建导出时的生命周期状态';
COMMENT ON COLUMN hasn_storage_export_items.bindings IS '创建导出时冻结的业务引用清单';
COMMENT ON COLUMN hasn_storage_export_items.object_id IS '物理对象 ID';
COMMENT ON COLUMN hasn_storage_export_items.storage_id IS '快照中的存储配置 ID';
COMMENT ON COLUMN hasn_storage_export_items.object_key IS '快照中的对象键';
COMMENT ON COLUMN hasn_storage_export_items.size_bytes IS '预期对象字节数';
COMMENT ON COLUMN hasn_storage_export_items.sha256 IS '预期对象 SHA-256';
COMMENT ON COLUMN hasn_storage_export_items.verify_status IS '校验状态 (pending:待校验:blue/verified:已校验:green/failed:失败:red)';
COMMENT ON COLUMN hasn_storage_export_items.error_code IS '失败错误码';
COMMENT ON COLUMN hasn_storage_export_items.created_time IS '创建时间';
COMMENT ON COLUMN hasn_storage_export_items.updated_time IS '更新时间';
