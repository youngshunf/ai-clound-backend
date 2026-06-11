-- 多任务编排（工作流 / 任务图）schema 扩展（模块 12 设计 07 + 实施 92 N1）
-- 在 v3.0 hasn_task 应用之上加 workflow / workflow_edge / workflow_run 三表，
-- 并给 task / run 加「节点归属」列。节点复用 v3.0 的 hasn_task.task（W3）。
--
-- 端云稳定标识：与 task.task_uuid 一致，本仓所有跨端实体用稳定 *_uuid 同步（非 bigint id）。
-- 边/节点用 workflow_uuid + node_key 引用（跨 schema 无法做 FK，由 service 层强制级联，07 §5.3）。
-- W5 驱动权租约：workflow_run.driver_node_id / lease_expires_at（07 §5.0）。
-- 图快照：workflow_run.graph_snapshot fire 时固化 nodes+edges（07 §5.4）。
--
-- 幂等：可重复执行；PostgreSQL 语法。
-- 执行：psql -d huanxing -f backend/sql/hasn_task/migrations/2026-06-11-workflow.sql

CREATE SCHEMA IF NOT EXISTS hasn_task;

-- ============================================================
-- 1. hasn_task.workflow（任务图定义）
-- ============================================================
CREATE TABLE IF NOT EXISTS hasn_task.workflow (
    "id"                    bigserial PRIMARY KEY,
    "workflow_uuid"         varchar(64) NOT NULL UNIQUE,
    "owner_id"              varchar(64) NOT NULL DEFAULT '',
    "name"                  varchar(200) NOT NULL DEFAULT '',
    "goal"                  text,
    "schedule_type"         varchar(20) NOT NULL DEFAULT 'once',
    "schedule_config"       jsonb NOT NULL DEFAULT '{}'::jsonb,
    "schedule_display"      varchar(200),
    "timezone"              varchar(64) NOT NULL DEFAULT 'Asia/Shanghai',
    "misfire_policy"        varchar(20) NOT NULL DEFAULT 'run_once',
    "catchup_limit"         integer,
    "enabled"               boolean NOT NULL DEFAULT true,
    "status"                varchar(20) NOT NULL DEFAULT 'active',
    "source"                varchar(32) NOT NULL DEFAULT 'owner',
    "created_by_kind"       varchar(16) NOT NULL DEFAULT 'owner',
    "continuation_enabled"  boolean NOT NULL DEFAULT false,
    "next_run_at"           timestamptz(6),
    "last_run_at"           timestamptz(6),
    "workflow_revision"     bigint NOT NULL DEFAULT 0,
    "deleted_at"            timestamptz(6),
    "created_time"          timestamptz(6) NOT NULL DEFAULT now(),
    "updated_time"          timestamptz(6),

    CONSTRAINT "chk_workflow_schedule_type"
        CHECK ("schedule_type" IN ('once', 'interval', 'cron')),
    CONSTRAINT "chk_workflow_status"
        CHECK ("status" IN ('draft', 'active', 'paused', 'archived', 'pending_approval', 'rejected')),
    CONSTRAINT "chk_workflow_created_by_kind"
        CHECK ("created_by_kind" IN ('owner', 'agent', 'builtin'))
);

CREATE INDEX IF NOT EXISTS "idx_workflow_owner_updated"
    ON hasn_task.workflow ("owner_id", "updated_time" DESC);
CREATE INDEX IF NOT EXISTS "idx_workflow_next_run"
    ON hasn_task.workflow ("enabled", "status", "next_run_at");

COMMENT ON TABLE  hasn_task.workflow IS '工作流（任务图）定义';
COMMENT ON COLUMN hasn_task.workflow."workflow_uuid" IS '端云稳定工作流 UUID（同步主键）';
COMMENT ON COLUMN hasn_task.workflow."goal" IS '总目标（也作整图验收口径，整图 sink 摘要语义见 07 §15-1）';
COMMENT ON COLUMN hasn_task.workflow."schedule_type" IS '整图定时 (once:一次性:blue/interval:间隔:green/cron:定时:orange)';
COMMENT ON COLUMN hasn_task.workflow."status" IS '状态 (draft:草稿:gray/active:启用:green/paused:已暂停:orange/archived:已归档:gray/pending_approval:待审批:orange/rejected:已拒绝:gray)';
COMMENT ON COLUMN hasn_task.workflow."created_by_kind" IS '创建者类别 (owner:主人:blue/agent:分身:violet/builtin:内置:gray)';
COMMENT ON COLUMN hasn_task.workflow."continuation_enabled" IS '跨 fire 接续：上次整图产出注入下次入口节点（07 §8.3，二期）';
COMMENT ON COLUMN hasn_task.workflow."workflow_revision" IS '工作流定义服务端修订号（字段级合并）';

-- ============================================================
-- 2. hasn_task.workflow_edge（依赖边 parent_node_key → child_node_key）
-- ============================================================
CREATE TABLE IF NOT EXISTS hasn_task.workflow_edge (
    "id"                    bigserial PRIMARY KEY,
    "workflow_uuid"         varchar(64) NOT NULL,
    "parent_node_key"       varchar(64) NOT NULL,
    "child_node_key"        varchar(64) NOT NULL,
    "created_time"          timestamptz(6) NOT NULL DEFAULT now(),
    "updated_time"          timestamptz(6),

    CONSTRAINT "uq_workflow_edge"
        UNIQUE ("workflow_uuid", "parent_node_key", "child_node_key")
);

