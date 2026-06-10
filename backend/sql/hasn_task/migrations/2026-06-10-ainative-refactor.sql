-- 任务系统 AI-Native 应用化重构（模块 12 设计 06，M1）
-- 1) public.hasn_task* 五表 → 独立 schema hasn_task（表去前缀，ADR-15 §3.1）
-- 2) 新列：任务接续（D2）/ 子分身（D5）/ 创建者类别（D4）/ 接续链 / 产物引用清单（D6）
-- 3) state CHECK 重建：增 pending_approval / rejected（D4 业务态）
-- 幂等：可重复执行；PostgreSQL 语法。
-- 执行：psql -d huanxing -f backend/sql/hasn_task/migrations/2026-06-10-ainative-refactor.sql

CREATE SCHEMA IF NOT EXISTS hasn_task;

-- ---------- 1. SET SCHEMA + RENAME（仅当旧表仍在 public 时执行；索引/序列/约束随表迁移） ----------
DO $$
BEGIN
    IF EXISTS (SELECT 1 FROM information_schema.tables WHERE table_schema = 'public' AND table_name = 'hasn_task') THEN
        ALTER TABLE public.hasn_task SET SCHEMA hasn_task;
    END IF;
    IF EXISTS (SELECT 1 FROM information_schema.tables WHERE table_schema = 'hasn_task' AND table_name = 'hasn_task') THEN
        ALTER TABLE hasn_task.hasn_task RENAME TO task;
    END IF;

    IF EXISTS (SELECT 1 FROM information_schema.tables WHERE table_schema = 'public' AND table_name = 'hasn_task_run') THEN
        ALTER TABLE public.hasn_task_run SET SCHEMA hasn_task;
    END IF;
    IF EXISTS (SELECT 1 FROM information_schema.tables WHERE table_schema = 'hasn_task' AND table_name = 'hasn_task_run') THEN
        ALTER TABLE hasn_task.hasn_task_run RENAME TO run;
    END IF;

    IF EXISTS (SELECT 1 FROM information_schema.tables WHERE table_schema = 'public' AND table_name = 'hasn_task_assignment') THEN
        ALTER TABLE public.hasn_task_assignment SET SCHEMA hasn_task;
    END IF;
    IF EXISTS (SELECT 1 FROM information_schema.tables WHERE table_schema = 'hasn_task' AND table_name = 'hasn_task_assignment') THEN
        ALTER TABLE hasn_task.hasn_task_assignment RENAME TO assignment;
    END IF;

    IF EXISTS (SELECT 1 FROM information_schema.tables WHERE table_schema = 'public' AND table_name = 'hasn_task_run_summary') THEN
        ALTER TABLE public.hasn_task_run_summary SET SCHEMA hasn_task;
    END IF;
    IF EXISTS (SELECT 1 FROM information_schema.tables WHERE table_schema = 'hasn_task' AND table_name = 'hasn_task_run_summary') THEN
        ALTER TABLE hasn_task.hasn_task_run_summary RENAME TO run_summary;
    END IF;

    IF EXISTS (SELECT 1 FROM information_schema.tables WHERE table_schema = 'public' AND table_name = 'hasn_builtin_task_catalog') THEN
        ALTER TABLE public.hasn_builtin_task_catalog SET SCHEMA hasn_task;
    END IF;
    IF EXISTS (SELECT 1 FROM information_schema.tables WHERE table_schema = 'hasn_task' AND table_name = 'hasn_builtin_task_catalog') THEN
        ALTER TABLE hasn_task.hasn_builtin_task_catalog RENAME TO builtin_catalog;
    END IF;
END $$;

-- ---------- 2. 新列（幂等） ----------
ALTER TABLE hasn_task.task ADD COLUMN IF NOT EXISTS continuation_enabled BOOLEAN NOT NULL DEFAULT FALSE;
ALTER TABLE hasn_task.task ADD COLUMN IF NOT EXISTS enable_subagents BOOLEAN NOT NULL DEFAULT FALSE;
ALTER TABLE hasn_task.task ADD COLUMN IF NOT EXISTS created_by_kind VARCHAR(16) NOT NULL DEFAULT 'owner';
COMMENT ON COLUMN hasn_task.task.continuation_enabled IS '任务接续：把上次成功 run 的结果注入下次 session（D2）';
COMMENT ON COLUMN hasn_task.task.enable_subagents IS '允许任务会话内使用子分身 delegate_task（D5）';
COMMENT ON COLUMN hasn_task.task.created_by_kind IS '创建者类别 (owner:主人:blue/agent:分身:violet/builtin:内置:gray)';

ALTER TABLE hasn_task.run ADD COLUMN IF NOT EXISTS continued_from_run_id VARCHAR(64);
COMMENT ON COLUMN hasn_task.run.continued_from_run_id IS '本次 run 接续自哪条 run（端云稳定 run UUID，接续链 D2）';

ALTER TABLE hasn_task.run_summary ADD COLUMN IF NOT EXISTS continued_from_run_id VARCHAR(64);
ALTER TABLE hasn_task.run_summary ADD COLUMN IF NOT EXISTS artifacts_json JSONB;
COMMENT ON COLUMN hasn_task.run_summary.continued_from_run_id IS '本次 run 接续自哪条 run（端云稳定 run UUID，与 hasn_task.run 同名列，D2）';
COMMENT ON COLUMN hasn_task.run_summary.artifacts_json IS '产物引用清单 [{kind, filename, local_path?, producer_node_id, asset_id?, asset_uri?, available}]（D6，跨设备可见）';

-- ---------- 3. state CHECK 重建：增 pending_approval / rejected（D4） ----------
ALTER TABLE hasn_task.task DROP CONSTRAINT IF EXISTS chk_hasn_task_state;
ALTER TABLE hasn_task.task ADD CONSTRAINT chk_hasn_task_state CHECK (
    state IN (
        'scheduled', 'paused', 'completed', 'error', 'deleted',
        'waiting_for_runtime', 'needs_package_resolution', 'needs_skill_install',
        'pending_approval', 'rejected'
    )
);

-- ---------- 4. created_by_kind CHECK（幂等重建） ----------
ALTER TABLE hasn_task.task DROP CONSTRAINT IF EXISTS chk_hasn_task_created_by_kind;
ALTER TABLE hasn_task.task ADD CONSTRAINT chk_hasn_task_created_by_kind CHECK (
    created_by_kind IN ('owner', 'agent', 'builtin')
);
