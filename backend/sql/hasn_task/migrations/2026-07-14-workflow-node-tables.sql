-- 工作流应用产品化 P1-cloud 数据层：节点/节点执行拆专属表（模块 12 · 场景与工作流产品化 doc11）
--
-- 现状：工作流节点借道 hasn_task.task（workflow_uuid + node_key），节点执行借道
-- hasn_task.run（workflow_run_uuid + node_key）。本迁移采用 expand-only 安全迁移：
--   ① 新增 workflow_node（节点定义）+ workflow_node_run（节点执行态）两张专属表；
--   ② 从存量 task/run 回填两表；
--   ③ 建图 service 双写（保留 task 节点行兼容），读侧切新表。
-- 本切片**不删** task/run 的节点写路径（daemon 尚未切换，删了会破坏现有链路）。
--
-- 端云稳定标识：node_uuid（前缀 nd_）/ node_run_uuid（前缀 ndr_）与本仓所有跨端实体一致，
-- 用稳定 *_uuid 同步（非 bigint id）。节点用 workflow_uuid + node_key 在图内唯一。
--
-- 幂等：可重复执行（IF NOT EXISTS + ON CONFLICT DO NOTHING）；PostgreSQL 语法。
-- 执行：psql -d huanxing -f backend/sql/hasn_task/migrations/2026-07-14-workflow-node-tables.sql

CREATE SCHEMA IF NOT EXISTS hasn_task;
-- 回填用 gen_random_uuid() 生成稳定 UUID
CREATE EXTENSION IF NOT EXISTS pgcrypto;

-- ============================================================
-- 1. hasn_task.workflow_node（节点定义，建图时物化）
-- ============================================================
CREATE TABLE IF NOT EXISTS hasn_task.workflow_node (
    "id"                    bigserial PRIMARY KEY,
    "node_uuid"             varchar(64) NOT NULL UNIQUE,
    "workflow_uuid"         varchar(64) NOT NULL,
    "owner_id"              varchar(64) NOT NULL DEFAULT '',
    "node_key"              varchar(64) NOT NULL,
    "name"                  varchar(200) NOT NULL DEFAULT '',
    "description"           text,
    "default_agent_type"    varchar(64),
    "agent_id"              varchar(64) NOT NULL DEFAULT '',
    "prompt"                text,
    "system_prompt"         text,
    "apps"                  jsonb NOT NULL DEFAULT '[]'::jsonb,
    "skills"                jsonb NOT NULL DEFAULT '[]'::jsonb,
    "enabled_toolsets"      jsonb,
    "output_spec"           jsonb,
    "review_policy"         jsonb,
    "is_origin"             boolean NOT NULL DEFAULT false,
    "display"               jsonb NOT NULL DEFAULT '{}'::jsonb,
    "max_retries"           integer NOT NULL DEFAULT 4,
    "enable_subagents"      boolean NOT NULL DEFAULT false,
    "created_time"          timestamptz(6) NOT NULL DEFAULT now(),
    "updated_time"          timestamptz(6),

    CONSTRAINT "uq_workflow_node_key"
        UNIQUE ("workflow_uuid", "node_key")
);

CREATE INDEX IF NOT EXISTS "idx_workflow_node_workflow"
    ON hasn_task.workflow_node ("workflow_uuid");

