-- 场景工作流执行记录恢复（doc98 R1-b）：使云端执行账本在父定义缺失后仍可独立呈现。
--
-- 本迁移只扩展历史投影字段与实例化幂等键：执行调度仍由 daemon 权威推进。
-- 所有 DDL 均可重复执行；既有孤儿执行记录保持 NULL，禁止根据标题或 UUID 猜测项目归属。

CREATE SCHEMA IF NOT EXISTS hasn_task;

-- 执行记录的展示和项目快照。父 workflow 后续改名、改模板或摘项目时，历史记录不随之漂移。
ALTER TABLE hasn_task.workflow_run
    ADD COLUMN IF NOT EXISTS workflow_name_snapshot varchar(255);
ALTER TABLE hasn_task.workflow_run
    ADD COLUMN IF NOT EXISTS template_key_snapshot varchar(128);
ALTER TABLE hasn_task.workflow_run
    ADD COLUMN IF NOT EXISTS project_id uuid;

COMMENT ON COLUMN hasn_task.workflow_run.workflow_name_snapshot IS 'fire 时的工作流名称快照；父定义缺失仍可呈现历史';
COMMENT ON COLUMN hasn_task.workflow_run.template_key_snapshot IS 'fire 时的场景模板键快照；父定义缺失仍可呈现历史';
COMMENT ON COLUMN hasn_task.workflow_run.project_id IS 'fire 时的云端权威平台项目 UUID；存量孤儿历史保持 NULL';

CREATE INDEX IF NOT EXISTS idx_workflow_run_owner_project_created
    ON hasn_task.workflow_run (owner_id, project_id, created_time DESC)
    WHERE project_id IS NOT NULL;

-- Owner 场景实例化必须可按幂等键重放，防止本地镜像失败或网络超时后创建第二张定义。
ALTER TABLE hasn_task.workflow
    ADD COLUMN IF NOT EXISTS instantiation_idempotency_key varchar(128);

COMMENT ON COLUMN hasn_task.workflow.instantiation_idempotency_key IS 'Owner 场景实例化幂等键；同一 owner 重放返回同一 workflow';

CREATE UNIQUE INDEX IF NOT EXISTS uq_workflow_owner_instantiation_idempotency
    ON hasn_task.workflow (owner_id, instantiation_idempotency_key)
    WHERE instantiation_idempotency_key IS NOT NULL;
