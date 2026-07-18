-- 任务中心 B1 · 数据层（doc96 施工清单 / doc12 §6.1「定时任务的项目·应用视角统一管理」）
-- 给 hasn_task.task 补三轴四列：项目轴 / 应用轴 / 执行方式，支撑任务中心按项目·应用视角分组。
--   · project_id     归属平台项目（云端权威 id，可空；freeform/裸任务为空）
--   · app_id         驱动的应用（freeform 为 NULL）
--   · execution_kind 执行方式（app_workflow / freeform，NOT NULL 默认 freeform）
--   · execution_spec 执行规格（app_workflow 存 {app_id,workflow_ref,params}；freeform 存 {prompt}）
--
-- 幂等：ADD COLUMN IF NOT EXISTS + CREATE INDEX IF NOT EXISTS，可重跑不报错。
-- 注意 PostgreSQL 语法：COMMENT ON（非 MySQL 内联注释）、jsonb（非 json）。

-- ① 补四列：project_id/app_id 可空；execution_kind/execution_spec NOT NULL + DEFAULT（存量行自动落缺省值）
ALTER TABLE hasn_task.task
    ADD COLUMN IF NOT EXISTS project_id UUID,
    ADD COLUMN IF NOT EXISTS app_id VARCHAR(64),
    ADD COLUMN IF NOT EXISTS execution_kind VARCHAR(32) NOT NULL DEFAULT 'freeform',
    ADD COLUMN IF NOT EXISTS execution_spec JSONB NOT NULL DEFAULT '{}'::jsonb;

COMMENT ON COLUMN hasn_task.task.project_id IS '归属平台项目 (hasn_project.hasn_project.id，云端权威 id，可空: freeform/裸任务为空; doc12 §6.1 项目视角)';
COMMENT ON COLUMN hasn_task.task.app_id IS '驱动的应用 app_id (freeform 为 NULL; doc12 §6.1 应用视角)';
COMMENT ON COLUMN hasn_task.task.execution_kind IS '执行方式 (app_workflow:应用工作流:blue/freeform:自由指令:gray)';
COMMENT ON COLUMN hasn_task.task.execution_spec IS '执行规格 JSON: app_workflow 存 {app_id,workflow_ref,params}; freeform 存 {prompt}';

-- ② 部分索引：按项目 / 应用视角过滤活任务（对齐 doc12 §6 的分组读，list_tasks 过滤走此路）
CREATE INDEX IF NOT EXISTS idx_task_owner_project
    ON hasn_task.task (owner_id, project_id)
    WHERE project_id IS NOT NULL AND deleted_at IS NULL;

CREATE INDEX IF NOT EXISTS idx_task_owner_app
    ON hasn_task.task (owner_id, app_id)
    WHERE app_id IS NOT NULL AND deleted_at IS NULL;
