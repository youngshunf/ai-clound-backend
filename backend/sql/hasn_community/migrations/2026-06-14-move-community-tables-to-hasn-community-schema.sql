-- 迁移：社区 14 张表从 public 搬入独立 PG schema hasn_community（ADR-15：AI-Native 应用一应用一 schema）。
-- 模块目录已是 app/hasn_community；本迁移补齐云端轴①（schema 隔离）。
-- app_id / catalog / URL（/api/v1/community/*）保持 community 不变；表名不改（仅 SET SCHEMA）。
-- 幂等：逐表「仅当表在 public 且不在 hasn_community 时」才 SET SCHEMA；
--       全新库（codegen 直接落 hasn_community）/ 已迁移库 → 自动跳过，无副作用。可重复执行。
-- ⚠️ 破坏性 DDL（元数据操作、很快，但会移动表）：生产执行须在停机/低峰窗口，先全库备份。
-- 执行：psql "$DATABASE_URL" -v ON_ERROR_STOP=1 -f 本文件

DO $$
DECLARE
    t text;
    tbls text[] := ARRAY[
        'hasn_posts', 'hasn_articles', 'hasn_comments', 'hasn_likes', 'hasn_follows',
        'hasn_topics', 'hasn_content_topics', 'hasn_circles', 'hasn_circle_members',
        'hasn_collections', 'hasn_collection_items', 'hasn_community_blocks',
        'hasn_doc_spaces', 'hasn_doc_nodes'
    ];
    moved int := 0;
BEGIN
    CREATE SCHEMA IF NOT EXISTS hasn_community;

    FOREACH t IN ARRAY tbls LOOP
        IF EXISTS (
            SELECT 1 FROM information_schema.tables
            WHERE table_schema = 'public' AND table_name = t
        ) AND NOT EXISTS (
            SELECT 1 FROM information_schema.tables
            WHERE table_schema = 'hasn_community' AND table_name = t
        ) THEN
            EXECUTE format('ALTER TABLE public.%I SET SCHEMA hasn_community', t);
            moved := moved + 1;
            RAISE NOTICE 'moved public.% -> hasn_community.%', t, t;
        END IF;
    END LOOP;

    RAISE NOTICE 'community schema 迁移完成：本次搬迁 % 张表（其余已在 hasn_community 或不存在，幂等跳过）', moved;
END $$;
