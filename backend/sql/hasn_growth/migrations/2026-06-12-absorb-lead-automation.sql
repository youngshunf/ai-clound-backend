-- 获客营销全链路应用：采集子域收编迁移（设计 07 §5.0，G1 v1.2）
-- public 下 10 张 lead_* 采集表 → 独立 schema hasn_growth（表去前缀，ADR-15 §3.1）
-- 索引/约束/序列/部分唯一索引随 SET SCHEMA 自动迁移，不重建。
-- 幂等：可重复执行（仅当旧表仍在 public 时搬，仅当目标名未被占用时改名）；PostgreSQL 语法。
-- 不迁移 lead_*_smoke 测试残留表（留在 public）。
-- 执行：DATABASE_PORT=15432 psql 或 sqlalchemy 跑本文件（本地 dev 真在 :15432）。

CREATE SCHEMA IF NOT EXISTS hasn_growth;

-- ---------- SET SCHEMA + RENAME（10 表，幂等守卫） ----------
DO $$
DECLARE
    m RECORD;
BEGIN
    FOR m IN
        SELECT * FROM (VALUES
            ('lead_source_config',    'source_config'),
            ('lead_collection_job',   'collection_job'),
            ('lead_firecrawl_request','firecrawl_request'),
            ('lead_raw_record',       'raw_record'),
            ('lead_contact',          'contact'),
            ('lead_contact_source',   'contact_source'),
            ('lead_rejected_record',  'rejected_record'),
            ('lead_export_batch',     'export_batch'),
            ('lead_export_item',      'export_item'),
            ('lead_audit_log',        'audit_log')
        ) AS t(old_name, new_name)
    LOOP
        -- 1) 旧表仍在 public → 搬进 hasn_growth（保持原名）
        IF EXISTS (
            SELECT 1 FROM information_schema.tables
            WHERE table_schema = 'public' AND table_name = m.old_name
        ) THEN
            EXECUTE format('ALTER TABLE public.%I SET SCHEMA hasn_growth', m.old_name);
        END IF;

        -- 2) 已在 hasn_growth 且仍是旧名 + 新名未被占用 → 去前缀改名
        IF EXISTS (
            SELECT 1 FROM information_schema.tables
            WHERE table_schema = 'hasn_growth' AND table_name = m.old_name
        ) AND NOT EXISTS (
            SELECT 1 FROM information_schema.tables
            WHERE table_schema = 'hasn_growth' AND table_name = m.new_name
        ) THEN
            EXECUTE format('ALTER TABLE hasn_growth.%I RENAME TO %I', m.old_name, m.new_name);
        END IF;
    END LOOP;
END $$;
