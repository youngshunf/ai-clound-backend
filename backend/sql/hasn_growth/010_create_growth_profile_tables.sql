-- AI 获客项目化 v4：S5 画像版本与待确认建议。
-- 已确认画像只增版本；分身和系统只能写待确认建议，不得覆盖当前画像。

SET search_path TO hasn_growth, public;

CREATE TABLE IF NOT EXISTS growth_profile_version (
    id bigserial PRIMARY KEY,
    growth_project_id uuid NOT NULL
        REFERENCES growth_project(id) ON DELETE RESTRICT,
    version integer NOT NULL,
    product_profile jsonb NOT NULL DEFAULT '{}'::jsonb,
    icp_profile jsonb NOT NULL DEFAULT '{}'::jsonb,
    knowledge_document_versions jsonb NOT NULL DEFAULT '[]'::jsonb,
    source_hash varchar(64) NOT NULL,
    confirmed_by_kind varchar(16) NOT NULL,
    confirmed_by_id varchar(64) NOT NULL,
    created_time timestamptz NOT NULL DEFAULT now(),
    updated_time timestamptz DEFAULT now(),
    CONSTRAINT uq_growth_profile_version
        UNIQUE (growth_project_id, version),
    CONSTRAINT ck_growth_profile_version_positive CHECK (version >= 1),
    CONSTRAINT ck_growth_profile_version_actor CHECK (
        confirmed_by_kind IN ('owner', 'migration')
    ),
    CONSTRAINT ck_growth_profile_version_documents_array CHECK (
        jsonb_typeof(knowledge_document_versions) = 'array'
    )
);
COMMENT ON TABLE growth_profile_version IS '获客项目已确认画像的不可变版本历史';
COMMENT ON COLUMN growth_profile_version.knowledge_document_versions IS
    '参与画像确认的 Knowledge 文档及版本 [{document_id,version}]';
COMMENT ON COLUMN growth_profile_version.source_hash IS
    '参与文档稳定 ID 与版本的规范化 SHA256';
COMMENT ON COLUMN growth_profile_version.confirmed_by_kind IS
    '确认主体 (owner:主人:blue/migration:迁移:gray)';

CREATE INDEX IF NOT EXISTS idx_growth_profile_version_project_time
    ON growth_profile_version (growth_project_id, version DESC);

CREATE TABLE IF NOT EXISTS growth_profile_suggestion (
    id bigserial PRIMARY KEY,
    growth_project_id uuid NOT NULL
        REFERENCES growth_project(id) ON DELETE RESTRICT,
    expected_version integer NOT NULL,
    product_profile jsonb NOT NULL DEFAULT '{}'::jsonb,
    icp_profile jsonb NOT NULL DEFAULT '{}'::jsonb,
    knowledge_document_versions jsonb NOT NULL DEFAULT '[]'::jsonb,
    source_hash varchar(64) NOT NULL,
    proposed_by_kind varchar(16) NOT NULL,
    proposed_by_id varchar(64) NOT NULL,
    trace_id uuid NOT NULL,
    idempotency_key varchar(200) NOT NULL,
    status varchar(16) NOT NULL DEFAULT 'pending',
    reviewed_by_owner_id varchar(64),
    reviewed_time timestamptz,
    created_time timestamptz NOT NULL DEFAULT now(),
    updated_time timestamptz DEFAULT now(),
    CONSTRAINT uq_growth_profile_suggestion_trace
        UNIQUE (growth_project_id, trace_id),
    CONSTRAINT uq_growth_profile_suggestion_idempotency
        UNIQUE (growth_project_id, idempotency_key),
    CONSTRAINT ck_growth_profile_suggestion_expected_version CHECK (
        expected_version >= 1
    ),
    CONSTRAINT ck_growth_profile_suggestion_actor CHECK (
        proposed_by_kind IN ('agent', 'system')
    ),
    CONSTRAINT ck_growth_profile_suggestion_status CHECK (
        status IN ('pending', 'accepted', 'rejected', 'stale')
    ),
    CONSTRAINT ck_growth_profile_suggestion_documents_array CHECK (
        jsonb_typeof(knowledge_document_versions) = 'array'
    )
);
COMMENT ON TABLE growth_profile_suggestion IS '分身或系统提出、等待主人确认的画像建议';
COMMENT ON COLUMN growth_profile_suggestion.proposed_by_kind IS
    '建议主体 (agent:AI分身:purple/system:系统:gray)';
COMMENT ON COLUMN growth_profile_suggestion.status IS
    '状态 (pending:待确认:orange/accepted:已接受:green/rejected:已拒绝:red/stale:版本冲突:gray)';

CREATE INDEX IF NOT EXISTS idx_growth_profile_suggestion_project_status
    ON growth_profile_suggestion (growth_project_id, status, created_time DESC);
