-- =====================================================
-- hasn_task.task 增 risk_level 列（「了解主人」主动规划闭环 D3）
-- 主动规划闭环为主人建任务时按风险分级落初始状态：
--   low（提醒/汇报/整理/查询，只读+对主人本人）→ scheduled 自动跑；
--   high（外发消息/花钱/改外部数据/不可逆）→ pending_approval 等主人批。
-- 不新增 task 状态枚举（既有 status 已含 scheduled/pending_approval/rejected）；risk_level 仅决定建任务落哪个初始态。
-- 设计事实源：docs/hasn-node设计文档/19-规划与目标管理/03-了解主人：采访建档·完整度判定·主动规划闭环设计.md §4.5
-- 幂等：IF NOT EXISTS。
-- =====================================================

ALTER TABLE hasn_task.task
    ADD COLUMN IF NOT EXISTS risk_level varchar(10) NOT NULL DEFAULT 'low';

COMMENT ON COLUMN hasn_task.task.risk_level IS '风险等级 (low:低风险:green/high:高风险:orange)';
