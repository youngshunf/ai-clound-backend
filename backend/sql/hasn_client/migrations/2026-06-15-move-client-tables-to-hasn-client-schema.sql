-- 迁移：客户端 companion 6 张表从 public 搬入独立 PG schema hasn_client（ADR-15：一应用一 schema）。
-- 收编方案 09 R3（承 R2 目录归位 app/hasn_client）：补齐云端轴①（schema 隔离）。
-- app_id=client；URL /api/v1/app/* 保持不变（移动端+桌面端客户端已发布版本依赖）；表名不改（仅 SET SCHEMA）。
-- 6 张表 = 推送 push_tokens / push_token_audit / push_receipts + 灰度 feature_flags /
--          feature_flag_assignments + 遥测 telemetry_events。
-- feature_flag_assignments → feature_flags 的外键随两表一并 SET SCHEMA（PG 按 OID 引用，
--   两表都搬到 hasn_client 后约束自动保持有效，搬迁顺序无关）。
-- 幂等：逐表「仅当表在 public 且不在 hasn_client 时」才 SET SCHEMA；
--       全新库（codegen 直接落 hasn_client）/ 已迁移库 → 自动跳过，无副作用。可重复执行。
-- ⚠️ 破坏性 DDL（元数据操作、很快，但会移动表）：生产执行须在停机/低峰窗口，先全库备份
--    （PG16 用 /www/server/pgsql/bin/pg_dump）。本地 6 表当前均 0 行，SET SCHEMA 无数据风险。
-- ⚠️ 配套代码：本应用表全 ORM 访问（经各模型 __table_args__ 'schema' 自动落 hasn_client），
--    无裸 raw SQL 需全限定（已全仓核验）。
-- 执行：psql "$DATABASE_URL" -v ON_ERROR_STOP=1 -f 本文件

DO $$
DECLARE
    t text;
    tbls text[] := ARRAY[
        'push_tokens',
        'push_token_audit',
        'push_receipts',
        'feature_flags',
        'feature_flag_assignments',
        'telemetry_events'
    ];
    moved int := 0;
BEGIN
    CREATE SCHEMA IF NOT EXISTS hasn_client;

    FOREACH t IN ARRAY tbls LOOP
        IF EXISTS (
            SELECT 1 FROM information_schema.tables
            WHERE table_schema = 'public' AND table_name = t
        ) AND NOT EXISTS (
            SELECT 1 FROM information_schema.tables
            WHERE table_schema = 'hasn_client' AND table_name = t
        ) THEN
            EXECUTE format('ALTER TABLE public.%I SET SCHEMA hasn_client', t);
            moved := moved + 1;
            RAISE NOTICE 'moved public.% -> hasn_client.%', t, t;
        END IF;
    END LOOP;

    RAISE NOTICE 'hasn_client schema 迁移完成：本次搬迁 % 张表（其余已在 hasn_client 或不存在，幂等跳过）', moved;
END $$;
