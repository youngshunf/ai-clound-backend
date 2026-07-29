-- AI 获客项目化 v4：S1 存量表加法迁移。
-- 项目列保持可空，存量回填、状态映射和旧明文清理分别在 S3/S10 完成。

SET search_path TO hasn_growth, public;

ALTER TABLE customer
    ADD COLUMN IF NOT EXISTS growth_project_id uuid;
ALTER TABLE opportunity
    ADD COLUMN IF NOT EXISTS growth_project_id uuid;
ALTER TABLE outreach_message
    ADD COLUMN IF NOT EXISTS growth_project_id uuid;
ALTER TABLE activity
    ADD COLUMN IF NOT EXISTS growth_project_id uuid;
ALTER TABLE form_submission
    ADD COLUMN IF NOT EXISTS growth_project_id uuid;

ALTER TABLE outreach_message
    ADD COLUMN IF NOT EXISTS approval_status varchar(24),
    ADD COLUMN IF NOT EXISTS delivery_status varchar(32),
    ADD COLUMN IF NOT EXISTS approval_version integer,
    ADD COLUMN IF NOT EXISTS content_version integer,
    ADD COLUMN IF NOT EXISTS manual_attested_at timestamptz,
    ADD COLUMN IF NOT EXISTS manual_attested_by varchar(64),
    ADD COLUMN IF NOT EXISTS manual_attested_channel varchar(24);

ALTER TABLE outreach_message
    ALTER COLUMN approval_status SET DEFAULT 'draft',
    ALTER COLUMN delivery_status SET DEFAULT 'not_queued',
    ALTER COLUMN approval_version SET DEFAULT 1,
    ALTER COLUMN content_version SET DEFAULT 1,
    ALTER COLUMN auto_approved SET DEFAULT false;

ALTER TABLE playbook
    ADD COLUMN IF NOT EXISTS version integer;
ALTER TABLE playbook
    ALTER COLUMN version SET DEFAULT 1;
UPDATE playbook SET version = 1 WHERE version IS NULL;

ALTER TABLE optout_record
    ADD COLUMN IF NOT EXISTS owner_scope varchar(16),
    ADD COLUMN IF NOT EXISTS enterprise_id bigint,
    ADD COLUMN IF NOT EXISTS address_hmac varchar(128),
    ADD COLUMN IF NOT EXISTS hash_key_version integer,
    ADD COLUMN IF NOT EXISTS growth_project_id uuid;
ALTER TABLE optout_record
    ALTER COLUMN owner_scope SET DEFAULT 'personal',
    ALTER COLUMN address_hash DROP NOT NULL;
UPDATE optout_record SET owner_scope = 'personal' WHERE owner_scope IS NULL;

ALTER TABLE form_submission
    ADD COLUMN IF NOT EXISTS publish_site_id bigint,
    ADD COLUMN IF NOT EXISTS platform_project_id uuid,
    ADD COLUMN IF NOT EXISTS idempotency_key varchar(200),
    ADD COLUMN IF NOT EXISTS submission_fingerprint varchar(128),
    ADD COLUMN IF NOT EXISTS contact_private_profile_id bigint,
    ADD COLUMN IF NOT EXISTS contact_channel_ids jsonb,
    ADD COLUMN IF NOT EXISTS privacy_notice_version varchar(64),
    ADD COLUMN IF NOT EXISTS consent_purpose varchar(128),
    ADD COLUMN IF NOT EXISTS consent_source varchar(128),
    ADD COLUMN IF NOT EXISTS consent_at timestamptz,
    ADD COLUMN IF NOT EXISTS ip_hmac varchar(128),
    ADD COLUMN IF NOT EXISTS spam_status varchar(16),
    ADD COLUMN IF NOT EXISTS spam_reason varchar(255),
    ADD COLUMN IF NOT EXISTS utm_source varchar(255),
    ADD COLUMN IF NOT EXISTS utm_medium varchar(255),
    ADD COLUMN IF NOT EXISTS utm_campaign varchar(255),
    ADD COLUMN IF NOT EXISTS utm_content varchar(255),
    ADD COLUMN IF NOT EXISTS utm_term varchar(255),
    ADD COLUMN IF NOT EXISTS referrer text,
    ADD COLUMN IF NOT EXISTS lead_contact_id bigint,
    ADD COLUMN IF NOT EXISTS project_lead_id bigint,
    ADD COLUMN IF NOT EXISTS task_id varchar(64);
