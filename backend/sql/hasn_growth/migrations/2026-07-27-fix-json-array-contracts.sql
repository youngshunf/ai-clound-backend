-- 修复生成模型曾把数组型 JSONB 误标为对象并以 {} 初始化的问题。
-- 只迁移空对象，保留可能承载历史扩展数据的非空对象。
DO $$
DECLARE
    item record;
BEGIN
    FOR item IN
        SELECT *
        FROM (VALUES
            ('collection_job', 'source_types'),
            ('source_config', 'min_contact_fields'),
            ('source_config', 'domain_blacklist'),
            ('source_config', 'country_blacklist'),
            ('customer', 'tags')
        ) AS fields(table_name, column_name)
    LOOP
        EXECUTE format(
            'UPDATE hasn_growth.%I SET %I = ''[]''::jsonb WHERE %I = ''{}''::jsonb',
            item.table_name,
            item.column_name,
            item.column_name
        );
        EXECUTE format(
            'ALTER TABLE hasn_growth.%I ALTER COLUMN %I SET DEFAULT ''[]''::jsonb',
            item.table_name,
            item.column_name
        );
    END LOOP;
END
$$;
