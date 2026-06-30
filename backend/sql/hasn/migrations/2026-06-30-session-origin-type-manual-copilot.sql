-- MEMCLOUD-B 契约修正（工作会话 summary_only 上云 #1989/#1990）：hasn_sessions.origin_type
-- 新增 `manual`、`copilot` 两个取值，对齐 daemon 实际产出的会话来源全集。
--
-- 背景：daemon 把工作会话 summary_only 异步上推 `POST /api/v1/hasn/app/sessions/upsert` 时，
-- origin_type 直接取本地 SQLite `sessions.origin_type`（daemon 全集 = task_run/ui/app/copilot/manual）。
-- 但云端 chk_origin_type 只放行 ui/scheduler/task_run/workflow_run/external_app/api/system/app，
-- 缺 `manual`（派发型工作会话，如 work_disp_*）与 `copilot`（会议副驾会话，
-- session_flow.rs `const ORIGIN_TYPE = "copilot"`）→ CheckViolationError → upsert 持续 500 →
-- session_outbox 无限重试刷爆 fba_error.log，工作会话→云端 summary 同步全断（毁 doc16 跨设备/单一云端记忆链）。
--
-- 取值集 = 既有放行集 ∪ daemon 实际在用的 manual/copilot，确保任何存量/在途行都不违例（只放宽不收紧）。
-- 幂等：DROP ... IF EXISTS + COMMENT 可反复执行。

ALTER TABLE hasn_sessions DROP CONSTRAINT IF EXISTS chk_origin_type;
ALTER TABLE hasn_sessions ADD CONSTRAINT chk_origin_type CHECK (
    origin_type IN ('ui', 'scheduler', 'task_run', 'workflow_run', 'external_app', 'api', 'system', 'app', 'manual', 'copilot')
);

COMMENT ON COLUMN hasn_sessions.origin_type IS
    '来源类型 (ui/scheduler/external_app/api/system/task_run/workflow_run/app/manual/copilot)；app=AI-Native 应用工作会话；manual=派发型工作会话；copilot=会议副驾会话';
