-- 修复生成模型曾把数组型 JSONB 误标为对象并以 {} 初始化的问题。
-- 仅迁移空对象；非空对象可能是历史扩展形态，保留并交由业务显式处理。
DO $$
DECLARE
    item record;
BEGIN
    FOR item IN
        SELECT *
        FROM (VALUES
            ('profile', 'keywords'),
            ('profile', 'content_pillars'),
            ('profile', 'style_references'),
            ('profile', 'taboo_topics'),
            ('competitor', 'strengths'),
            ('competitor', 'tags'),
            ('content', 'target_platforms'),
            ('content_stage', 'asset_refs'),
            ('topic', 'keywords'),
            ('topic', 'creative_angles'),
            ('draft', 'media'),
            ('draft', 'tags'),
            ('draft', 'target_platforms'),
            ('media', 'tags'),
            ('viral_pattern', 'tags')
        ) AS fields(table_name, column_name)
    LOOP
        EXECUTE format(
            'UPDATE hasn_creator.%I SET %I = ''[]''::jsonb WHERE %I = ''{}''::jsonb',
            item.table_name,
            item.column_name,
            item.column_name
        );
        EXECUTE format(
            'ALTER TABLE hasn_creator.%I ALTER COLUMN %I SET DEFAULT ''[]''::jsonb',
            item.table_name,
            item.column_name
        );
    END LOOP;
END
$$;
