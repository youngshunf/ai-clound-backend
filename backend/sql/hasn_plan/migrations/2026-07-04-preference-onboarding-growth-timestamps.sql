-- KNOWU「每日关注·了解主人」每周再提醒节奏闸：preference 加两个「上次派发时间」时间戳列。
--
-- 背景：内置每日简报（daily_briefing）每天都跑。要在简报里加「了解主人」维度——画像不完整就主动派采访会话、
-- 完整就每周派一次成长复盘会话——但**不能每天都派**（会打扰主人）。用 preference 记「上次派采访/成长会话的时间」，
-- 配周期性「超冷却期才可重新认领」的原子 claim（默认 7 天，见 plan_service.claim_profile_onboarding /
-- claim_growth_review）：距上次 > 7 天或从未派过才认领本轮派发权。NULL=从未派过。
--
-- 依赖既有 uq_plan_preference_owner 唯一索引（2026-06-27 已建）作 ON CONFLICT 目标。幂等：列 IF NOT EXISTS。

ALTER TABLE hasn_plan.preference
    ADD COLUMN IF NOT EXISTS last_onboarding_at timestamptz;

ALTER TABLE hasn_plan.preference
    ADD COLUMN IF NOT EXISTS last_growth_at timestamptz;

COMMENT ON COLUMN hasn_plan.preference.last_onboarding_at IS
    '上次派「了解主人」采访会话时间（KNOWU 每周再提醒节奏闸，周期 claim 用；NULL=从未派过）';

COMMENT ON COLUMN hasn_plan.preference.last_growth_at IS
    '上次派「成长复盘/主动规划」会话时间（KNOWU 每周再提醒节奏闸，周期 claim 用；NULL=从未派过）';
