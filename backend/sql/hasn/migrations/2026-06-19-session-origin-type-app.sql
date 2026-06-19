-- AppCollab（doc21 §D3 · 实施 AC-P2）：hasn_sessions.origin_type 新增 `app` 取值，origin_ref 约定 `resource:<app>:<id>`。
-- origin_type 有 CHECK 约束 chk_origin_type（原仅 ui/task_run/external_app/system）→ 必须放开约束加 'app'。
-- 取值集 = 列注释历史枚举(ui/scheduler/external_app/api/system) ∪ 实际在用(task_run/workflow_run) ∪ 新增(app)，
-- 确保任何存量行都不违例（只放宽不收紧）。幂等：DROP ... IF EXISTS + COMMENT 可反复执行。

ALTER TABLE hasn_sessions DROP CONSTRAINT IF EXISTS chk_origin_type;
ALTER TABLE hasn_sessions ADD CONSTRAINT chk_origin_type CHECK (
    origin_type IN ('ui', 'scheduler', 'task_run', 'workflow_run', 'external_app', 'api', 'system', 'app')
);

COMMENT ON COLUMN hasn_sessions.origin_type IS
    '来源类型 (ui/scheduler/external_app/api/system/task_run/workflow_run/app)；app=AI-Native 应用工作会话(AppCollab doc21 §D3)';
COMMENT ON COLUMN hasn_sessions.origin_ref IS
    '来源引用 (task_id/app_id/trace_id)；origin_type=app 时形如 resource:<app>:<id>(AppCollab doc21 §D3)';
