-- AI 获客项目化 v4：S1 加法数据底座。
-- 本文件只创建新表、新索引和追加式审计门禁；存量表加列见同阶段 migration。
-- 所有业务删除均采用状态归档，外键统一 RESTRICT。

SET search_path TO hasn_growth, public;

CREATE TABLE IF NOT EXISTS growth_project (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    platform_project_id uuid NOT NULL
        REFERENCES hasn_project.hasn_project(id) ON DELETE RESTRICT,
    user_id bigint NOT NULL,
    owner_hasn_id varchar(40) NOT NULL,
    owner_scope varchar(16) NOT NULL DEFAULT 'personal',
    enterprise_id bigint,
    name varchar(200) NOT NULL,
    tagline varchar(500),
    product_profile jsonb NOT NULL DEFAULT '{}'::jsonb,
    icp_profile jsonb NOT NULL DEFAULT '{}'::jsonb,
    profile_version integer NOT NULL DEFAULT 1,
    profile_source_hash varchar(64),
    profile_updated_time timestamptz,
    kb_ref varchar(255),
    landing_site_ref varchar(255),
    owner_agent_id varchar(40),
    status varchar(16) NOT NULL DEFAULT 'draft',
    provision_status varchar(16) NOT NULL DEFAULT 'pending',
    provision_error jsonb,
    monthly_budget numeric(18,2),
    budget_currency varchar(3) NOT NULL DEFAULT 'CNY',
    quiet_hours_start smallint NOT NULL DEFAULT 21,
    quiet_hours_end smallint NOT NULL DEFAULT 9,
    daily_outreach_limit integer NOT NULL DEFAULT 20,
    policy_version integer NOT NULL DEFAULT 1,
    readiness_snapshot jsonb NOT NULL DEFAULT '{}'::jsonb,
    stats_snapshot jsonb NOT NULL DEFAULT '{}'::jsonb,
    created_time timestamptz NOT NULL DEFAULT now(),
    updated_time timestamptz DEFAULT now(),
    CONSTRAINT uq_growth_project_platform_project UNIQUE (platform_project_id),
    CONSTRAINT ck_growth_project_owner_scope CHECK (
        (owner_scope = 'personal' AND enterprise_id IS NULL)
        OR (owner_scope = 'enterprise' AND enterprise_id IS NOT NULL)
    ),
    CONSTRAINT ck_growth_project_status CHECK (
        status IN ('draft', 'active', 'paused', 'archived')
    ),
    CONSTRAINT ck_growth_project_provision_status CHECK (
        provision_status IN ('pending', 'running', 'ready', 'failed')
    ),
    CONSTRAINT ck_growth_project_profile_version CHECK (profile_version >= 1),
    CONSTRAINT ck_growth_project_monthly_budget CHECK (
        monthly_budget IS NULL OR monthly_budget >= 0
    ),
    CONSTRAINT ck_growth_project_quiet_hours CHECK (
        quiet_hours_start BETWEEN 0 AND 23
        AND quiet_hours_end BETWEEN 0 AND 23
        AND quiet_hours_start <> quiet_hours_end
    ),
    CONSTRAINT ck_growth_project_daily_outreach_limit CHECK (
        daily_outreach_limit BETWEEN 1 AND 10000
    ),
    CONSTRAINT ck_growth_project_policy_version CHECK (policy_version >= 1)
);
COMMENT ON TABLE growth_project IS '平台项目唯一挂靠的获客漏斗';
COMMENT ON COLUMN growth_project.platform_project_id IS '平台项目云端权威 UUID，一个平台项目至多一个获客漏斗';
COMMENT ON COLUMN growth_project.owner_hasn_id IS '主人稳定 HASN ID，由服务端从平台项目和鉴权上下文解析';
COMMENT ON COLUMN growth_project.owner_scope IS '归属模式 (personal:个人:blue/enterprise:企业:purple)';
COMMENT ON COLUMN growth_project.status IS '状态 (draft:草稿:gray/active:运行中:green/paused:已暂停:orange/archived:已归档:gray)';
COMMENT ON COLUMN growth_project.provision_status IS '开通状态 (pending:待开始:gray/running:进行中:blue/ready:就绪:green/failed:失败:red)';
COMMENT ON COLUMN growth_project.kb_ref IS '知识库资源引用 hasn://knowledge/kbs/{id}';
COMMENT ON COLUMN growth_project.landing_site_ref IS '站点资源引用 hasn://publish/sites/{id}';
COMMENT ON COLUMN growth_project.quiet_hours_start IS '静默时段开始小时，使用项目时区的 0–23 整点';
COMMENT ON COLUMN growth_project.quiet_hours_end IS '静默时段结束小时，使用项目时区的 0–23 整点';
COMMENT ON COLUMN growth_project.daily_outreach_limit IS '项目每日发送成功或人工发送证明的触达上限';
COMMENT ON COLUMN growth_project.policy_version IS '渠道、静默时段、频控和预算策略版本';

