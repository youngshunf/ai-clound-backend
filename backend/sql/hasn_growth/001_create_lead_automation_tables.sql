CREATE TABLE IF NOT EXISTS lead_source_config (
    id bigserial PRIMARY KEY,
    source_type varchar(32) NOT NULL,
    name varchar(100) NOT NULL,
    enabled boolean NOT NULL DEFAULT true,
    firecrawl_options jsonb NOT NULL DEFAULT '{}'::jsonb,
    min_contact_fields jsonb NOT NULL DEFAULT '["email","phone"]'::jsonb,
    persist_raw_html boolean NOT NULL DEFAULT false,
    max_html_bytes int NOT NULL DEFAULT 524288,
    domain_blacklist jsonb NOT NULL DEFAULT '[]'::jsonb,
    country_blacklist jsonb NOT NULL DEFAULT '["DE","FR","IT","NL","ES"]'::jsonb,
    rate_limit_per_minute int NOT NULL DEFAULT 60,
    concurrency int NOT NULL DEFAULT 3,
    metadata jsonb NOT NULL DEFAULT '{}'::jsonb,
    created_time timestamptz NOT NULL DEFAULT now(),
    updated_time timestamptz NOT NULL DEFAULT now(),
    CONSTRAINT uq_lead_source_config_type_name UNIQUE (source_type, name)
);
COMMENT ON TABLE lead_source_config IS 'AI lead automation source configuration';

CREATE TABLE IF NOT EXISTS lead_collection_job (
    id bigserial PRIMARY KEY,
    job_no varchar(40) NOT NULL UNIQUE,
    keyword varchar(200) NOT NULL,
    source_types jsonb NOT NULL DEFAULT '[]'::jsonb,
    lead_scope varchar(16) NOT NULL,
    user_id bigint,
    status varchar(24) NOT NULL DEFAULT 'pending',
    max_pages int NOT NULL DEFAULT 5,
    max_results int NOT NULL DEFAULT 100,
    request_config jsonb NOT NULL DEFAULT '{}'::jsonb,
    total_found int NOT NULL DEFAULT 0,
    raw_count int NOT NULL DEFAULT 0,
    valid_count int NOT NULL DEFAULT 0,
    invalid_count int NOT NULL DEFAULT 0,
    duplicate_count int NOT NULL DEFAULT 0,
    firecrawl_success_count int NOT NULL DEFAULT 0,
    firecrawl_failed_count int NOT NULL DEFAULT 0,
    started_at timestamptz,
    finished_at timestamptz,
    error_message text,
    metadata jsonb NOT NULL DEFAULT '{}'::jsonb,
    created_time timestamptz NOT NULL DEFAULT now(),
    updated_time timestamptz NOT NULL DEFAULT now(),
    CONSTRAINT ck_lead_collection_job_scope CHECK (
        (lead_scope = 'public' AND user_id IS NULL)
        OR (lead_scope = 'user' AND user_id IS NOT NULL)
    )
);
COMMENT ON TABLE lead_collection_job IS 'AI lead automation collection job';

CREATE TABLE IF NOT EXISTS lead_firecrawl_request (
    id bigserial PRIMARY KEY,
    job_id bigint NOT NULL REFERENCES lead_collection_job(id),
    source_config_id bigint REFERENCES lead_source_config(id),
    source_type varchar(32) NOT NULL,
    endpoint varchar(64) NOT NULL,
    target_url varchar(2048),
    query varchar(500),
    request_payload jsonb NOT NULL DEFAULT '{}'::jsonb,
    extract_mode varchar(32) NOT NULL,
    llm_schema_version varchar(64),
    llm_prompt_version varchar(64),
    response_status int,
    status varchar(16) NOT NULL DEFAULT 'pending',
    attempt_count int NOT NULL DEFAULT 1,
    duration_ms int,
    result_count int,
    error_message text,
    response_excerpt varchar(4096),
    metadata jsonb NOT NULL DEFAULT '{}'::jsonb,
    created_time timestamptz NOT NULL DEFAULT now(),
    updated_time timestamptz NOT NULL DEFAULT now()
);
COMMENT ON TABLE lead_firecrawl_request IS 'Firecrawl request audit for AI lead automation';

