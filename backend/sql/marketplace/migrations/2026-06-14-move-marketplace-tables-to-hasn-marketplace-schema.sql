-- 迁移：能力市场 10 张表从 public 搬入独立 PG schema hasn_marketplace（ADR-15：AI-Native 应用一应用一 schema）。
-- 模块目录仍是 app/marketplace（hasn_ 目录归一另议）；本迁移补齐云端轴①（schema 隔离）。
-- app_id / catalog / URL（/api/v1/marketplace/*、/api/v1/skill-pack/* 等）保持不变；表名不改（仅 SET SCHEMA）。
-- 幂等：逐表「仅当表在 public 且不在 hasn_marketplace 时」才 SET SCHEMA；
--       全新库（codegen 直接落 hasn_marketplace）/ 已迁移库 → 自动跳过，无副作用。可重复执行。
-- ⚠️ 破坏性 DDL（元数据操作、很快，但会移动表）：生产执行须在停机/低峰窗口，先全库备份。
-- ⚠️ 配套代码：凡裸 raw SQL 引用 marketplace 表的地方（agent profile/listing 热路径 + skill_pack + 测试）
--    已同步全限定改写为 hasn_marketplace.<table>；ORM 经 MarketplaceBase 自动落到新 schema。
-- 执行：psql "$DATABASE_URL" -v ON_ERROR_STOP=1 -f 本文件

DO $$
DECLARE
    t text;
    tbls text[] := ARRAY[
        'marketplace_category', 'marketplace_download', 'marketplace_personal_skill',
        'marketplace_skill', 'marketplace_skill_version',
        'marketplace_sop', 'marketplace_sop_version',
        'marketplace_sync_log',
        'marketplace_template', 'marketplace_template_version'
    ];
    moved int := 0;
BEGIN
    CREATE SCHEMA IF NOT EXISTS hasn_marketplace;

    FOREACH t IN ARRAY tbls LOOP
        IF EXISTS (
            SELECT 1 FROM information_schema.tables
            WHERE table_schema = 'public' AND table_name = t
        ) AND NOT EXISTS (
            SELECT 1 FROM information_schema.tables
            WHERE table_schema = 'hasn_marketplace' AND table_name = t
        ) THEN
            EXECUTE format('ALTER TABLE public.%I SET SCHEMA hasn_marketplace', t);
            moved := moved + 1;
            RAISE NOTICE 'moved public.% -> hasn_marketplace.%', t, t;
        END IF;
    END LOOP;

    RAISE NOTICE 'marketplace schema 迁移完成：本次搬迁 % 张表（其余已在 hasn_marketplace 或不存在，幂等跳过）', moved;
END $$;