CREATE UNIQUE INDEX IF NOT EXISTS uq_growth_project_kb_ref
    ON growth_project (kb_ref) WHERE kb_ref IS NOT NULL;
CREATE UNIQUE INDEX IF NOT EXISTS uq_growth_project_landing_site_ref
    ON growth_project (landing_site_ref) WHERE landing_site_ref IS NOT NULL;
CREATE INDEX IF NOT EXISTS idx_growth_project_owner_status
    ON growth_project (owner_hasn_id, status, updated_time DESC);

CREATE TABLE IF NOT EXISTS contact_private_profile (
    id bigserial PRIMARY KEY,
    lead_contact_id bigint NOT NULL REFERENCES contact(id) ON DELETE RESTRICT,
    owner_scope varchar(16) NOT NULL,
    user_id bigint,
    enterprise_id bigint,
    contact_name_ciphertext text,
    title_ciphertext text,
    encryption_key_version integer NOT NULL,
    lawful_basis varchar(48) NOT NULL,
    source_ref varchar(255) NOT NULL,
    consent_ref varchar(255),
    retention_until timestamptz NOT NULL,
    status varchar(16) NOT NULL DEFAULT 'active',
    created_time timestamptz NOT NULL DEFAULT now(),
    updated_time timestamptz DEFAULT now(),
    CONSTRAINT ck_growth_private_profile_owner CHECK (
        (owner_scope = 'personal' AND user_id IS NOT NULL AND enterprise_id IS NULL)
        OR (owner_scope = 'enterprise' AND enterprise_id IS NOT NULL)
    ),
    CONSTRAINT ck_growth_private_profile_status CHECK (
        status IN ('active', 'revoked', 'expired')
    ),
    CONSTRAINT ck_growth_private_profile_key_version CHECK (encryption_key_version >= 1)
);
COMMENT ON TABLE contact_private_profile IS 'Owner 或企业对全局联系人的私有资料密文';
COMMENT ON COLUMN contact_private_profile.contact_name_ciphertext IS '联系人姓名应用层密文';
COMMENT ON COLUMN contact_private_profile.title_ciphertext IS '联系人职位应用层密文';
COMMENT ON COLUMN contact_private_profile.lawful_basis IS '本主体取得和使用资料的合法依据';
COMMENT ON COLUMN contact_private_profile.source_ref IS '本主体取得资料的稳定来源引用';
COMMENT ON COLUMN contact_private_profile.retention_until IS '资料允许保留到期时间';

CREATE UNIQUE INDEX IF NOT EXISTS uq_growth_private_profile_personal
    ON contact_private_profile (lead_contact_id, user_id)
    WHERE owner_scope = 'personal';
CREATE UNIQUE INDEX IF NOT EXISTS uq_growth_private_profile_enterprise
    ON contact_private_profile (lead_contact_id, enterprise_id)
    WHERE owner_scope = 'enterprise';
CREATE INDEX IF NOT EXISTS idx_growth_private_profile_retention
    ON contact_private_profile (status, retention_until);

