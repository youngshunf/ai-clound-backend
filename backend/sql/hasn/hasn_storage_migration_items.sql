-- 存储迁移逐对象明细，保留迁移前位置以支持观察期回滚。
CREATE TABLE hasn_storage_migration_items (
    id                   BIGSERIAL     PRIMARY KEY,
    item_id              VARCHAR(40)   NOT NULL,
    job_id               VARCHAR(40)   NOT NULL,
    object_id            VARCHAR(40)   NOT NULL,
    source_storage_id    BIGINT        NOT NULL,
    source_object_key    VARCHAR(1024) NOT NULL,
    source_key_layout    VARCHAR(24)   NOT NULL,
    target_storage_id    BIGINT        NOT NULL,
    target_object_key    VARCHAR(1024) NOT NULL,
    source_size_bytes    BIGINT        NOT NULL,
    source_sha256        CHAR(64),
    verify_status        VARCHAR(24)   NOT NULL DEFAULT 'pending',
    source_cleanup_status VARCHAR(24)  NOT NULL DEFAULT 'retained',
    source_deleted_time  TIMESTAMPTZ,
    error_code           VARCHAR(64),
    created_time         TIMESTAMPTZ   NOT NULL DEFAULT NOW(),
    updated_time         TIMESTAMPTZ,
    CONSTRAINT ck_hasn_storage_migration_items_size CHECK (source_size_bytes >= 0)
);

CREATE UNIQUE INDEX uq_hasn_storage_migration_items_id
    ON hasn_storage_migration_items (item_id);
CREATE UNIQUE INDEX uq_hasn_storage_migration_items_job_object
    ON hasn_storage_migration_items (job_id, object_id);
CREATE INDEX idx_hasn_storage_migration_items_status
    ON hasn_storage_migration_items (job_id, verify_status);

COMMENT ON TABLE hasn_storage_migration_items IS '用户云存储迁移逐对象明细';
COMMENT ON COLUMN hasn_storage_migration_items.item_id IS '迁移明细稳定 ID';
COMMENT ON COLUMN hasn_storage_migration_items.job_id IS '所属迁移作业 ID';
COMMENT ON COLUMN hasn_storage_migration_items.object_id IS '被迁移物理对象 ID';
COMMENT ON COLUMN hasn_storage_migration_items.source_storage_id IS '迁移前存储配置 ID';
COMMENT ON COLUMN hasn_storage_migration_items.source_object_key IS '迁移前对象键';
COMMENT ON COLUMN hasn_storage_migration_items.source_key_layout IS '迁移前对象键布局（精确回滚依据）';
COMMENT ON COLUMN hasn_storage_migration_items.target_storage_id IS '目标存储配置 ID';
COMMENT ON COLUMN hasn_storage_migration_items.target_object_key IS '目标对象键';
COMMENT ON COLUMN hasn_storage_migration_items.source_size_bytes IS '源对象校验字节数';
COMMENT ON COLUMN hasn_storage_migration_items.source_sha256 IS '源对象校验 SHA-256';
COMMENT ON COLUMN hasn_storage_migration_items.verify_status IS '校验状态 (pending:待处理:blue/copied:已复制:orange/verified:已校验:green/switched:已切换:green/rolled_back:已回滚:gray/failed:失败:red)';
COMMENT ON COLUMN hasn_storage_migration_items.source_cleanup_status IS '源对象清理状态 (retained:观察期保留:blue/deleted:已清理:green/shared:跨主人共享保留:orange/failed:清理失败:red)';
COMMENT ON COLUMN hasn_storage_migration_items.source_deleted_time IS '源对象确认删除时间';
COMMENT ON COLUMN hasn_storage_migration_items.error_code IS '失败错误码';
COMMENT ON COLUMN hasn_storage_migration_items.created_time IS '创建时间';
COMMENT ON COLUMN hasn_storage_migration_items.updated_time IS '更新时间';
