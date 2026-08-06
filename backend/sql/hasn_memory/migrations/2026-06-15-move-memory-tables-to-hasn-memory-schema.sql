-- 迁移：记忆系统 7 张表从 public 搬入独立 PG schema hasn_memory（ADR-15：AI-Native 应用一应用一 schema）。
-- 配套 `docs/产品与技术/技术设计/02-平台能力/记忆与知识库/归档/2026-08-06-旧记忆与知识库设计/旧域/实施/95-记忆独立模块与schema拆分方案.md`。
-- 记忆子系统按 ADR-15 收编为独立模块 app/hasn_memory；本迁移补齐云端轴①（schema 隔离）。
--
-- 7 张表（public → hasn_memory，schema 内去 hasn_/memory_ 冗余前缀）：
--   hasn_owner_memory              -> owner_memory               （USER.md 合并，0 行）
--   hasn_owner_memory_contribution -> owner_memory_contribution   （贡献流，0 行）
--   memory_namespace_revisions     -> namespace_revision          （同步 revision，⚠️ 31 行真实数据，必须 SET SCHEMA 保全）
--   episodic_turns                 -> episodic_turn               （doc 04 §5，0 行 stub）
--   semantic_facts                 -> semantic_fact               （doc 04 §6，0 行 stub）
--   memory_events                  -> memory_event                （doc 04 §7，0 行 stub）
--   memory_extraction_jobs         -> extraction_job              （doc 04 §11，0 行 stub）
--
-- 幂等：逐表「仅当目标 hasn_memory.<new> 不存在」时才搬迁；支持「已 SET SCHEMA 未 RENAME」的半迁移恢复。
--       全新库（codegen 直接落 hasn_memory）/ 已迁移库 → 自动跳过，无副作用。可重复执行。
-- ⚠️ 破坏性 DDL（元数据操作、很快，但会移动/改名表）：生产执行须在停机/低峰窗口，先全库备份。
-- ⚠️ 数据保全：namespace_revisions 走 SET SCHEMA（移动元数据，不重建/drop），31 行 revision 无损。
-- ⚠️ 配套代码：hasn_sync_service 裸 SQL 已全限定改写 `hasn_memory.namespace_revision`；
--    ORM 经 HasnMemoryBase / 各 MappedBase 模型自动落到新 schema。
-- 执行：psql "$DATABASE_URL" -v ON_ERROR_STOP=1 -f 本文件

DO $$
DECLARE
    old_names text[] := ARRAY[
        'hasn_owner_memory', 'hasn_owner_memory_contribution', 'memory_namespace_revisions',
        'episodic_turns', 'semantic_facts', 'memory_events', 'memory_extraction_jobs'
    ];
    new_names text[] := ARRAY[
        'owner_memory', 'owner_memory_contribution', 'namespace_revision',
        'episodic_turn', 'semantic_fact', 'memory_event', 'extraction_job'
    ];
    i int;
    old_name text;
    new_name text;
    moved int := 0;
BEGIN
    CREATE SCHEMA IF NOT EXISTS hasn_memory;

    FOR i IN 1 .. array_length(old_names, 1) LOOP
        old_name := old_names[i];
        new_name := new_names[i];

        -- 目标已存在 → 已迁移，跳过
        IF to_regclass('hasn_memory.' || quote_ident(new_name)) IS NOT NULL THEN
            CONTINUE;
        END IF;

        IF to_regclass('public.' || quote_ident(old_name)) IS NOT NULL THEN
            -- 标准路径：public.old 仍在 → SET SCHEMA 再去前缀 RENAME
            EXECUTE format('ALTER TABLE public.%I SET SCHEMA hasn_memory', old_name);
            EXECUTE format('ALTER TABLE hasn_memory.%I RENAME TO %I', old_name, new_name);
            moved := moved + 1;
            RAISE NOTICE 'moved public.% -> hasn_memory.%', old_name, new_name;
        ELSIF to_regclass('hasn_memory.' || quote_ident(old_name)) IS NOT NULL THEN
            -- 半迁移恢复：已 SET SCHEMA 但未 RENAME
            EXECUTE format('ALTER TABLE hasn_memory.%I RENAME TO %I', old_name, new_name);
            moved := moved + 1;
            RAISE NOTICE 'renamed hasn_memory.% -> hasn_memory.%', old_name, new_name;
        END IF;
    END LOOP;

    RAISE NOTICE 'hasn_memory schema 迁移完成：本次搬迁 % 张表（其余已在 hasn_memory 或不存在，幂等跳过）', moved;
END $$;