CREATE TABLE IF NOT EXISTS lead_raw_record (
    id bigserial PRIMARY KEY,
    job_id bigint NOT NULL REFERENCES lead_collection_job(id),
    source_config_id bigint REFERENCES lead_source_config(id),
    firecrawl_request_id bigint REFERENCES lead_firecrawl_request(id),
    source_type varchar(32) NOT NULL,
    source_url varchar(2048),
    domain varchar(255),
    title varchar(500),
    markdown text,
    raw_text text,
    raw_html text,
    raw_payload jsonb,
    structured_payload jsonb,
    llm_confidence numeric(5,2),
    system_score numeric(5,2),
    content_hash varchar(64) NOT NULL,
    normalization_version varchar(32) NOT NULL DEFAULT 'v1',
    status varchar(16) NOT NULL DEFAULT 'pending',
    error_message text,
    metadata jsonb NOT NULL DEFAULT '{}'::jsonb,
    created_time timestamptz NOT NULL DEFAULT now(),
    updated_time timestamptz NOT NULL DEFAULT now(),
    CONSTRAINT uq_lead_raw_record_job_hash UNIQUE (job_id, content_hash)
);
COMMENT ON TABLE lead_raw_record IS 'Raw crawled lead page record';

CREATE TABLE IF NOT EXISTS lead_contact (
    id bigserial PRIMARY KEY,
    lead_no varchar(40) NOT NULL UNIQUE,
    lead_scope varchar(16) NOT NULL,
    user_id bigint,
    company_name varchar(255),
    contact_name varchar(100),
    email varchar(255),
    email_normalized varchar(255),
    phone varchar(50),
    phone_normalized varchar(20),
    website varchar(500),
    domain varchar(255),
    country varchar(8),
    region varchar(100),
    city varchar(100),
    address varchar(500),
    industry varchar(100),
    source_type varchar(32),
    source_url varchar(2048),
    keyword varchar(200),
    status varchar(16) NOT NULL DEFAULT 'new',
    confidence_score numeric(5,2) NOT NULL DEFAULT 0,
    dedupe_key_email varchar(64),
    dedupe_key_phone varchar(64),
    dedupe_key_domain varchar(64),
    normalization_version varchar(32) NOT NULL DEFAULT 'v1',
    first_seen_at timestamptz NOT NULL DEFAULT now(),
    last_seen_at timestamptz NOT NULL DEFAULT now(),
    last_exported_at timestamptz,
    archived_at timestamptz NOT NULL DEFAULT (now() + interval '18 months'),
    metadata jsonb NOT NULL DEFAULT '{}'::jsonb,
    created_time timestamptz NOT NULL DEFAULT now(),
    updated_time timestamptz NOT NULL DEFAULT now(),
    CONSTRAINT ck_lead_contact_scope CHECK (
        (lead_scope = 'public' AND user_id IS NULL)
        OR (lead_scope = 'user' AND user_id IS NOT NULL)
    )
);
COMMENT ON TABLE lead_contact IS 'Valid deduplicated lead contact';

CREATE TABLE IF NOT EXISTS lead_contact_source (
    id bigserial PRIMARY KEY,
    lead_contact_id bigint NOT NULL REFERENCES lead_contact(id),
    raw_record_id bigint REFERENCES lead_raw_record(id),
    firecrawl_request_id bigint REFERENCES lead_firecrawl_request(id),
    source_type varchar(32) NOT NULL,
    source_url varchar(2048),
    match_dimension varchar(16) NOT NULL,
    seen_at timestamptz NOT NULL DEFAULT now(),
    metadata jsonb NOT NULL DEFAULT '{}'::jsonb,
    created_time timestamptz NOT NULL DEFAULT now(),
    CONSTRAINT uq_lead_contact_source UNIQUE (lead_contact_id, source_url, match_dimension)
);
COMMENT ON TABLE lead_contact_source IS 'Lead multi-source evidence';

CREATE TABLE IF NOT EXISTS lead_rejected_record (
    id bigserial PRIMARY KEY,
    job_id bigint NOT NULL REFERENCES lead_collection_job(id),
    raw_record_id bigint REFERENCES lead_raw_record(id),
    firecrawl_request_id bigint REFERENCES lead_firecrawl_request(id),
    source_type varchar(32),
    source_url varchar(2048),
    reason varchar(32) NOT NULL,
    email varchar(255),
    phone varchar(50),
    raw_excerpt varchar(4096),
    duplicate_contact_id bigint REFERENCES lead_contact(id),
    error_message text,
    metadata jsonb NOT NULL DEFAULT '{}'::jsonb,
    created_time timestamptz NOT NULL DEFAULT now()
);
COMMENT ON TABLE lead_rejected_record IS 'Rejected, invalid, duplicate, or failed lead record';

CREATE TABLE IF NOT EXISTS lead_export_batch (
    id bigserial PRIMARY KEY,
    batch_no varchar(40) NOT NULL UNIQUE,
    user_id bigint NOT NULL,
    lead_scope varchar(16) NOT NULL,
    filter_payload jsonb NOT NULL DEFAULT '{}'::jsonb,
    format varchar(16) NOT NULL DEFAULT 'csv',
    total_count int NOT NULL DEFAULT 0,
    file_path varchar(500),
    file_sha256 varchar(64),
    status varchar(16) NOT NULL DEFAULT 'pending',
    error_message text,
    started_at timestamptz,
    finished_at timestamptz,
    created_time timestamptz NOT NULL DEFAULT now()
);
COMMENT ON TABLE lead_export_batch IS 'Lead CSV export batch';

