-- PLAN-LOOP L3（修 G6/G7）：待办挂里程碑 + 里程碑轻状态。
--
-- G6：todo.milestone_id 缺列 → 「计划→里程碑→待办」在数据层断裂（F6/F7 无法承载「阶段」维度）。
-- G7：里程碑仅 done bool，无进行中/未开始态。加 status 三态；done bool 保留兼容读，写侧由 status 派生。
-- 里程碑派生进度 = 其下待办完成率（服务端实时算，不加缓存列——量小）。幂等：IF NOT EXISTS。
SET search_path TO hasn_plan, public;

ALTER TABLE todo ADD COLUMN IF NOT EXISTS milestone_id bigint NULL REFERENCES plan_milestone(id) ON DELETE SET NULL;
COMMENT ON COLUMN todo.milestone_id IS '所属里程碑 (可空；须与 plan_id 同计划，服务端校验)';
CREATE INDEX IF NOT EXISTS idx_plan_todo_milestone ON todo (milestone_id) WHERE milestone_id IS NOT NULL;

ALTER TABLE plan_milestone ADD COLUMN IF NOT EXISTS status varchar(16) NOT NULL DEFAULT 'planned';
COMMENT ON COLUMN plan_milestone.status IS '状态 (planned:未开始:gray/doing:进行中:amber/done:已完成:green)';

-- 存量 done=true 的里程碑回填 status='done'（与 done bool 对齐）。
UPDATE plan_milestone SET status = 'done' WHERE done = true AND status = 'planned';
