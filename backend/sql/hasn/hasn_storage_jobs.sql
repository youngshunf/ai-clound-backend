-- 用户云存储持久作业与补偿 outbox。
CREATE TABLE hasn_storage_jobs (
    id                 BIGSERIAL     PRIMARY KEY,
    job_id             VARCHAR(40)   NOT NULL,
    owner_hasn_id      VARCHAR(40),
    job_type           VARCHAR(32)   NOT NULL,
    status             VARCHAR(24)   NOT NULL DEFAULT 'pending',
    cursor             JSONB         NOT NULL DEFAULT '{}'::JSONB,
    total_items        BIGINT        NOT NULL DEFAULT 0,
    processed_items    BIGINT        NOT NULL DEFAULT 0,
    failed_items       BIGINT        NOT NULL DEFAULT 0,
    error_code         VARCHAR(64),
    payload            JSONB         NOT NULL DEFAULT '{}'::JSONB,
    result             JSONB         NOT NULL DEFAULT '{}'::JSONB,
    attempt_count      INTEGER       NOT NULL DEFAULT 0,
    next_attempt_time  TIMESTAMPTZ,
    expires_time       TIMESTAMPTZ,
    created_time       TIMESTAMPTZ   NOT NULL DEFAULT NOW(),
    updated_time       TIMESTAMPTZ,
    CONSTRAINT ck_hasn_storage_jobs_counts CHECK (
        total_items >= 0
        AND processed_items >= 0
        AND failed_items >= 0
        AND attempt_count >= 0
    )
);

CREATE UNIQUE INDEX uq_hasn_storage_jobs_id
    ON hasn_storage_jobs (job_id);
CREATE INDEX idx_hasn_storage_jobs_owner
    ON hasn_storage_jobs (owner_hasn_id, job_type, status);
CREATE INDEX idx_hasn_storage_jobs_retry
    ON hasn_storage_jobs (next_attempt_time)
    WHERE status IN ('pending', 'retrying');

COMMENT ON TABLE hasn_storage_jobs IS '用户云存储持久作业与补偿 outbox';
COMMENT ON COLUMN hasn_storage_jobs.job_id IS '作业稳定 ID';
COMMENT ON COLUMN hasn_storage_jobs.owner_hasn_id IS '所属主人 hasn_id；平台对账可为空';
COMMENT ON COLUMN hasn_storage_jobs.job_type IS '作业类型 (storage_export:导出:blue/storage_migration:迁移:orange/storage_reconcile:对账:green/object_purge:对象清理:red/orphan_cleanup:孤儿清理:orange/unbound_asset_sweep:无引用资产清扫:blue/multipart_abort_sweep:分片清理:purple)';
COMMENT ON COLUMN hasn_storage_jobs.status IS '作业状态 (pending:待执行:blue/running:执行中:orange/retrying:重试中:orange/paused:已暂停:gray/succeeded:成功:green/failed:失败:red/cancelled:已取消:gray)';
COMMENT ON COLUMN hasn_storage_jobs.cursor IS '数据库权威游标';
COMMENT ON COLUMN hasn_storage_jobs.total_items IS '总条目数';
COMMENT ON COLUMN hasn_storage_jobs.processed_items IS '已处理条目数';
COMMENT ON COLUMN hasn_storage_jobs.failed_items IS '失败条目数';
COMMENT ON COLUMN hasn_storage_jobs.error_code IS '稳定错误码';
COMMENT ON COLUMN hasn_storage_jobs.payload IS '作业输入与补偿数据';
COMMENT ON COLUMN hasn_storage_jobs.result IS '作业结果摘要';
COMMENT ON COLUMN hasn_storage_jobs.attempt_count IS '已执行次数';
COMMENT ON COLUMN hasn_storage_jobs.next_attempt_time IS '下次重试时间';
COMMENT ON COLUMN hasn_storage_jobs.expires_time IS '作业或产物过期时间';
COMMENT ON COLUMN hasn_storage_jobs.created_time IS '创建时间';
COMMENT ON COLUMN hasn_storage_jobs.updated_time IS '更新时间';
