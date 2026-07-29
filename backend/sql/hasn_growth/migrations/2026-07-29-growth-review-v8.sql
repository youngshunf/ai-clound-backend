-- S11：经营复盘建议、渠道策略版本与服务端发送门禁。

SET search_path TO hasn_growth, public;

ALTER TABLE growth_project
    ADD COLUMN IF NOT EXISTS quiet_hours_start smallint NOT NULL DEFAULT 21,
    ADD COLUMN IF NOT EXISTS quiet_hours_end smallint NOT NULL DEFAULT 9,
    ADD COLUMN IF NOT EXISTS daily_outreach_limit integer NOT NULL DEFAULT 20,
    ADD COLUMN IF NOT EXISTS policy_version integer NOT NULL DEFAULT 1;

DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1
        FROM pg_constraint
        WHERE conname = 'ck_growth_project_quiet_hours'
          AND conrelid = 'hasn_growth.growth_project'::regclass
    ) THEN
        ALTER TABLE growth_project
            ADD CONSTRAINT ck_growth_project_quiet_hours CHECK (
                quiet_hours_start BETWEEN 0 AND 23
                AND quiet_hours_end BETWEEN 0 AND 23
                AND quiet_hours_start <> quiet_hours_end
            );
    END IF;
    IF NOT EXISTS (
        SELECT 1
        FROM pg_constraint
        WHERE conname = 'ck_growth_project_daily_outreach_limit'
          AND conrelid = 'hasn_growth.growth_project'::regclass
    ) THEN
        ALTER TABLE growth_project
            ADD CONSTRAINT ck_growth_project_daily_outreach_limit CHECK (
                daily_outreach_limit BETWEEN 1 AND 10000
            );
    END IF;
    IF NOT EXISTS (
        SELECT 1
        FROM pg_constraint
        WHERE conname = 'ck_growth_project_policy_version'
          AND conrelid = 'hasn_growth.growth_project'::regclass
    ) THEN
        ALTER TABLE growth_project
            ADD CONSTRAINT ck_growth_project_policy_version CHECK (policy_version >= 1);
    END IF;
END
$$;

COMMENT ON COLUMN growth_project.quiet_hours_start IS '静默时段开始小时，使用项目时区的 0–23 整点';
COMMENT ON COLUMN growth_project.quiet_hours_end IS '静默时段结束小时，使用项目时区的 0–23 整点';
COMMENT ON COLUMN growth_project.daily_outreach_limit IS '项目每日发送成功或人工发送证明的触达上限';
COMMENT ON COLUMN growth_project.policy_version IS '渠道、静默时段、频控和预算策略版本';

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