CREATE TABLE IF NOT EXISTS contact_channel (
    id bigserial PRIMARY KEY,
    private_profile_id bigint NOT NULL
        REFERENCES contact_private_profile(id) ON DELETE RESTRICT,
    lead_contact_id bigint NOT NULL REFERENCES contact(id) ON DELETE RESTRICT,
    owner_scope varchar(16) NOT NULL,
    user_id bigint,
    enterprise_id bigint,
    channel varchar(24) NOT NULL,
    value_ciphertext text NOT NULL,
    encryption_key_version integer NOT NULL,
    value_hmac varchar(128) NOT NULL,
    hash_key_version integer NOT NULL,
    lawful_basis varchar(48) NOT NULL,
    source_ref varchar(255) NOT NULL,
    consent_ref varchar(255),
    verified_at timestamptz,
    fresh_until timestamptz,
    retention_until timestamptz NOT NULL,
    status varchar(16) NOT NULL DEFAULT 'active',
    created_time timestamptz NOT NULL DEFAULT now(),
    updated_time timestamptz DEFAULT now(),
    CONSTRAINT ck_growth_contact_channel_owner CHECK (
        (owner_scope = 'personal' AND user_id IS NOT NULL AND enterprise_id IS NULL)
        OR (owner_scope = 'enterprise' AND enterprise_id IS NOT NULL)
    ),
    CONSTRAINT ck_growth_contact_channel_status CHECK (
        status IN ('active', 'revoked', 'expired')
    ),
    CONSTRAINT ck_growth_contact_channel_key_versions CHECK (
        encryption_key_version >= 1 AND hash_key_version >= 1
    )
);
COMMENT ON TABLE contact_channel IS 'Owner 或企业授权持有的联系方式密文与版本化 HMAC';
COMMENT ON COLUMN contact_channel.value_ciphertext IS '联系方式应用层密文，禁止进入 Agent、日志和 daemon 缓存';
COMMENT ON COLUMN contact_channel.value_hmac IS '使用独立服务端 HMAC 密钥计算的渠道匹配值';
COMMENT ON COLUMN contact_channel.hash_key_version IS 'HMAC 密钥版本，轮换期支持多版本匹配';

CREATE UNIQUE INDEX IF NOT EXISTS uq_growth_contact_channel_personal
    ON contact_channel (user_id, channel, value_hmac, hash_key_version)
    WHERE owner_scope = 'personal';
CREATE UNIQUE INDEX IF NOT EXISTS uq_growth_contact_channel_enterprise
    ON contact_channel (enterprise_id, channel, value_hmac, hash_key_version)
    WHERE owner_scope = 'enterprise';
CREATE INDEX IF NOT EXISTS idx_growth_contact_channel_profile
    ON contact_channel (private_profile_id, status);
CREATE INDEX IF NOT EXISTS idx_growth_contact_channel_retention
    ON contact_channel (status, retention_until);

CREATE TABLE IF NOT EXISTS playbook_version (
    id bigserial PRIMARY KEY,
    playbook_id bigint NOT NULL REFERENCES playbook(id) ON DELETE RESTRICT,
    version integer NOT NULL,
    name varchar(200) NOT NULL,
    goal text,
    target_profile jsonb NOT NULL DEFAULT '{}'::jsonb,
    cadence jsonb NOT NULL DEFAULT '[]'::jsonb,
    tone_guide text,
    exit_rule jsonb NOT NULL DEFAULT '{}'::jsonb,
    definition_hash varchar(64) NOT NULL,
    created_by_kind varchar(16) NOT NULL DEFAULT 'system',
    created_by_id varchar(64),
    created_time timestamptz NOT NULL DEFAULT now(),
    updated_time timestamptz DEFAULT now(),
    CONSTRAINT uq_growth_playbook_version UNIQUE (playbook_id, version),
    CONSTRAINT ck_growth_playbook_version_positive CHECK (version >= 1),
    CONSTRAINT ck_growth_playbook_version_actor CHECK (
        created_by_kind IN ('owner', 'agent', 'system', 'migration')
    )
);
COMMENT ON TABLE playbook_version IS '获客打法不可变版本快照，历史执行只读取本表';
COMMENT ON COLUMN playbook_version.definition_hash IS '规范化打法定义 SHA256，用于版本幂等与审计';

CREATE TABLE IF NOT EXISTS growth_project_playbook (
    id bigserial PRIMARY KEY,
    growth_project_id uuid NOT NULL REFERENCES growth_project(id) ON DELETE RESTRICT,
    playbook_id bigint NOT NULL REFERENCES playbook(id) ON DELETE RESTRICT,
    playbook_version integer NOT NULL,
    status varchar(16) NOT NULL DEFAULT 'active',
    configuration_snapshot jsonb NOT NULL DEFAULT '{}'::jsonb,
    created_time timestamptz NOT NULL DEFAULT now(),
    updated_time timestamptz DEFAULT now(),
    CONSTRAINT uq_growth_project_playbook_version
        UNIQUE (growth_project_id, playbook_id, playbook_version),
    CONSTRAINT fk_growth_project_playbook_version
        FOREIGN KEY (playbook_id, playbook_version)
        REFERENCES playbook_version(playbook_id, version) ON DELETE RESTRICT,
    CONSTRAINT ck_growth_project_playbook_status CHECK (
        status IN ('active', 'paused', 'retired')
    )
);
COMMENT ON TABLE growth_project_playbook IS '获客漏斗采用的打法版本与项目级配置快照';
CREATE INDEX IF NOT EXISTS idx_growth_project_playbook_status
    ON growth_project_playbook (growth_project_id, status, updated_time DESC);

