-- =====================================================
-- hasn_task.workflow_run 增 advance_mode 列（工作流应用产品化 P2 · W-S1 推进档位）
-- manual（默认，逐环派发）：节点进 ready 后停住，等放行 API（= 场景「派 XX 分身开工」蓝色主按钮），主人始终在环上；
-- auto（自动接力）：节点 done → recompute_ready 解锁后继 → 自动直派，并行支路同 tick 并发派发。
-- 可运行中翻转（= 场景头卡「自动接力」开关）。
-- 设计事实源：docs/hasn-node设计文档/12-任务系统实施方案/11-工作流应用产品化（场景即模板·直派工作会话·双闸·商业化）设计.md §5.1
-- 幂等：ADD COLUMN IF NOT EXISTS + CHECK 约束 DROP/ADD IF EXISTS，可重复执行。
-- =====================================================

ALTER TABLE hasn_task.workflow_run
    ADD COLUMN IF NOT EXISTS advance_mode varchar(10) NOT NULL DEFAULT 'manual';

-- CHECK 约束幂等重建（沿用本 schema 既有范式：DROP IF EXISTS + ADD）
ALTER TABLE hasn_task.workflow_run DROP CONSTRAINT IF EXISTS chk_workflow_run_advance_mode;
ALTER TABLE hasn_task.workflow_run ADD CONSTRAINT chk_workflow_run_advance_mode
    CHECK (advance_mode IN ('manual', 'auto'));

COMMENT ON COLUMN hasn_task.workflow_run.advance_mode IS '推进档位 (manual:逐环派发:blue/auto:自动接力:green)，默认 manual；可运行中翻转（W-S1 §5.1）';