COMMENT ON TABLE  hasn_task.workflow_node IS '工作流节点定义（建图时物化，P1 与借道 task 节点行双写并存）';
COMMENT ON COLUMN hasn_task.workflow_node."node_uuid" IS '端云稳定节点 UUID（前缀 nd_，同步主键）';
COMMENT ON COLUMN hasn_task.workflow_node."workflow_uuid" IS '所属工作流稳定 UUID';
COMMENT ON COLUMN hasn_task.workflow_node."node_key" IS '图内稳定节点标识（如 research-cost，同图唯一）';
COMMENT ON COLUMN hasn_task.workflow_node."name" IS '节点名称（缺省取 node_key）';
COMMENT ON COLUMN hasn_task.workflow_node."description" IS '节点描述';
COMMENT ON COLUMN hasn_task.workflow_node."default_agent_type" IS '默认人设类型（P3 用，可空）';
COMMENT ON COLUMN hasn_task.workflow_node."agent_id" IS '解析后的目标分身 hasn_id';
COMMENT ON COLUMN hasn_task.workflow_node."prompt" IS '节点任务指令';
COMMENT ON COLUMN hasn_task.workflow_node."system_prompt" IS '节点系统提示词';
COMMENT ON COLUMN hasn_task.workflow_node."apps" IS '默认应用绑定 [app_id...]';
COMMENT ON COLUMN hasn_task.workflow_node."skills" IS '默认技能绑定 [skill...]';
COMMENT ON COLUMN hasn_task.workflow_node."enabled_toolsets" IS '限制工具集（NULL=全部；继承 task 语义，派发时取授权交集）';
COMMENT ON COLUMN hasn_task.workflow_node."output_spec" IS '产出闸声明 {kind,label}（P2，可空）';
COMMENT ON COLUMN hasn_task.workflow_node."review_policy" IS '质量门声明 {mode,criteria,reviewer_agent_type,max_rejects}（P4，可空）';
COMMENT ON COLUMN hasn_task.workflow_node."is_origin" IS '是否起点节点';
COMMENT ON COLUMN hasn_task.workflow_node."display" IS '呈现元数据 {order,step_label}';
COMMENT ON COLUMN hasn_task.workflow_node."max_retries" IS '最大重试次数';
COMMENT ON COLUMN hasn_task.workflow_node."enable_subagents" IS '允许节点会话内使用子分身 delegate_task';

-- ============================================================
-- 2. hasn_task.workflow_node_run（节点执行态）
-- ============================================================
CREATE TABLE IF NOT EXISTS hasn_task.workflow_node_run (
    "id"                    bigserial PRIMARY KEY,
    "node_run_uuid"         varchar(64) NOT NULL UNIQUE,
    "workflow_run_uuid"     varchar(64) NOT NULL,
    "workflow_uuid"         varchar(64) NOT NULL DEFAULT '',
    "owner_id"              varchar(64) NOT NULL DEFAULT '',
    "node_key"              varchar(64) NOT NULL,
    "status"                varchar(20) NOT NULL DEFAULT 'pending',
    "work_session_id"       varchar(64),
    "artifacts"             jsonb NOT NULL DEFAULT '[]'::jsonb,
    "output_summary"        text,
    "output_gate_retries"   integer NOT NULL DEFAULT 0,
    "review_rejects"        integer NOT NULL DEFAULT 0,
    "attention_reason"      text,
    "started_time"          timestamptz(6),
    "completed_time"        timestamptz(6),
    "created_time"          timestamptz(6) NOT NULL DEFAULT now(),
    "updated_time"          timestamptz(6),

    CONSTRAINT "chk_workflow_node_run_status"
        CHECK ("status" IN ('pending', 'ready', 'running', 'waiting', 'needs_attention',
                            'done', 'failed', 'skipped', 'stale', 'cancelled')),
    CONSTRAINT "uq_workflow_node_run_key"
        UNIQUE ("workflow_run_uuid", "node_key")
);

CREATE INDEX IF NOT EXISTS "idx_workflow_node_run_run"
    ON hasn_task.workflow_node_run ("workflow_run_uuid");

