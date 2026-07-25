-- =====================================================
-- doc94 D1（末刀）：删除 user_subscription 上的五个余额列
--
-- 合同表记录的是「买了什么」，不是「还剩多少」。这五列是云端第二套余额账本
-- 留在合同表上的最后残留：
--   monthly_credits / current_credits / used_credits / purchased_credits / next_grant_date
--
-- **为什么单独一支、且必须最后执行**：
-- 这些列仍出现在对外响应契约里（`SubscriptionInfoResponse` → daemon 透传 → WebUI 与官网）。
-- 虽然它们的**取值**已经不再来自余额表（current_credits 取 NewAPI 权威快照、
-- used_credits 取 NewAPI 周期用量、monthly_credits 取商品目录 plan 的 credits_per_cycle），
-- 但**字段名仍在契约上**。删列必须与三端字段改造同窗，否则订阅页直接报错。
--
-- 执行前置：
--   1. 2026-07-25-credit-authority-drop-legacy-credit-objects.sql 已执行；
--   2. 云端响应改为只回 `credits_per_cycle` / `cycle_consumed_credits` / `wallet_credits`
--      三个新字段，旧五个字段已从 schema 中移除；
--   3. daemon 透传无需改（Value 透传），但 WebUI 与官网已停止读取旧字段并完成发布；
--   4. 上述发布观察无回退。
--
-- 周期额度不会因此丢失：C1 起每份合同都带 `plan_snapshot.credits_per_cycle`，
-- 那是购买时固化的快照，比 monthly_credits 更可信（不会被后续改价穿透）。
-- =====================================================

ALTER TABLE "hasn_billing"."user_subscription"
  DROP COLUMN IF EXISTS "monthly_credits",
  DROP COLUMN IF EXISTS "current_credits",
  DROP COLUMN IF EXISTS "used_credits",
  DROP COLUMN IF EXISTS "purchased_credits",
  DROP COLUMN IF EXISTS "next_grant_date";
