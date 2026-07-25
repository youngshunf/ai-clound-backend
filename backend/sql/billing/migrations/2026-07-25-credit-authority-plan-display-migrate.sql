-- =====================================================
-- doc94 D1：subscription_tier / credit_package 的展示字段迁入商品目录
--
-- 这两张表要被删除，但 /tiers、/packages 两个公开端点还从它们取 display_name、
-- features、description。删表前必须先把展示数据搬进 billing_plan，端点改指后
-- 才允许 drop——顺序反了会同时打断 daemon 的两个已发布端点和官网定价页。
--
-- 展示字段统一进 billing_plan.display_json：
--   { "display_name": ..., "features": {...}, "description": ..., "tier_name"/"package_name": ... }
-- 价格与配额已在 MK-1 seed 里进 price_amount / quota_json，这里只补展示面。
--
-- 幂等：ADD COLUMN IF NOT EXISTS + 仅回填空 display_json，可重复执行。
-- =====================================================

ALTER TABLE "hasn_billing"."billing_plan"
  ADD COLUMN IF NOT EXISTS "display_json" JSONB NOT NULL DEFAULT '{}'::jsonb;

COMMENT ON COLUMN "hasn_billing"."billing_plan"."display_json" IS
  '展示字段快照（display_name/features/description 等；doc94 D1 从 subscription_tier/credit_package 迁入）';

-- ============ 1) LLM 订阅档：月付 plan ============
UPDATE "hasn_billing"."billing_plan" bp
   SET "display_json" = jsonb_strip_nulls(jsonb_build_object(
         'display_name', st."display_name",
         'tier_name', st."tier_name",
         'features', st."features",
         'yearly_discount', st."yearly_discount"
       ))
  FROM "hasn_billing"."subscription_tier" st
 WHERE bp."offering_key" = 'llm:tier'
   AND bp."plan_key" = st."tier_name"
   AND bp."display_json" = '{}'::jsonb;

-- ============ 2) LLM 订阅档：年付 plan（<tier>_yearly） ============
UPDATE "hasn_billing"."billing_plan" bp
   SET "display_json" = jsonb_strip_nulls(jsonb_build_object(
         'display_name', st."display_name",
         'tier_name', st."tier_name",
         'features', st."features",
         'yearly_discount', st."yearly_discount"
       ))
  FROM "hasn_billing"."subscription_tier" st
 WHERE bp."offering_key" = 'llm:tier'
   AND bp."plan_key" = st."tier_name" || '_yearly'
   AND bp."display_json" = '{}'::jsonb;

-- ============ 3) 积分包 plan ============
UPDATE "hasn_billing"."billing_plan" bp
   SET "display_json" = jsonb_strip_nulls(jsonb_build_object(
         'display_name', cp."package_name",
         'package_name', cp."package_name",
         'description', cp."description"
       ))
  FROM "hasn_billing"."credit_package" cp
 WHERE bp."offering_key" = 'credits:topup'
   AND bp."plan_key" = cp."package_name"
   AND bp."display_json" = '{}'::jsonb;

-- ============ 4) 完整性守卫 ============
-- 存量 plan 若仍有空 display_json，说明搬迁没覆盖全，**此时不允许继续 drop 源表**。
-- 执行完本迁移请人工核对下面这条查询返回 0 行；drop 迁移里也有同口径的硬断言。
--
--   SELECT offering_key, plan_key
--     FROM hasn_billing.billing_plan
--    WHERE offering_key IN ('llm:tier', 'credits:topup')
--      AND display_json = '{}'::jsonb;