COMMENT ON TABLE  hasn_task.workflow_node_run IS '工作流节点执行态（P1 与借道 run 节点执行行双写并存）';
COMMENT ON COLUMN hasn_task.workflow_node_run."node_run_uuid" IS '端云稳定节点执行 UUID（前缀 ndr_，同步主键）';
COMMENT ON COLUMN hasn_task.workflow_node_run."workflow_run_uuid" IS '所属工作流执行实例稳定 UUID';
COMMENT ON COLUMN hasn_task.workflow_node_run."workflow_uuid" IS '所属工作流稳定 UUID（冗余便于查询）';
COMMENT ON COLUMN hasn_task.workflow_node_run."node_key" IS '图内节点标识';
COMMENT ON COLUMN hasn_task.workflow_node_run."status" IS '状态 (pending:未开始:gray/ready:可派发:blue/running:分身工作中:orange/waiting:待你决策:orange/needs_attention:需要处理:red/done:已完成:green/failed:执行失败:red/skipped:已提供:gray/stale:基于旧产物:orange/cancelled:已取消:gray)';
COMMENT ON COLUMN hasn_task.workflow_node_run."work_session_id" IS '最新工作会话（历史经 origin_ref 反查）';
COMMENT ON COLUMN hasn_task.workflow_node_run."artifacts" IS '产出物 [{artifact_id,is_current}]';
COMMENT ON COLUMN hasn_task.workflow_node_run."output_summary" IS '产出摘要';
COMMENT ON COLUMN hasn_task.workflow_node_run."output_gate_retries" IS '产出闸重试次数（P2）';
COMMENT ON COLUMN hasn_task.workflow_node_run."review_rejects" IS '质量门驳回次数（P4）';
COMMENT ON COLUMN hasn_task.workflow_node_run."attention_reason" IS '需要处理的原因';

-- ============================================================
-- 3. 存量回填：task → workflow_node（幂等 ON CONFLICT DO NOTHING）
--    task 上 (workflow_uuid, node_key) 已由 partial unique 保证唯一，无需去重。
-- ============================================================
INSERT INTO hasn_task.workflow_node (
    "node_uuid", "workflow_uuid", "owner_id", "node_key", "name", "description",
    "agent_id", "prompt", "system_prompt", "enabled_toolsets", "is_origin", "enable_subagents"
)
SELECT
    'nd_' || replace(gen_random_uuid()::text, '-', ''),
    t."workflow_uuid",
    coalesce(t."owner_id", ''),
    t."node_key",
    coalesce(t."name", t."node_key"),
    t."description",
    coalesce(t."agent_id", ''),
    t."prompt",
    t."system_prompt",
    t."enabled_toolsets",
    false,
    coalesce(t."enable_subagents", false)
FROM hasn_task.task t
WHERE t."workflow_uuid" IS NOT NULL
  AND t."node_key" IS NOT NULL
  AND t."state" <> 'deleted'
ON CONFLICT ("workflow_uuid", "node_key") DO NOTHING;

-- ============================================================
-- 4. 存量回填：run → workflow_node_run（幂等 ON CONFLICT DO NOTHING）
--    run 无 (workflow_run_uuid, node_key) 唯一约束，同节点可能多条执行 →
--    DISTINCT ON 取最近一条（避免同 INSERT 内重复键），状态映射到节点执行态。
--    owner_id / workflow_uuid 从父 workflow_run 取（run 表无 owner_id 列）。
-- ============================================================
INSERT INTO hasn_task.workflow_node_run (
    "node_run_uuid", "workflow_run_uuid", "workflow_uuid", "owner_id", "node_key", "status",
    "work_session_id", "output_summary"
)
SELECT DISTINCT ON (r."workflow_run_uuid", r."node_key")
    'ndr_' || replace(gen_random_uuid()::text, '-', ''),
    r."workflow_run_uuid",
    coalesce(wr."workflow_uuid", ''),
    coalesce(wr."owner_id", ''),
    r."node_key",
    CASE r."status"
        WHEN 'pending' THEN 'pending'
        WHEN 'running' THEN 'running'
        WHEN 'success' THEN 'done'
        WHEN 'error'   THEN 'failed'
        WHEN 'timeout' THEN 'failed'
        WHEN 'silent'  THEN 'needs_attention'
        ELSE 'pending'
    END,
    r."session_id",
    r."output"
FROM hasn_task.run r
LEFT JOIN hasn_task.workflow_run wr ON wr."workflow_run_uuid" = r."workflow_run_uuid"
WHERE r."workflow_run_uuid" IS NOT NULL
  AND r."node_key" IS NOT NULL
ORDER BY r."workflow_run_uuid", r."node_key",
         coalesce(r."finished_at", r."started_at") DESC NULLS LAST, r."id" DESC
ON CONFLICT ("workflow_run_uuid", "node_key") DO NOTHING;