CREATE TABLE IF NOT EXISTS growth_project_lead (
    id bigserial PRIMARY KEY,
    growth_project_id uuid NOT NULL REFERENCES growth_project(id) ON DELETE RESTRICT,
    lead_contact_id bigint NOT NULL REFERENCES contact(id) ON DELETE RESTRICT,
    user_id bigint NOT NULL,
    owner_scope varchar(16) NOT NULL DEFAULT 'personal',
    enterprise_id bigint,
    assignee varchar(40),
    source_kind varchar(32),
    source_tool varchar(64),
    source_ref varchar(255),
    source_meta jsonb NOT NULL DEFAULT '{}'::jsonb,
    status varchar(16) NOT NULL DEFAULT 'new',
    dismiss_reason text,
    note text,
    match_score numeric(5,2),
    score_breakdown jsonb NOT NULL DEFAULT '{}'::jsonb,
    scoring_version varchar(64),
    evidence_fresh_at timestamptz,
    acquired_at timestamptz NOT NULL DEFAULT now(),
    created_time timestamptz NOT NULL DEFAULT now(),
    updated_time timestamptz DEFAULT now(),
    CONSTRAINT uq_growth_project_lead_contact
        UNIQUE (growth_project_id, lead_contact_id),
    CONSTRAINT ck_growth_project_lead_owner CHECK (
        (owner_scope = 'personal' AND enterprise_id IS NULL)
        OR (owner_scope = 'enterprise' AND enterprise_id IS NOT NULL)
    ),
    CONSTRAINT ck_growth_project_lead_status CHECK (
        status IN ('new', 'qualified', 'dismissed')
    ),
    CONSTRAINT ck_growth_project_lead_match_score CHECK (
        match_score IS NULL OR (match_score >= 0 AND match_score <= 100)
    )
);
COMMENT ON TABLE growth_project_lead IS '获客漏斗对全局联系人事实的项目级引用';
CREATE INDEX IF NOT EXISTS idx_growth_project_lead_status_score
    ON growth_project_lead (growth_project_id, status, match_score DESC);
CREATE INDEX IF NOT EXISTS idx_growth_project_lead_assignee
    ON growth_project_lead (growth_project_id, assignee, updated_time DESC);

CREATE TABLE IF NOT EXISTS growth_project_provision (
    id bigserial PRIMARY KEY,
    growth_project_id uuid NOT NULL REFERENCES growth_project(id) ON DELETE RESTRICT,
    command_id varchar(64) NOT NULL,
    idempotency_key varchar(200) NOT NULL,
    step varchar(48) NOT NULL,
    status varchar(16) NOT NULL DEFAULT 'pending',
    attempts integer NOT NULL DEFAULT 0,
    next_retry_time timestamptz,
    last_error jsonb,
    started_time timestamptz,
    finished_time timestamptz,
    created_time timestamptz NOT NULL DEFAULT now(),
    updated_time timestamptz DEFAULT now(),
    CONSTRAINT uq_growth_project_provision_command_step UNIQUE (command_id, step),
    CONSTRAINT uq_growth_project_provision_idempotency_step UNIQUE (idempotency_key, step),
    CONSTRAINT uq_growth_project_provision_project_step UNIQUE (growth_project_id, step),
    CONSTRAINT ck_growth_project_provision_status CHECK (
        status IN ('pending', 'running', 'success', 'failed')
    ),
    CONSTRAINT ck_growth_project_provision_attempts CHECK (attempts >= 0)
);
COMMENT ON TABLE growth_project_provision IS '建漏斗、建库、挂靠和建站步骤的可靠编排状态';
CREATE INDEX IF NOT EXISTS idx_growth_project_provision_retry
    ON growth_project_provision (status, next_retry_time);

