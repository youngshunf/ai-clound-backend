-- S11 codegen 事实源：下一周期经营复盘建议。

SET search_path TO hasn_growth, public;

CREATE TABLE IF NOT EXISTS growth_review_suggestion (
    id bigserial PRIMARY KEY,
    growth_project_id uuid NOT NULL REFERENCES growth_project(id) ON DELETE RESTRICT,
    suggestion_kind varchar(16) NOT NULL,
    proposal jsonb NOT NULL DEFAULT '{}'::jsonb,
    evidence jsonb NOT NULL DEFAULT '{}'::jsonb,
    proposed_by_kind varchar(16) NOT NULL,
    proposed_by_id varchar(64) NOT NULL,
    idempotency_key varchar(200) NOT NULL,
    status varchar(16) NOT NULL DEFAULT 'pending',
    applied_version integer,
    reviewed_by_owner_id varchar(64),
    reviewed_time timestamptz,
    created_time timestamptz NOT NULL DEFAULT now(),
    updated_time timestamptz DEFAULT now(),
    CONSTRAINT uq_growth_review_suggestion_idempotency
        UNIQUE (growth_project_id, idempotency_key),
    CONSTRAINT ck_growth_review_suggestion_kind CHECK (
        suggestion_kind IN ('icp', 'channel', 'playbook')
    ),
    CONSTRAINT ck_growth_review_suggestion_actor CHECK (
        proposed_by_kind IN ('agent', 'system')
    ),
    CONSTRAINT ck_growth_review_suggestion_status CHECK (
        status IN ('pending', 'accepted', 'rejected', 'stale')
    ),
    CONSTRAINT ck_growth_review_suggestion_applied_version CHECK (
        applied_version IS NULL OR applied_version >= 1
    )
);

COMMENT ON TABLE growth_review_suggestion IS '下一周期 ICP、渠道与打法建议及 Owner 审阅结果';
COMMENT ON COLUMN growth_review_suggestion.evidence IS
    '建议证据范围、样本量、数据不足和局限，禁止保存联系人明文';

CREATE INDEX IF NOT EXISTS idx_growth_review_suggestion_project_status
    ON growth_review_suggestion (growth_project_id, status, created_time DESC);
