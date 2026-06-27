-- 统一线索池重构（2026-06-27 福仔「统一的线索池，用户只是引用线索池中的线索」）
-- 把 contact 从「公共/用户混存」改为「纯公共线索池」：每条线索全局一份，pool_visibility 区分公共可匹配/私有；
-- 用户对线索的拥有与状态全部下沉到新 lead_ref 引用表。本迁移只「加列 + 建表 + 迁数据」，
-- 不 drop contact.lead_scope/user_id（见同目录 2026-06-27-drop-contact-scope-columns.sql，待 service 切到新模型后执行）。
-- 全程幂等可重跑。PostgreSQL 语法，落 schema hasn_growth。

SET search_path TO hasn_growth, public;

CREATE EXTENSION IF NOT EXISTS pgcrypto;

-- 1) 用户↔线索引用表（与 006_create_lead_ref_table.sql 一致·自包含便于 prod 迁移）
CREATE TABLE IF NOT EXISTS lead_ref (
    id bigserial PRIMARY KEY,
    user_id bigint NOT NULL,
    lead_contact_id bigint NOT NULL,
    source varchar(16) NOT NULL DEFAULT 'request',
    status varchar(16) NOT NULL DEFAULT 'new',
    dismiss_reason varchar(255),
    note text,
    acquired_at timestamptz NOT NULL DEFAULT now(),
    created_time timestamptz NOT NULL DEFAULT now(),
    updated_time timestamptz DEFAULT now(),
    CONSTRAINT uq_growth_lead_ref_user_lead UNIQUE (user_id, lead_contact_id)
);
CREATE INDEX IF NOT EXISTS ix_growth_lead_ref_user ON lead_ref (user_id);
CREATE INDEX IF NOT EXISTS ix_growth_lead_ref_contact ON lead_ref (lead_contact_id);
COMMENT ON TABLE lead_ref IS '用户↔线索引用表（统一线索池：用户引用池中线索·用户级状态落本表不污染池行）';
COMMENT ON COLUMN lead_ref.source IS '来源 (request:请求匹配:blue/manual:手动登记:cyan/collect:分身采集:green/backfill:缺口补爬:orange)';
COMMENT ON COLUMN lead_ref.status IS '状态 (new:新线索:blue/qualified:已晋级:green/dismissed:已忽略:gray)';

-- 2) contact 加池可见性列（默认公共池）
ALTER TABLE contact ADD COLUMN IF NOT EXISTS pool_visibility varchar(16) NOT NULL DEFAULT 'public';
COMMENT ON COLUMN contact.pool_visibility IS '池可见性 (public:公共池:green/private:私有:gray)';

-- 2.5) 删旧 scope 约束（编码已废弃的「public+user_id NULL / user+user_id NOT NULL」scope 模型）。
--      统一池后新建池行不再设 lead_scope（默认 ''），会违反旧 CHECK；过渡列本身待 drop-columns 迁移删除。
ALTER TABLE contact DROP CONSTRAINT IF EXISTS ck_lead_contact_scope;
ALTER TABLE collection_job DROP CONSTRAINT IF EXISTS ck_lead_collection_job_scope;

-- 3) 重算全局 dedupe_key（去 lead_scope/user_id，与 service 切到的全局键同源：sha256(规整值)）
--    旧值含 scope+user 复合，统一池后必须按全局值去重，否则同一线索跨用户重复入池。
UPDATE contact SET dedupe_key_email = encode(digest(email_normalized, 'sha256'), 'hex')
  WHERE email_normalized IS NOT NULL AND email_normalized <> '';
UPDATE contact SET dedupe_key_phone = encode(digest(phone_normalized, 'sha256'), 'hex')
  WHERE phone_normalized IS NOT NULL AND phone_normalized <> '';
UPDATE contact SET dedupe_key_domain = encode(digest(domain, 'sha256'), 'hex')
  WHERE domain IS NOT NULL AND domain <> '';

-- 4) 存量 user-scope 行（user_id 非空）→ 标私有池 + 建 owner 引用（保隐私：原私有线索迁为 private，owner 经 ref 仍可见）
INSERT INTO lead_ref (user_id, lead_contact_id, source, status, acquired_at, created_time, updated_time)
SELECT c.user_id, c.id,
       CASE WHEN c.source_type = 'manual' THEN 'manual' ELSE 'collect' END,
       CASE WHEN c.status = 'qualified' THEN 'qualified'
            WHEN c.status = 'rejected' THEN 'dismissed'
            ELSE 'new' END,
       COALESCE(c.created_time, now()), now(), now()
FROM contact c
WHERE c.user_id IS NOT NULL
ON CONFLICT (user_id, lead_contact_id) DO NOTHING;

UPDATE contact SET pool_visibility = 'private' WHERE user_id IS NOT NULL AND pool_visibility <> 'private';

-- 5) 清理空壳脏行（company_name/contact_name 全空 = 采集 LLM 提取失败的无意义线索·问题1根因产物）
--    仅删无 customer 晋级引用的；先删其 lead_ref / contact_source 子行，再删池行。条件三处一致。
DELETE FROM lead_ref WHERE lead_contact_id IN (
  SELECT c.id FROM contact c
  WHERE COALESCE(NULLIF(trim(c.company_name), ''), '') = ''
    AND COALESCE(NULLIF(trim(c.contact_name), ''), '') = ''
    AND NOT EXISTS (SELECT 1 FROM customer cu WHERE cu.lead_contact_id = c.id)
);
DELETE FROM contact_source WHERE lead_contact_id IN (
  SELECT c.id FROM contact c
  WHERE COALESCE(NULLIF(trim(c.company_name), ''), '') = ''
    AND COALESCE(NULLIF(trim(c.contact_name), ''), '') = ''
    AND NOT EXISTS (SELECT 1 FROM customer cu WHERE cu.lead_contact_id = c.id)
);
DELETE FROM contact c
WHERE COALESCE(NULLIF(trim(c.company_name), ''), '') = ''
  AND COALESCE(NULLIF(trim(c.contact_name), ''), '') = ''
  AND NOT EXISTS (SELECT 1 FROM customer cu WHERE cu.lead_contact_id = c.id);
