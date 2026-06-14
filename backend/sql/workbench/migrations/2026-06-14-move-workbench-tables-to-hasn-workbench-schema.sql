-- 迁移：工作台 3 张表从 public 搬入独立 PG schema hasn_workbench（ADR-15：AI-Native 应用一应用一 schema）。
-- 工作台简报/偏好子域已按 ADR-15 §4 从 app/hasn 抽出为独立应用 app/workbench；本迁移补齐云端轴①（schema 隔离）。
-- app_id=workbench；URL /api/v1/hasn/app/workbench/* 保持不变（daemon WorkbenchModule/WorkbenchCloud 代理依赖）；表名不改（仅 SET SCHEMA）。
-- 平台底座 workbench_domain_service/workbench_app_registry/workbench_event_bus 留 app/hasn（不迁），内置任务目录在 app/hasn_task。
-- 幂等：逐表「仅当表在 public 且不在 hasn_workbench 时」才 SET SCHEMA；全新库/已迁移库自动跳过。可重复执行。
-- ⚠️ 破坏性 DDL（元数据操作、很快，但会移动表）：生产执行须在停机/低峰窗口，先全库备份。
-- ⚠️ 配套代码：本应用表当前全 ORM 访问（经 WorkbenchBase 自动落 hasn_workbench），无裸 raw SQL 需全限定。
-- 执行：psql "$DATABASE_URL" -v ON_ERROR_STOP=1 -f 本文件

DO $$
DECLARE
    t text;
    tbls text[] := ARRAY[
        'hasn_workbench_briefing',
        'hasn_workbench_briefing_feedback',
        'hasn_owner_workbench_pref'
    ];
    moved int := 0;
BEGIN
    CREATE SCHEMA IF NOT EXISTS hasn_workbench;

    FOREACH t IN ARRAY tbls LOOP
        IF EXISTS (
            SELECT 1 FROM information_schema.tables
            WHERE table_schema = 'public' AND table_name = t
        ) AND NOT EXISTS (
            SELECT 1 FROM information_schema.tables
            WHERE table_schema = 'hasn_workbench' AND table_name = t
        ) THEN
            EXECUTE format('ALTER TABLE public.%I SET SCHEMA hasn_workbench', t);
            moved := moved + 1;
            RAISE NOTICE 'moved public.% -> hasn_workbench.%', t, t;
        END IF;
    END LOOP;

    RAISE NOTICE 'workbench schema 迁移完成：本次搬迁 % 张表（其余已在 hasn_workbench 或不存在，幂等跳过）', moved;
END $$;
