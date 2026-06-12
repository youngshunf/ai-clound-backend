-- 迁移：为 hasn_growth 中「缺失 updated_time 列」的收编表补列，对齐 fba DateTimeMixin 约定。
--
-- 背景：lead_automation 的若干 append-only 表（audit_log/contact_source/export_batch/
-- export_item/rejected_record）原 DDL 只有 created_time，没有 updated_time。但其 model 经
-- fba codegen 继承 `Base`（含 DateTimeMixin.updated_time），任何经 ORM 的 INSERT 都会显式写
-- `updated_time`（init=False，插入值为 NULL）→ 触发 `column "updated_time" does not exist`。
-- 收编时 SET SCHEMA 原样搬表，未补这一列，遂成潜在缺口（business_service/export_service/
-- outreach_service 的审计写入路径全部受影响，仅因此前无真实 ORM 插入测试覆盖未暴露）。
--
-- 修复：补 `updated_time timestamptz NULL`（NULLABLE 对齐 onupdate-only 语义；DEFAULT now()
-- 仅为手工 SQL 插入兜底，不影响 ORM）。与 2026-06-12-updated-time-nullable.sql 配套
-- （那条管「有列但 NOT NULL」，本条管「整列缺失」）。
--
-- 幂等：仅对 information_schema 中不存在 updated_time 列的 hasn_growth 表执行 ADD COLUMN。

SET search_path TO hasn_growth, public;

DO $$
DECLARE
    r record;
BEGIN
    FOR r IN
        SELECT t.table_name
        FROM information_schema.tables t
        WHERE t.table_schema = 'hasn_growth'
          AND t.table_type = 'BASE TABLE'
          AND NOT EXISTS (
              SELECT 1
              FROM information_schema.columns c
              WHERE c.table_schema = 'hasn_growth'
                AND c.table_name = t.table_name
                AND c.column_name = 'updated_time'
          )
    LOOP
        EXECUTE format(
            'ALTER TABLE hasn_growth.%I ADD COLUMN updated_time timestamptz NULL DEFAULT now()',
            r.table_name
        );
        RAISE NOTICE 'updated_time ADD COLUMN: hasn_growth.%', r.table_name;
    END LOOP;
END $$;
