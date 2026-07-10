-- =====================================================
-- 统一商业化内核 MK-1：pay_order 加 offering_ref
-- 幂等：可重复执行。schema=hasn_billing（PayOrder 继承 BillingBase）
-- 设计事实源：docs/hasn-node设计文档/16-订阅与积分计费/02-统一商业化内核设计.md
-- 施工权威：同模块 实施/92 MK-1
-- =====================================================

-- offering_ref：商品目录引用快照 {offering_key,plan_key,kind}
-- 支付成功回调据 kind 分发履约（收编散落 order_type 的 if/elif 分派）
ALTER TABLE "hasn_billing"."pay_order"
  ADD COLUMN IF NOT EXISTS "offering_ref" jsonb;
COMMENT ON COLUMN "hasn_billing"."pay_order"."offering_ref"
  IS '商品目录引用快照（{offering_key,plan_key,kind}；支付成功回调按 kind 分发履约）';