CREATE TABLE IF NOT EXISTS lead_export_item (
    id bigserial PRIMARY KEY,
    batch_id bigint NOT NULL REFERENCES lead_export_batch(id) ON DELETE CASCADE,
    lead_contact_id bigint NOT NULL REFERENCES lead_contact(id),
    lead_no varchar(40) NOT NULL,
    snapshot jsonb NOT NULL DEFAULT '{}'::jsonb,
    created_time timestamptz NOT NULL DEFAULT now(),
    CONSTRAINT uq_lead_export_item UNIQUE (batch_id, lead_contact_id)
);
COMMENT ON TABLE lead_export_item IS 'Lead CSV export item snapshot';

CREATE TABLE IF NOT EXISTS lead_audit_log (
    id bigserial PRIMARY KEY,
    event_type varchar(32) NOT NULL,
    actor_user_id bigint,
    actor_role varchar(32),
    actor_ip varchar(64),
    actor_ua varchar(500),
    target_table varchar(64),
    target_count int NOT NULL DEFAULT 0,
    target_ref varchar(64),
    payload jsonb NOT NULL DEFAULT '{}'::jsonb,
    result varchar(16) NOT NULL DEFAULT 'success',
    error_message text,
    created_time timestamptz NOT NULL DEFAULT now()
);
COMMENT ON TABLE lead_audit_log IS 'Lead automation PII and compliance audit log';

CREATE INDEX IF NOT EXISTS idx_lead_collection_job_user_status ON lead_collection_job (user_id, status);
CREATE INDEX IF NOT EXISTS idx_lead_collection_job_status_created ON lead_collection_job (status, created_time DESC);
CREATE INDEX IF NOT EXISTS idx_lead_collection_job_scope_status ON lead_collection_job (lead_scope, status);
CREATE INDEX IF NOT EXISTS idx_lead_firecrawl_request_job_status ON lead_firecrawl_request (job_id, status);
CREATE INDEX IF NOT EXISTS idx_lead_firecrawl_request_source_status ON lead_firecrawl_request (source_type, status);
CREATE INDEX IF NOT EXISTS idx_lead_raw_record_domain ON lead_raw_record (domain);
CREATE INDEX IF NOT EXISTS idx_lead_raw_record_status ON lead_raw_record (status);
CREATE UNIQUE INDEX IF NOT EXISTS uq_lead_contact_email ON lead_contact (dedupe_key_email) WHERE dedupe_key_email IS NOT NULL;
CREATE UNIQUE INDEX IF NOT EXISTS uq_lead_contact_phone ON lead_contact (dedupe_key_phone) WHERE dedupe_key_phone IS NOT NULL;
CREATE UNIQUE INDEX IF NOT EXISTS uq_lead_contact_domain ON lead_contact (dedupe_key_domain) WHERE dedupe_key_domain IS NOT NULL;
CREATE INDEX IF NOT EXISTS idx_lead_contact_email_normalized ON lead_contact (email_normalized);
CREATE INDEX IF NOT EXISTS idx_lead_contact_phone_normalized ON lead_contact (phone_normalized);
CREATE INDEX IF NOT EXISTS idx_lead_contact_domain ON lead_contact (domain);
CREATE INDEX IF NOT EXISTS idx_lead_contact_scope_user ON lead_contact (lead_scope, user_id);
CREATE INDEX IF NOT EXISTS idx_lead_contact_keyword ON lead_contact (keyword);
CREATE INDEX IF NOT EXISTS idx_lead_contact_last_seen ON lead_contact (last_seen_at DESC);
CREATE INDEX IF NOT EXISTS idx_lead_contact_archived_at ON lead_contact (archived_at);
CREATE INDEX IF NOT EXISTS idx_lead_rejected_job_reason ON lead_rejected_record (job_id, reason);
CREATE INDEX IF NOT EXISTS idx_lead_export_batch_user_created ON lead_export_batch (user_id, created_time DESC);
CREATE INDEX IF NOT EXISTS idx_lead_audit_log_event_type ON lead_audit_log (event_type);
CREATE INDEX IF NOT EXISTS idx_lead_audit_log_actor ON lead_audit_log (actor_user_id, created_time DESC);
CREATE INDEX IF NOT EXISTS idx_lead_audit_log_created ON lead_audit_log (created_time DESC);
