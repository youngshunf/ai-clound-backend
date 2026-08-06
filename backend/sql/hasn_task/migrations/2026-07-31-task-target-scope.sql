-- =====================================================
-- hasn_task.task 增 target_scope（内置任务广播语义随任务行落地并下行）
-- 设计事实源：docs/产品与技术/技术设计/02-平台能力/记忆与知识库/归档/2026-08-06-旧记忆与知识库设计/旧域/19-多节点记忆分层与分身自治整理设计.md §9 / 决策 D-24
--
-- 播种时从 builtin_catalog.target_scope 透传到任务行，再随普通 task 同步事件下行到各节点：
--   · master_brain（默认）：任务只派给 task.agent_id 绑定的那个分身（既有行为，全部存量任务落此值）；
--   · all_agents：本节点 task_scheduler 到期时向**本节点每个在线分身**各派发一次（本地扇出）。
--
-- 为什么扇出在本地而不是云端多播几行：hasn_task.task 上有
-- uq_task_owner_builtin_key (owner_id, builtin_key) 唯一索引（2026-06-16-task-builtin-key.sql），
-- 一个 owner 同一 builtin_key 只允许一行存活；且播种幂等键 client_event_id='bts_{owner}_{builtin_key}'
-- 同样是 per-(owner,builtin_key) 唯一。云端只能播一行，广播语义只能靠字段透传 + 本地扇出。
--
-- 幂等：ADD COLUMN IF NOT EXISTS + 约束存在性判定，可重跑。
-- 注意 PostgreSQL 语法：COMMENT ON（非 MySQL 内联注释）。
-- =====================================================

ALTER TABLE hasn_task.task
    ADD COLUMN IF NOT EXISTS target_scope VARCHAR(16) NOT NULL DEFAULT 'master_brain';

-- CHECK 约束：取值收敛为两种广播语义（ADD CONSTRAINT 无 IF NOT EXISTS，按 conrelid+conname 判存在）
DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1
        FROM pg_constraint
        WHERE conrelid = 'hasn_task.task'::regclass
          AND conname = 'ck_task_target_scope'
    ) THEN
        ALTER TABLE hasn_task.task
            ADD CONSTRAINT ck_task_target_scope
            CHECK (target_scope IN ('master_brain', 'all_agents'));
    END IF;
END
$$;

COMMENT ON COLUMN hasn_task.task.target_scope IS
    '广播语义 (master_brain:只派绑定分身:gray/all_agents:本节点每个在线分身各一次:violet)：'
    '来自 builtin_catalog.target_scope 透传；all_agents 的扇出由本地 task_scheduler 完成，'
    '云端不因此多播任务行（受 uq_task_owner_builtin_key 唯一索引约束）';
