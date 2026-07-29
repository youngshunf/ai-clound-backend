-- S6：为全局联系人公共事实与项目导入批次增加稳定去重键。
-- 存量同域名可能已有重复行，只给每个域名最早一行建立权威键；其余行保留待后续人工归并，
-- 避免迁移阶段静默合并不同业务对象。

SET search_path TO hasn_growth, public;

ALTER TABLE contact
    ADD COLUMN IF NOT EXISTS fact_dedupe_key varchar(64);

ALTER TABLE growth_project_lead
    ADD COLUMN IF NOT EXISTS ingest_batch_id varchar(64),
    ADD COLUMN IF NOT EXISTS ingest_client_ref varchar(64);

WITH ranked_domain AS (
    SELECT
        id,
        encode(digest('domain:' || lower(btrim(domain)), 'sha256'), 'hex') AS fact_key,
        row_number() OVER (
            PARTITION BY lower(btrim(domain))
            ORDER BY id
        ) AS row_number
    FROM contact
    WHERE pool_visibility = 'public'
      AND domain IS NOT NULL
      AND btrim(domain) <> ''
)
UPDATE contact AS target
SET fact_dedupe_key = ranked_domain.fact_key
FROM ranked_domain
WHERE target.id = ranked_domain.id
  AND ranked_domain.row_number = 1
  AND target.fact_dedupe_key IS NULL;

CREATE UNIQUE INDEX IF NOT EXISTS uq_growth_contact_public_fact
    ON contact (fact_dedupe_key)
    WHERE pool_visibility = 'public' AND fact_dedupe_key IS NOT NULL;

UPDATE growth_project_lead
SET
    ingest_batch_id = source_meta -> '_ingest' ->> 'batch_id',
    ingest_client_ref = source_meta -> '_ingest' ->> 'client_ref'
WHERE ingest_batch_id IS NULL
  AND ingest_client_ref IS NULL
  AND jsonb_typeof(source_meta -> '_ingest') = 'object';

CREATE UNIQUE INDEX IF NOT EXISTS uq_growth_project_lead_ingest_item
    ON growth_project_lead (growth_project_id, ingest_batch_id, ingest_client_ref)
    WHERE ingest_batch_id IS NOT NULL AND ingest_client_ref IS NOT NULL;

COMMENT ON COLUMN contact.fact_dedupe_key
    IS '不含 PII 的规范化企业域名或企业名称地域事实 SHA256；仅用于全局公共事实去重';

COMMENT ON COLUMN growth_project_lead.ingest_batch_id
    IS '稳定导入批次 ID，与项目和 client_ref 共同保证并发重放幂等';

COMMENT ON COLUMN growth_project_lead.ingest_client_ref
    IS '调用方批次内稳定行标识，不含联系人 PII';
