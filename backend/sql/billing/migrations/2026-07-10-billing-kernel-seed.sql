-- =====================================================
-- 统一商业化内核 MK-1：存量定价 → 商品目录（offering + plan）种子迁移
-- 幂等：全部 INSERT ... ON CONFLICT DO NOTHING / UPDATE ... WHERE 空值，可重复执行
-- 收编三处散落定价：subscription_tier（LLM 订阅）/ credit_package（积分包）/ hasn_app_catalog（应用商业化字段）
-- 设计事实源：docs/hasn-node设计文档/16-订阅与积分计费/02-统一商业化内核设计.md §8.1（K0 拍板）
-- 施工权威：同模块 实施/92 MK-1
-- 说明：webapp:hosting feature_key 仅在 feature_registry 预定义，暂不 seed offering 行（doc06 全栈托管另立）
-- =====================================================

-- ============ 1) LLM 订阅：单 offering + 每档×周期一个 plan ============
INSERT INTO "hasn_billing"."billing_offering"
  ("key", "kind", "feature_key", "display_name", "status", "source", "sort_order")
VALUES ('llm:tier', 'llm_tier', 'llm:tier', 'LLM 订阅', 'active', 'platform', 10)
ON CONFLICT ("key") DO NOTHING;

-- 月付档（每个订阅档一条；配额快照固化 monthly_credits/max_agents/tier）
INSERT INTO "hasn_billing"."billing_plan"
  ("offering_key", "plan_key", "price_amount", "price_unit", "cycle", "quota_json", "trial_json", "grace_json", "status", "sort_order")
SELECT
  'llm:tier', st."tier_name", st."monthly_price", 'cny', 'month',
  jsonb_build_object('monthly_credits', st."monthly_credits", 'max_agents', st."max_agents", 'tier', st."tier_name"),
  '{}'::jsonb, '{}'::jsonb,
  CASE WHEN st."enabled" THEN 'active' ELSE 'inactive' END,
  COALESCE(st."sort_order", 0)
FROM "hasn_billing"."subscription_tier" st
WHERE st."monthly_price" IS NOT NULL
ON CONFLICT ("offering_key", "plan_key") DO NOTHING;

-- 年付档（仅有年价的档追加 <tier>_yearly plan）
INSERT INTO "hasn_billing"."billing_plan"
  ("offering_key", "plan_key", "price_amount", "price_unit", "cycle", "quota_json", "trial_json", "grace_json", "status", "sort_order")
SELECT
  'llm:tier', st."tier_name" || '_yearly', st."yearly_price", 'cny', 'year',
  jsonb_build_object('monthly_credits', st."monthly_credits", 'max_agents', st."max_agents", 'tier', st."tier_name"),
  '{}'::jsonb, '{}'::jsonb,
  CASE WHEN st."enabled" THEN 'active' ELSE 'inactive' END,
  COALESCE(st."sort_order", 0)
FROM "hasn_billing"."subscription_tier" st
WHERE st."yearly_price" IS NOT NULL AND st."yearly_price" > 0
ON CONFLICT ("offering_key", "plan_key") DO NOTHING;

-- ============ 2) 积分充值：单 offering + 每包一个 plan（once 一次买断） ============
INSERT INTO "hasn_billing"."billing_offering"
  ("key", "kind", "feature_key", "display_name", "status", "source", "sort_order")
VALUES ('credits:topup', 'credit_pack', 'credits:topup', '积分充值', 'active', 'platform', 20)
ON CONFLICT ("key") DO NOTHING;

INSERT INTO "hasn_billing"."billing_plan"
  ("offering_key", "plan_key", "price_amount", "price_unit", "cycle", "quota_json", "trial_json", "grace_json", "status", "sort_order")
SELECT
  'credits:topup', cp."package_name", cp."price", 'cny', 'once',
  jsonb_build_object('credits', cp."credits", 'bonus_credits', cp."bonus_credits"),
  '{}'::jsonb, '{}'::jsonb,
  CASE WHEN cp."enabled" THEN 'active' ELSE 'inactive' END,
  COALESCE(cp."sort_order", 0)
FROM "hasn_billing"."credit_package" cp
WHERE cp."price" IS NOT NULL
ON CONFLICT ("offering_key", "plan_key") DO NOTHING;

-- ============ 3) 应用商业化：每个有价应用一个 offering + standard plan + 回填 sku_ref ============
-- 只收编「有价」应用（price_amount > 0）；免费应用无需 offering
INSERT INTO "hasn_billing"."billing_offering"
  ("key", "kind", "feature_key", "display_name", "status", "source", "sort_order")
SELECT
  'app:' || c."app_id", 'app', 'app:' || c."app_id", c."name", 'active', 'platform', COALESCE(c."sort_order", 0)
FROM "public"."hasn_app_catalog" c
WHERE c."price_amount" IS NOT NULL AND c."price_amount" > 0
ON CONFLICT ("key") DO NOTHING;

INSERT INTO "hasn_billing"."billing_plan"
  ("offering_key", "plan_key", "price_amount", "price_unit", "cycle", "quota_json", "trial_json", "grace_json", "status", "sort_order")
SELECT
  'app:' || c."app_id", 'standard', c."price_amount", COALESCE(NULLIF(c."price_unit", ''), 'cny'),
  CASE c."billing_cycle"
    WHEN 'monthly' THEN 'month'
    WHEN 'yearly'  THEN 'year'
    WHEN 'month'   THEN 'month'
    WHEN 'year'    THEN 'year'
    ELSE 'once'
  END,
  '{}'::jsonb,
  jsonb_build_object('enabled', COALESCE(c."trial_days", 0) > 0, 'days', COALESCE(c."trial_days", 0)),
  '{}'::jsonb, 'active', COALESCE(c."sort_order", 0)
FROM "public"."hasn_app_catalog" c
WHERE c."price_amount" IS NOT NULL AND c."price_amount" > 0
ON CONFLICT ("offering_key", "plan_key") DO NOTHING;

-- 回填 catalog.sku_ref 指向 offering.key（读侧从 catalog 反查商品目录）
UPDATE "public"."hasn_app_catalog" c
  SET "sku_ref" = 'app:' || c."app_id"
WHERE c."price_amount" IS NOT NULL AND c."price_amount" > 0
  AND ("sku_ref" IS NULL OR "sku_ref" = '');