CREATE INDEX IF NOT EXISTS "idx_workflow_edge_workflow"
    ON hasn_task.workflow_edge ("workflow_uuid");

COMMENT ON TABLE  hasn_task.workflow_edge IS '工作流依赖边（DAG，建/改时 DFS 环检测，云端 push 复验无环 07 §5.3）';
COMMENT ON COLUMN hasn_task.workflow_edge."workflow_uuid" IS '所属工作流稳定 UUID';

-- ============================================================
-- 3. hasn_task.workflow_run（执行实例 = 一次 fire）
-- ============================================================
CREATE TABLE IF NOT EXISTS hasn_task.workflow_run (
    "id"                    bigserial PRIMARY KEY,
    "workflow_run_uuid"     varchar(64) NOT NULL UNIQUE,
    "workflow_uuid"         varchar(64) NOT NULL,
    "owner_id"              varchar(64) NOT NULL DEFAULT '',
    "scheduled_fire_at"     timestamptz(6),
    "dedupe_key"            varchar(160) NOT NULL UNIQUE,
    "status"                varchar(20) NOT NULL DEFAULT 'running',
    "driver_node_id"        varchar(64),
    "lease_expires_at"      timestamptz(6),
    "graph_snapshot"        jsonb NOT NULL DEFAULT '{}'::jsonb,
    "output_summary"        text,
    "started_at"            timestamptz(6),
    "finished_at"           timestamptz(6),
    "created_time"          timestamptz(6) NOT NULL DEFAULT now(),
    "updated_time"          timestamptz(6),

    CONSTRAINT "chk_workflow_run_status"
        CHECK ("status" IN ('running', 'completed', 'failed', 'blocked', 'cancelled'))
);

CREATE INDEX IF NOT EXISTS "idx_workflow_run_workflow_created"
    ON hasn_task.workflow_run ("workflow_uuid", "created_time" DESC);
CREATE INDEX IF NOT EXISTS "idx_workflow_run_owner_created"
    ON hasn_task.workflow_run ("owner_id", "created_time" DESC);

COMMENT ON TABLE  hasn_task.workflow_run IS '工作流执行实例（一次 fire = 一个 workflow_run）';
COMMENT ON COLUMN hasn_task.workflow_run."workflow_run_uuid" IS '端云稳定执行实例 UUID（同步主键）';
COMMENT ON COLUMN hasn_task.workflow_run."dedupe_key" IS '幂等键 workflow_uuid:fire_at（防同 fire 重复创建实例）';
COMMENT ON COLUMN hasn_task.workflow_run."status" IS '状态 (running:运行中:orange/completed:已完成:green/failed:失败:red/blocked:阻塞:orange/cancelled:已取消:gray)';
COMMENT ON COLUMN hasn_task.workflow_run."driver_node_id" IS 'W5 驱动权租约：唯一推进者节点 ID（07 §5.0）';
COMMENT ON COLUMN hasn_task.workflow_run."lease_expires_at" IS 'W5 driver 租约到期（超时可被 CAS 接管）';
COMMENT ON COLUMN hasn_task.workflow_run."graph_snapshot" IS 'fire 时固化的 nodes+edges 快照，门控/收敛只读快照（07 §5.4）';
COMMENT ON COLUMN hasn_task.workflow_run."output_summary" IS '整图终态综合（无出边末端节点拼接，可标 is_sink 覆盖 07 §15-1）';

-- ============================================================
-- 4. task 加列：节点归属（workflow_uuid IS NOT NULL = 工作流节点）
--    注意：保留 task 上已存在的 v2.1 遗留列 workflow_id(bigint)/workflow(jsonb)，
--    本扩展用全新的 workflow_uuid/node_key 列，避免与遗留语义混淆。
-- ============================================================
ALTER TABLE hasn_task.task ADD COLUMN IF NOT EXISTS "workflow_uuid" varchar(64);
ALTER TABLE hasn_task.task ADD COLUMN IF NOT EXISTS "node_key" varchar(64);
COMMENT ON COLUMN hasn_task.task."workflow_uuid" IS '所属工作流稳定 UUID（NULL=独立任务，非工作流节点）';
COMMENT ON COLUMN hasn_task.task."node_key" IS '图内稳定节点标识（如 research-cost），同图唯一';

-- 同图节点 node_key 唯一（仅工作流节点，partial unique）
CREATE UNIQUE INDEX IF NOT EXISTS "uq_task_workflow_node"
    ON hasn_task.task ("workflow_uuid", "node_key")
    WHERE "workflow_uuid" IS NOT NULL;
CREATE INDEX IF NOT EXISTS "idx_task_workflow"
    ON hasn_task.task ("workflow_uuid")
    WHERE "workflow_uuid" IS NOT NULL;

-- ============================================================
-- 5. run 加列：节点执行归属
-- ============================================================
ALTER TABLE hasn_task.run ADD COLUMN IF NOT EXISTS "workflow_run_uuid" varchar(64);
ALTER TABLE hasn_task.run ADD COLUMN IF NOT EXISTS "node_key" varchar(64);
COMMENT ON COLUMN hasn_task.run."workflow_run_uuid" IS '所属工作流执行实例稳定 UUID（NULL=独立任务 run）';
COMMENT ON COLUMN hasn_task.run."node_key" IS '本次执行对应的图内节点标识';

CREATE INDEX IF NOT EXISTS "idx_run_workflow_run"
    ON hasn_task.run ("workflow_run_uuid")
    WHERE "workflow_run_uuid" IS NOT NULL;
