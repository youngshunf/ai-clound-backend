-- KNOWU §7 主动规划闭环：preference 加 proactive_planned 幂等认领标记 + owner 单例唯一约束。
--
-- 背景：5 维画像全 sufficient 后分身从"被动"切"主动"，需触发"恰好一次"主动规划工作会话。
-- 用 preference.proactive_planned 作跨设备持久幂等标记，配 owner_hasn_id 唯一索引 → 原子
-- INSERT ... ON CONFLICT DO UPDATE WHERE proactive_planned=false 认领，保证多设备/多次刷新只赢一次。
--
-- preference 既有「owner 单例」是模型约定但此前未在 DB 强制；此处补唯一索引（ON CONFLICT 依赖它）。
-- 幂等：列与索引均 IF NOT EXISTS，可重复执行。

ALTER TABLE hasn_plan.preference
    ADD COLUMN IF NOT EXISTS proactive_planned boolean NOT NULL DEFAULT false;

COMMENT ON COLUMN hasn_plan.preference.proactive_planned IS
    '主动规划闭环是否已触发（KNOWU §7，首次 all_sufficient 原子认领一次，跨设备持久幂等）';

CREATE UNIQUE INDEX IF NOT EXISTS uq_plan_preference_owner
    ON hasn_plan.preference (owner_hasn_id);
