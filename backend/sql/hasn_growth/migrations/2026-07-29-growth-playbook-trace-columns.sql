-- 为历史触达、活动和归因固定执行时打法快照。
-- 新列保持可空；存量若存在不完整快照，约束先以 NOT VALID 上线并阻断后续错误新写。

SET search_path TO hasn_growth, public;

ALTER TABLE outreach_message
    ADD COLUMN IF NOT EXISTS growth_project_playbook_id bigint,
    ADD COLUMN IF NOT EXISTS playbook_id bigint,
    ADD COLUMN IF NOT EXISTS playbook_version integer;

ALTER TABLE activity
    ADD COLUMN IF NOT EXISTS growth_project_playbook_id bigint,
    ADD COLUMN IF NOT EXISTS playbook_id bigint,
    ADD COLUMN IF NOT EXISTS playbook_version integer;

DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM pg_constraint
        WHERE conname = 'uq_growth_project_playbook_trace'
          AND conrelid = 'hasn_growth.growth_project_playbook'::regclass
    ) THEN
        ALTER TABLE growth_project_playbook
            ADD CONSTRAINT uq_growth_project_playbook_trace
            UNIQUE (id, growth_project_id, playbook_id, playbook_version);
    END IF;

    IF NOT EXISTS (
        SELECT 1 FROM pg_constraint
        WHERE conname = 'ck_growth_outreach_playbook_trace'
          AND conrelid = 'hasn_growth.outreach_message'::regclass
    ) THEN
        ALTER TABLE outreach_message
            ADD CONSTRAINT ck_growth_outreach_playbook_trace CHECK (
                (
                    growth_project_playbook_id IS NULL
                    AND playbook_id IS NULL
                    AND playbook_version IS NULL
                )
                OR (
                    growth_project_id IS NOT NULL
                    AND growth_project_playbook_id IS NOT NULL
                    AND playbook_id IS NOT NULL
                    AND playbook_version IS NOT NULL
                    AND playbook_version >= 1
                )
            ) NOT VALID;
    END IF;
    IF NOT EXISTS (
        SELECT 1 FROM pg_constraint
        WHERE conname = 'fk_growth_outreach_playbook_trace'
          AND conrelid = 'hasn_growth.outreach_message'::regclass
    ) THEN
        ALTER TABLE outreach_message
            ADD CONSTRAINT fk_growth_outreach_playbook_trace
            FOREIGN KEY (
                growth_project_playbook_id,
                growth_project_id,
                playbook_id,
                playbook_version
            )
            REFERENCES growth_project_playbook (
                id,
                growth_project_id,
                playbook_id,
                playbook_version
            )
            ON DELETE RESTRICT
            NOT VALID;
    END IF;

    IF NOT EXISTS (
        SELECT 1 FROM pg_constraint
        WHERE conname = 'ck_growth_activity_playbook_trace'
          AND conrelid = 'hasn_growth.activity'::regclass
    ) THEN
        ALTER TABLE activity
            ADD CONSTRAINT ck_growth_activity_playbook_trace CHECK (
                (
                    growth_project_playbook_id IS NULL
                    AND playbook_id IS NULL
                    AND playbook_version IS NULL
                )
                OR (
                    growth_project_id IS NOT NULL
                    AND growth_project_playbook_id IS NOT NULL
                    AND playbook_id IS NOT NULL
                    AND playbook_version IS NOT NULL
                    AND playbook_version >= 1
                )
            ) NOT VALID;
    END IF;
    IF NOT EXISTS (
        SELECT 1 FROM pg_constraint
        WHERE conname = 'fk_growth_activity_playbook_trace'
          AND conrelid = 'hasn_growth.activity'::regclass
    ) THEN
        ALTER TABLE activity
            ADD CONSTRAINT fk_growth_activity_playbook_trace
            FOREIGN KEY (
                growth_project_playbook_id,
                growth_project_id,
                playbook_id,
                playbook_version
            )
            REFERENCES growth_project_playbook (
                id,
                growth_project_id,
                playbook_id,
                playbook_version
            )
            ON DELETE RESTRICT
            NOT VALID;
    END IF;

    IF NOT EXISTS (
        SELECT 1 FROM pg_constraint
        WHERE conname = 'ck_growth_attribution_playbook_trace'
          AND conrelid = 'hasn_growth.growth_attribution_event'::regclass
    ) THEN
        ALTER TABLE growth_attribution_event
            ADD CONSTRAINT ck_growth_attribution_playbook_trace CHECK (
                (
                    growth_project_playbook_id IS NULL
                    AND playbook_id IS NULL
                    AND playbook_version IS NULL
                )
                OR (
                    growth_project_playbook_id IS NOT NULL
                    AND playbook_id IS NOT NULL
                    AND playbook_version IS NOT NULL
                    AND playbook_version >= 1
                )
            ) NOT VALID;
    END IF;
    IF NOT EXISTS (
        SELECT 1 FROM pg_constraint
        WHERE conname = 'fk_growth_attribution_playbook_trace'
          AND conrelid = 'hasn_growth.growth_attribution_event'::regclass
    ) THEN
        ALTER TABLE growth_attribution_event
            ADD CONSTRAINT fk_growth_attribution_playbook_trace
            FOREIGN KEY (
                growth_project_playbook_id,
                growth_project_id,
                playbook_id,
                playbook_version
            )
            REFERENCES growth_project_playbook (
                id,
                growth_project_id,
                playbook_id,
                playbook_version
            )
            ON DELETE RESTRICT
            NOT VALID;
    END IF;
END;
$$;
