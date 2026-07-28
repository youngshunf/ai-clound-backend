-- AI 获客项目化 v4：S3 项目挂靠、状态迁移和边界异常隔离清单。
-- 隔离记录只保存稳定行键、原因码和无敏感详情，不保存联系人明文。

SET search_path TO hasn_growth, public;

CREATE TABLE IF NOT EXISTS growth_project_migration_quarantine (
    id bigserial PRIMARY KEY,
    source_table varchar(64) NOT NULL,
    source_record_id varchar(64) NOT NULL,
    reason_code varchar(64) NOT NULL,
    owner_scope_hint varchar(16),
    user_id_hint bigint,
    enterprise_id_hint bigint,
    details jsonb NOT NULL DEFAULT '{}'::jsonb,
    status varchar(16) NOT NULL DEFAULT 'pending',
    resolution_note text,
    resolved_by varchar(64),
    resolved_time timestamptz,
    created_time timestamptz NOT NULL DEFAULT now(),
    updated_time timestamptz DEFAULT now(),
    CONSTRAINT uq_growth_project_migration_quarantine_source
        UNIQUE (source_table, source_record_id, reason_code),
    CONSTRAINT ck_growth_project_migration_quarantine_status
        CHECK (status IN ('pending', 'resolved', 'ignored'))
);

COMMENT ON TABLE growth_project_migration_quarantine
    IS '获客项目挂靠、状态迁移和归属边界异常的无敏感隔离清单';
COMMENT ON COLUMN growth_project_migration_quarantine.details
    IS '只允许原因分类、状态名和稳定资源键，禁止保存联系人明文';

CREATE INDEX IF NOT EXISTS idx_growth_project_migration_quarantine_pending
    ON growth_project_migration_quarantine (status, source_table, id)
    WHERE status = 'pending';