CREATE TABLE IF NOT EXISTS outreach_message_event (
    id bigserial PRIMARY KEY,
    growth_project_id uuid NOT NULL REFERENCES growth_project(id) ON DELETE RESTRICT,
    outreach_message_id bigint NOT NULL REFERENCES outreach_message(id) ON DELETE RESTRICT,
    event_type varchar(32) NOT NULL,
    idempotency_key varchar(200) NOT NULL,
    occurred_time timestamptz NOT NULL DEFAULT now(),
    actor_kind varchar(16) NOT NULL,
    actor_id varchar(64),
    approval_status varchar(24),
    delivery_status varchar(32),
    approval_version integer,
    content_version integer,
    error_class varchar(64),
    metadata jsonb NOT NULL DEFAULT '{}'::jsonb,
    created_time timestamptz NOT NULL DEFAULT now(),
    updated_time timestamptz DEFAULT now(),
    CONSTRAINT uq_growth_outreach_event_idempotency
        UNIQUE (outreach_message_id, idempotency_key),
    CONSTRAINT ck_growth_outreach_event_type CHECK (
        event_type IN (
            'drafted', 'approval_requested', 'approved', 'rejected', 'cancelled',
            'queued', 'sending', 'sent', 'delivered', 'failed',
            'blocked_optout', 'blocked_compliance', 'manual_attested', 'replied', 'retry_scheduled'
        )
    ),
    CONSTRAINT ck_growth_outreach_event_actor CHECK (
        actor_kind IN ('owner', 'agent', 'system', 'provider')
    )
);
COMMENT ON TABLE outreach_message_event IS '触达审批、投递、拦截、人工证明和回复的追加式事件';
CREATE INDEX IF NOT EXISTS idx_growth_outreach_event_message_time
    ON outreach_message_event (outreach_message_id, occurred_time DESC);
CREATE INDEX IF NOT EXISTS idx_growth_outreach_event_project_time
    ON outreach_message_event (growth_project_id, occurred_time DESC);

CREATE TABLE IF NOT EXISTS growth_attribution_event (
    id bigserial PRIMARY KEY,
    growth_project_id uuid NOT NULL REFERENCES growth_project(id) ON DELETE RESTRICT,
    event_type varchar(32) NOT NULL,
    lead_contact_id bigint REFERENCES contact(id) ON DELETE RESTRICT,
    customer_id bigint REFERENCES customer(id) ON DELETE RESTRICT,
    opportunity_id bigint REFERENCES opportunity(id) ON DELETE RESTRICT,
    growth_project_playbook_id bigint
        REFERENCES growth_project_playbook(id) ON DELETE RESTRICT,
    playbook_id bigint,
    playbook_version integer,
    source_kind varchar(32),
    source_ref varchar(255),
    campaign_ref varchar(255),
    playbook_ref varchar(255),
    amount numeric(18,2),
    currency varchar(3),
    occurred_time timestamptz NOT NULL,
    idempotency_key varchar(200) NOT NULL,
    metadata jsonb NOT NULL DEFAULT '{}'::jsonb,
    created_time timestamptz NOT NULL DEFAULT now(),
    updated_time timestamptz DEFAULT now(),
    CONSTRAINT uq_growth_attribution_idempotency
        UNIQUE (growth_project_id, idempotency_key),
    CONSTRAINT ck_growth_attribution_event_type CHECK (
        event_type IN (
            'lead_acquired', 'inbound', 'qualified', 'outreach_sent', 'replied',
            'opportunity', 'closed_won', 'closed_lost', 'cost'
        )
    ),
    CONSTRAINT ck_growth_attribution_amount CHECK (
        amount IS NULL OR amount >= 0
    ),
    CONSTRAINT ck_growth_attribution_playbook_version CHECK (
        playbook_version IS NULL OR playbook_version >= 1
    )
);
COMMENT ON TABLE growth_attribution_event IS '可按漏斗重算转化、收入、成本和赢输分布的追加式归因事实';
CREATE INDEX IF NOT EXISTS idx_growth_attribution_project_time
    ON growth_attribution_event (growth_project_id, occurred_time DESC);
CREATE INDEX IF NOT EXISTS idx_growth_attribution_project_type
    ON growth_attribution_event (growth_project_id, event_type, occurred_time DESC);

