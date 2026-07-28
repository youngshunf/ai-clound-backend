-- S4：把开通命令拆为可独立重试的四个步骤。
-- 项目总状态使用 ready，步骤终态使用 success，避免同一词同时表达两层状态。

SET search_path TO hasn_growth, public;

ALTER TABLE growth_project_provision
    DROP CONSTRAINT IF EXISTS uq_growth_project_provision_command,
    DROP CONSTRAINT IF EXISTS uq_growth_project_provision_idempotency,
    DROP CONSTRAINT IF EXISTS uq_growth_project_provision_command_step,
    DROP CONSTRAINT IF EXISTS uq_growth_project_provision_idempotency_step,
    DROP CONSTRAINT IF EXISTS uq_growth_project_provision_project_step,
    DROP CONSTRAINT IF EXISTS ck_growth_project_provision_status;

UPDATE growth_project_provision
SET status = 'success'
WHERE status = 'ready';

ALTER TABLE growth_project_provision
    ADD CONSTRAINT uq_growth_project_provision_command_step
        UNIQUE (command_id, step),
    ADD CONSTRAINT uq_growth_project_provision_idempotency_step
        UNIQUE (idempotency_key, step),
    ADD CONSTRAINT uq_growth_project_provision_project_step
        UNIQUE (growth_project_id, step),
    ADD CONSTRAINT ck_growth_project_provision_status CHECK (
        status IN ('pending', 'running', 'success', 'failed')
    );

COMMENT ON COLUMN growth_project_provision.command_id
    IS '一次开通或重试命令的 trace UUID；同一命令包含多个步骤';
COMMENT ON COLUMN growth_project_provision.idempotency_key
    IS '客户端稳定幂等键；同一键重放不得创建第二个项目或外部资源';
COMMENT ON COLUMN growth_project_provision.step
    IS '步骤 (create_funnel/create_knowledge/attach_knowledge/seed_knowledge)';
COMMENT ON COLUMN growth_project_provision.status
    IS '步骤状态 (pending/running/success/failed)';
