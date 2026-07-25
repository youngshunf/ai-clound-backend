-- =====================================================
-- doc94 D1：删除云端遗留积分对象
--
-- **执行前置（缺一不可，顺序不能反）**：
--   1. 档位读路径已切到 billing_offering/billing_plan 并上线
--      （2026-07-25-credit-authority-plan-display-migrate.sql 已执行且展示字段无缺口）；
--   2. daemon 与 WebUI 已完成改指发布（/credits/history 已下线，/transactions 与
--      /credits/daily 已改读 NewAPI 形状）；
--   3. 存量余额 rebase（R1）已执行并核对；
--   4. 流量切换后连续观察满一个完整 30 天周期且无回退。
--
-- 先删表会同时打断 daemon 的 5 个已发布端点与官网定价页——顺序反了就是线上事故。
--
-- 删除的是「云端的第二套余额账本」：
--   - user_credit_balance / credit_transaction：余额桶与流水，权威已回归 NewAPI；
--   - subscription_tier / credit_package：档位与积分包配置，已迁入商品目录；
--   - llm_newapi_user_mapping 的两个同步水位列：反向覆盖写回的残留；
--   - user_subscription 的五个余额列：合同表不该持有余额。
-- =====================================================

-- ============ 0) 完整性守卫：展示字段没搬完就不许往下走 ============
-- 这里故意让迁移**失败**而不是警告：drop 之后再发现缺口就无法回头了。
DO $$
DECLARE
  missing_count INTEGER;
BEGIN
  SELECT COUNT(*) INTO missing_count
    FROM "hasn_billing"."billing_plan"
   WHERE "offering_key" IN ('llm:tier', 'credits:topup')
     AND "display_json" = '{}'::jsonb;
  IF missing_count > 0 THEN
    RAISE EXCEPTION 'doc94 D1 前置未满足：仍有 % 条 plan 缺展示字段，先跑 plan-display-migrate 并补齐后再执行本迁移', missing_count;
  END IF;
END $$;

-- ============ 1) 余额桶与流水 ============
DROP TABLE IF EXISTS "hasn_billing"."user_credit_balance";
DROP TABLE IF EXISTS "hasn_billing"."credit_transaction";

-- ============ 2) 档位与积分包配置（已迁入商品目录） ============
DROP TABLE IF EXISTS "hasn_billing"."subscription_tier";
DROP TABLE IF EXISTS "hasn_billing"."credit_package";

-- ============ 3) 反向同步水位列 ============
-- 这两列是「云端按已用量算目标 quota 覆盖写回」留下的游标，随该数据流一并消失。
ALTER TABLE "hasn_billing"."llm_newapi_user_mapping"
  DROP COLUMN IF EXISTS "last_synced_used_quota",
  DROP COLUMN IF EXISTS "last_synced_at";

-- ============ 4) 合同表上的余额列 —— 见同目录 *-drop-user-subscription-credit-columns.sql ============
-- 这五列（monthly_credits / current_credits / used_credits / purchased_credits / next_grant_date）
-- 仍出现在对外响应契约里（SubscriptionInfoResponse → daemon → WebUI/官网），
-- 删列必须与前端同窗改造，独立一支迁移，**不与本迁移同窗执行**。