CREATE TABLE IF NOT EXISTS growth_pii_migration_quarantine (
    id bigserial PRIMARY KEY,
    source_table varchar(64) NOT NULL,
    source_record_id varchar(64) NOT NULL,
    reason_code varchar(64) NOT NULL,
    owner_scope_hint varchar(16),
    user_id_hint bigint,
    enterprise_id_hint bigint,
    field_names jsonb NOT NULL DEFAULT '[]'::jsonb,
    pii_fingerprint varchar(128),
    status varchar(16) NOT NULL DEFAULT 'pending',
    resolution_note text,
    resolved_by varchar(64),
    resolved_time timestamptz,
    created_time timestamptz NOT NULL DEFAULT now(),
    updated_time timestamptz DEFAULT now(),
    CONSTRAINT uq_growth_pii_quarantine_source
        UNIQUE (source_table, source_record_id, reason_code),
    CONSTRAINT ck_growth_pii_quarantine_status CHECK (
        status IN ('pending', 'resolved', 'discarded')
    )
);
COMMENT ON TABLE growth_pii_migration_quarantine IS '无法证明授权主体或合法依据的 PII 迁移隔离清单，不保存明文';
COMMENT ON COLUMN growth_pii_migration_quarantine.field_names IS '仅记录出现 PII 的字段名，不记录原值';
COMMENT ON COLUMN growth_pii_migration_quarantine.pii_fingerprint IS '用于重跑去重的带密钥指纹，不是明文或无盐哈希';
CREATE INDEX IF NOT EXISTS idx_growth_pii_quarantine_status
    ON growth_pii_migration_quarantine (status, created_time);

CREATE TABLE IF NOT EXISTS contact_private_access_audit (
    id bigserial PRIMARY KEY,
    owner_scope varchar(16) NOT NULL,
    user_id bigint,
    enterprise_id bigint,
    actor_type varchar(16) NOT NULL,
    actor_id varchar(64) NOT NULL,
    action varchar(24) NOT NULL,
    resource_type varchar(32) NOT NULL,
    resource_id varchar(64) NOT NULL,
    purpose varchar(128),
    trace_id varchar(128),
    result varchar(16) NOT NULL,
    denial_code varchar(64),
    request_metadata jsonb NOT NULL DEFAULT '{}'::jsonb,
    created_time timestamptz NOT NULL DEFAULT now(),
    updated_time timestamptz DEFAULT now(),
    CONSTRAINT ck_growth_private_audit_owner CHECK (
        (owner_scope = 'personal' AND user_id IS NOT NULL AND enterprise_id IS NULL)
        OR (owner_scope = 'enterprise' AND enterprise_id IS NOT NULL)
    ),
    CONSTRAINT ck_growth_private_audit_actor CHECK (
        actor_type IN ('owner', 'agent', 'system', 'admin')
    ),
    CONSTRAINT ck_growth_private_audit_result CHECK (
        result IN ('allowed', 'denied', 'error')
    )
);
COMMENT ON TABLE contact_private_access_audit IS '联系人私有资料访问的数据库追加式防篡改审计';
COMMENT ON COLUMN contact_private_access_audit.request_metadata IS '只允许脱敏请求元数据，禁止联系方式、密文和令牌';
CREATE INDEX IF NOT EXISTS idx_growth_private_access_owner_time
    ON contact_private_access_audit (owner_scope, user_id, enterprise_id, created_time DESC);
CREATE INDEX IF NOT EXISTS idx_growth_private_access_resource_time
    ON contact_private_access_audit (resource_type, resource_id, created_time DESC);

CREATE OR REPLACE FUNCTION hasn_growth.reject_contact_private_access_audit_mutation()
RETURNS trigger
LANGUAGE plpgsql
AS $$
BEGIN
    RAISE EXCEPTION 'contact_private_access_audit is append-only'
        USING ERRCODE = '55000';
END;
$$;
DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1
        FROM pg_trigger
        WHERE tgname = 'trg_growth_private_access_audit_append_only'
          AND tgrelid = 'hasn_growth.contact_private_access_audit'::regclass
    ) THEN
        CREATE TRIGGER trg_growth_private_access_audit_append_only
        BEFORE UPDATE OR DELETE ON hasn_growth.contact_private_access_audit
        FOR EACH ROW
        EXECUTE FUNCTION hasn_growth.reject_contact_private_access_audit_mutation();
    END IF;
END;
$$;
