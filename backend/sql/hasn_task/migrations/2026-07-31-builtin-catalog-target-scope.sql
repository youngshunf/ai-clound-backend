-- =====================================================
-- hasn_task.builtin_catalog 增 target_scope（内置任务的广播语义）
-- 设计事实源：docs/产品与技术/技术设计/02-平台能力/记忆与知识库/归档/2026-08-06-旧记忆与知识库设计/旧域/19-多节点记忆分层与分身自治整理设计.md §9 / 决策 D-24
--
-- 现行 target_agent_type 的语义是「承接该任务的内置 agent 类型键，NULL=绑主脑」——**只有单绑，
-- 没有「广播给全部分身」的形态**。而记忆复盘（memory_review）要求每个分身各自整理自己脚下那一片
-- 记忆（doc19 §4 自治边界），必须有真实机制承载，不能假装纯复用。故本迁移新增 target_scope：
--   · master_brain（默认）：沿用既有语义——按 target_agent_type 绑单个分身，NULL 时回退主脑；
--   · all_agents：本节点每个在线分身各执行一次。
--
-- 注意 all_agents 的扇出发生在**本地**：云端 hasn_task.task 上有
-- uq_task_owner_builtin_key (owner_id, builtin_key) 唯一索引（2026-06-16-task-builtin-key.sql），
-- 一条 catalog 在云端只能播出一行 task；因此云端仍只播一行（绑主脑），把 target_scope 随任务同步
-- 事件下行，由各节点 task_scheduler 到期时向本节点每个在线分身各派发一次。
--
-- 幂等：ADD COLUMN IF NOT EXISTS + 约束存在性判定，可重跑。
-- 注意 PostgreSQL 语法：COMMENT ON（非 MySQL 内联注释）。
-- =====================================================

ALTER TABLE hasn_task.builtin_catalog
    ADD COLUMN IF NOT EXISTS target_scope VARCHAR(16) NOT NULL DEFAULT 'master_brain';

-- CHECK 约束：取值收敛为两种广播语义（ADD CONSTRAINT 无 IF NOT EXISTS，按 conrelid+conname 判存在）
DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1
        FROM pg_constraint
        WHERE conrelid = 'hasn_task.builtin_catalog'::regclass
          AND conname = 'ck_builtin_catalog_target_scope'
    ) THEN
        ALTER TABLE hasn_task.builtin_catalog
            ADD CONSTRAINT ck_builtin_catalog_target_scope
            CHECK (target_scope IN ('master_brain', 'all_agents'));
    END IF;
END
$$;

COMMENT ON COLUMN hasn_task.builtin_catalog.target_scope IS
    '广播语义 (master_brain:绑单个分身:gray/all_agents:每个在线分身各一次:violet)：'
    'master_brain=按 target_agent_type 绑单个分身（NULL 回退主脑，既有语义不变）；'
    'all_agents=本节点每个在线分身各执行一次（云端仍只播一行 task，扇出在本地 task_scheduler）';