ALTER TABLE form_submission
    ALTER COLUMN contact_channel_ids SET DEFAULT '[]'::jsonb,
    ALTER COLUMN spam_status SET DEFAULT 'unchecked';

CREATE UNIQUE INDEX IF NOT EXISTS uq_growth_form_submission_idempotency
    ON form_submission (publish_site_id, idempotency_key)
    WHERE publish_site_id IS NOT NULL AND idempotency_key IS NOT NULL;

CREATE UNIQUE INDEX IF NOT EXISTS uq_growth_optout_personal_hmac
    ON optout_record (user_id, channel, address_hmac, hash_key_version)
    WHERE owner_scope = 'personal'
      AND address_hmac IS NOT NULL
      AND hash_key_version IS NOT NULL;
CREATE UNIQUE INDEX IF NOT EXISTS uq_growth_optout_enterprise_hmac
    ON optout_record (enterprise_id, channel, address_hmac, hash_key_version)
    WHERE owner_scope = 'enterprise'
      AND address_hmac IS NOT NULL
      AND hash_key_version IS NOT NULL;

DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM pg_constraint
        WHERE conname = 'ck_growth_outreach_approval_status'
          AND conrelid = 'hasn_growth.outreach_message'::regclass
    ) THEN
        ALTER TABLE outreach_message
            ADD CONSTRAINT ck_growth_outreach_approval_status CHECK (
                approval_status IS NULL OR approval_status IN (
                    'draft', 'pending_approval', 'approved', 'rejected', 'cancelled'
                )
            );
    END IF;
    IF NOT EXISTS (
        SELECT 1 FROM pg_constraint
        WHERE conname = 'ck_growth_outreach_delivery_status'
          AND conrelid = 'hasn_growth.outreach_message'::regclass
    ) THEN
        ALTER TABLE outreach_message
            ADD CONSTRAINT ck_growth_outreach_delivery_status CHECK (
                delivery_status IS NULL OR delivery_status IN (
                    'not_queued', 'queued', 'sending', 'sent', 'delivered', 'failed',
                    'blocked_optout', 'blocked_compliance'
                )
            );
    END IF;
    IF NOT EXISTS (
        SELECT 1 FROM pg_constraint
        WHERE conname = 'ck_growth_outreach_versions'
          AND conrelid = 'hasn_growth.outreach_message'::regclass
    ) THEN
        ALTER TABLE outreach_message
            ADD CONSTRAINT ck_growth_outreach_versions CHECK (
                (approval_version IS NULL OR approval_version >= 1)
                AND (content_version IS NULL OR content_version >= 1)
            );
    END IF;
    IF NOT EXISTS (
        SELECT 1 FROM pg_constraint
        WHERE conname = 'ck_growth_playbook_version'
          AND conrelid = 'hasn_growth.playbook'::regclass
    ) THEN
        ALTER TABLE playbook
            ADD CONSTRAINT ck_growth_playbook_version CHECK (
                version IS NULL OR version >= 1
            );
    END IF;
    IF NOT EXISTS (
        SELECT 1 FROM pg_constraint
        WHERE conname = 'ck_growth_optout_owner_scope'
          AND conrelid = 'hasn_growth.optout_record'::regclass
    ) THEN
        ALTER TABLE optout_record
            ADD CONSTRAINT ck_growth_optout_owner_scope CHECK (
                (owner_scope = 'personal' AND enterprise_id IS NULL)
                OR (owner_scope = 'enterprise' AND enterprise_id IS NOT NULL)
            );
    END IF;
    IF NOT EXISTS (
        SELECT 1 FROM pg_constraint
        WHERE conname = 'ck_growth_optout_hmac_version'
          AND conrelid = 'hasn_growth.optout_record'::regclass
    ) THEN
        ALTER TABLE optout_record
            ADD CONSTRAINT ck_growth_optout_hmac_version CHECK (
                (address_hmac IS NULL AND hash_key_version IS NULL)
                OR (address_hmac IS NOT NULL AND hash_key_version >= 1)
            );
    END IF;
END;
$$;

DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM pg_constraint
        WHERE conname = 'fk_growth_customer_project'
          AND conrelid = 'hasn_growth.customer'::regclass
    ) THEN
        ALTER TABLE customer
            ADD CONSTRAINT fk_growth_customer_project
            FOREIGN KEY (growth_project_id)
            REFERENCES growth_project(id) ON DELETE RESTRICT;
    END IF;
    IF NOT EXISTS (
        SELECT 1 FROM pg_constraint
        WHERE conname = 'uq_growth_customer_id_project'
          AND conrelid = 'hasn_growth.customer'::regclass
    ) THEN
        ALTER TABLE customer
            ADD CONSTRAINT uq_growth_customer_id_project
            UNIQUE (id, growth_project_id);
    END IF;

    IF NOT EXISTS (
        SELECT 1 FROM pg_constraint
        WHERE conname = 'fk_growth_opportunity_project'
          AND conrelid = 'hasn_growth.opportunity'::regclass
    ) THEN
        ALTER TABLE opportunity
            ADD CONSTRAINT fk_growth_opportunity_project
            FOREIGN KEY (growth_project_id)
            REFERENCES growth_project(id) ON DELETE RESTRICT;
    END IF;
    IF NOT EXISTS (
        SELECT 1 FROM pg_constraint
        WHERE conname = 'uq_growth_opportunity_id_project'
          AND conrelid = 'hasn_growth.opportunity'::regclass
    ) THEN
        ALTER TABLE opportunity
            ADD CONSTRAINT uq_growth_opportunity_id_project
            UNIQUE (id, growth_project_id);
    END IF;
    IF NOT EXISTS (
        SELECT 1 FROM pg_constraint
        WHERE conname = 'fk_growth_opportunity_customer_project'
          AND conrelid = 'hasn_growth.opportunity'::regclass
    ) THEN
        ALTER TABLE opportunity
            ADD CONSTRAINT fk_growth_opportunity_customer_project
            FOREIGN KEY (customer_id, growth_project_id)
            REFERENCES customer(id, growth_project_id) ON DELETE RESTRICT;
    END IF;

    IF NOT EXISTS (
        SELECT 1 FROM pg_constraint
        WHERE conname = 'fk_growth_outreach_project'
          AND conrelid = 'hasn_growth.outreach_message'::regclass
    ) THEN
        ALTER TABLE outreach_message
            ADD CONSTRAINT fk_growth_outreach_project
            FOREIGN KEY (growth_project_id)
            REFERENCES growth_project(id) ON DELETE RESTRICT;
    END IF;
    IF NOT EXISTS (
        SELECT 1 FROM pg_constraint
        WHERE conname = 'fk_growth_outreach_customer_project'
          AND conrelid = 'hasn_growth.outreach_message'::regclass
    ) THEN
        ALTER TABLE outreach_message
            ADD CONSTRAINT fk_growth_outreach_customer_project
            FOREIGN KEY (customer_id, growth_project_id)
            REFERENCES customer(id, growth_project_id) ON DELETE RESTRICT;
    END IF;
    IF NOT EXISTS (
        SELECT 1 FROM pg_constraint
        WHERE conname = 'fk_growth_outreach_opportunity_project'
          AND conrelid = 'hasn_growth.outreach_message'::regclass
    ) THEN
        ALTER TABLE outreach_message
            ADD CONSTRAINT fk_growth_outreach_opportunity_project
            FOREIGN KEY (opportunity_id, growth_project_id)
            REFERENCES opportunity(id, growth_project_id) ON DELETE RESTRICT;
    END IF;

    IF NOT EXISTS (
        SELECT 1 FROM pg_constraint
        WHERE conname = 'fk_growth_activity_project'
          AND conrelid = 'hasn_growth.activity'::regclass
    ) THEN
        ALTER TABLE activity
            ADD CONSTRAINT fk_growth_activity_project
            FOREIGN KEY (growth_project_id)
            REFERENCES growth_project(id) ON DELETE RESTRICT;
    END IF;
    IF NOT EXISTS (
        SELECT 1 FROM pg_constraint
        WHERE conname = 'fk_growth_activity_customer_project'
          AND conrelid = 'hasn_growth.activity'::regclass
    ) THEN
        ALTER TABLE activity
            ADD CONSTRAINT fk_growth_activity_customer_project
            FOREIGN KEY (customer_id, growth_project_id)
            REFERENCES customer(id, growth_project_id) ON DELETE RESTRICT;
    END IF;
    IF NOT EXISTS (
        SELECT 1 FROM pg_constraint
        WHERE conname = 'fk_growth_activity_opportunity_project'
          AND conrelid = 'hasn_growth.activity'::regclass
    ) THEN
        ALTER TABLE activity
            ADD CONSTRAINT fk_growth_activity_opportunity_project
            FOREIGN KEY (opportunity_id, growth_project_id)
            REFERENCES opportunity(id, growth_project_id) ON DELETE RESTRICT;
    END IF;

    IF NOT EXISTS (
        SELECT 1 FROM pg_constraint
        WHERE conname = 'fk_growth_form_project'
          AND conrelid = 'hasn_growth.form_submission'::regclass
    ) THEN
        ALTER TABLE form_submission
            ADD CONSTRAINT fk_growth_form_project
            FOREIGN KEY (growth_project_id)
            REFERENCES growth_project(id) ON DELETE RESTRICT;
    END IF;
    IF NOT EXISTS (
        SELECT 1 FROM pg_constraint
        WHERE conname = 'fk_growth_form_customer_project'
          AND conrelid = 'hasn_growth.form_submission'::regclass
    ) THEN
        ALTER TABLE form_submission
            ADD CONSTRAINT fk_growth_form_customer_project
            FOREIGN KEY (customer_id, growth_project_id)
            REFERENCES customer(id, growth_project_id) ON DELETE RESTRICT;
    END IF;
    IF NOT EXISTS (
        SELECT 1 FROM pg_constraint
        WHERE conname = 'fk_growth_form_private_profile'
          AND conrelid = 'hasn_growth.form_submission'::regclass
    ) THEN
        ALTER TABLE form_submission
            ADD CONSTRAINT fk_growth_form_private_profile
            FOREIGN KEY (contact_private_profile_id)
            REFERENCES contact_private_profile(id) ON DELETE RESTRICT;
    END IF;
    IF NOT EXISTS (
        SELECT 1 FROM pg_constraint
        WHERE conname = 'fk_growth_form_lead_contact'
          AND conrelid = 'hasn_growth.form_submission'::regclass
    ) THEN
        ALTER TABLE form_submission
            ADD CONSTRAINT fk_growth_form_lead_contact
            FOREIGN KEY (lead_contact_id)
            REFERENCES contact(id) ON DELETE RESTRICT;
    END IF;
    IF NOT EXISTS (
        SELECT 1 FROM pg_constraint
        WHERE conname = 'fk_growth_form_project_lead'
          AND conrelid = 'hasn_growth.form_submission'::regclass
    ) THEN
        ALTER TABLE form_submission
            ADD CONSTRAINT fk_growth_form_project_lead
            FOREIGN KEY (project_lead_id)
            REFERENCES growth_project_lead(id) ON DELETE RESTRICT;
    END IF;

    IF NOT EXISTS (
        SELECT 1 FROM pg_constraint
        WHERE conname = 'fk_growth_optout_source_project'
          AND conrelid = 'hasn_growth.optout_record'::regclass
    ) THEN
        ALTER TABLE optout_record
            ADD CONSTRAINT fk_growth_optout_source_project
            FOREIGN KEY (growth_project_id)
            REFERENCES growth_project(id) ON DELETE RESTRICT;
    END IF;
END;
$$;

INSERT INTO playbook_version (
    playbook_id,
    version,
    name,
    goal,
    target_profile,
    cadence,
    tone_guide,
    exit_rule,
    definition_hash,
    created_by_kind
)
SELECT
    p.id,
    COALESCE(p.version, 1),
    p.name,
    p.goal,
    p.target_profile,
    p.cadence,
    p.tone_guide,
    p.exit_rule,
    encode(
        digest(
            concat_ws(
                E'\n',
                p.name,
                COALESCE(p.goal, ''),
                p.target_profile::text,
                p.cadence::text,
                COALESCE(p.tone_guide, ''),
                p.exit_rule::text
            ),
            'sha256'
        ),
        'hex'
    ),
    'migration'
FROM playbook AS p
ON CONFLICT (playbook_id, version) DO NOTHING;
