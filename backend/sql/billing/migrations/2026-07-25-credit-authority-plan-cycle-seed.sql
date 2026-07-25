-- =====================================================
-- doc94 F2：商品档位的周期参数统一为 30 天固定口径
--
-- `billing_plan.quota_json` 过去只有 `monthly_credits`，「一个月」到底是自然月还是 30 天
-- 全靠调用方各自解释。本迁移把周期参数写进商品合同参数本身：
--
--   {"credits_per_cycle": "1000.00000", "cycle_seconds": 2592000, "cycle_count": 12, "wallet_overflow": true}
--
-- - 积分用**十进制字符串**传输并存 NUMERIC(18,5)：JSON 浮点在跨语言序列化时会丢精度，
--   而 NewAPI 的 QuotaPerUnit=500000 决定 6 位小数根本不可整除；
-- - 月付 cycle_count=1、年付 12、免费档 null（无限期循环）；
-- - **售价与积分数量是两个独立商品字段**：`price_amount` 保持不动，
--   任何时候都不得用支付金额或汇率反推发放积分。
--
-- 幂等：可重复执行（只补齐缺失键，不覆盖已有值）。
-- =====================================================

-- 免费档：无限期循环，没有第 N 期后的合同终点，但仍有 30 天清零周期
UPDATE "hasn_billing"."billing_plan"
   SET "quota_json" = "quota_json"
     || jsonb_build_object(
          'credits_per_cycle', to_char(COALESCE(("quota_json" ->> 'monthly_credits')::numeric, 0), 'FM9999999990.00000'),
          'cycle_seconds', 2592000,
          'cycle_count', NULL,
          'wallet_overflow', true
        )
 WHERE "offering_key" = 'llm:tier'
   AND ("quota_json" ->> 'tier') = 'free'
   AND NOT ("quota_json" ? 'cycle_seconds');

-- 月付档：1 个 30 天周期（免费档已在上面单独处理，这里必须排除，否则会被写成 cycle_count=1）
UPDATE "hasn_billing"."billing_plan"
   SET "quota_json" = "quota_json"
     || jsonb_build_object(
          'credits_per_cycle', to_char(COALESCE(("quota_json" ->> 'monthly_credits')::numeric, 0), 'FM9999999990.00000'),
          'cycle_seconds', 2592000,
          'cycle_count', 1,
          'wallet_overflow', true
        )
 WHERE "offering_key" = 'llm:tier'
   AND "cycle" = 'month'
   AND ("quota_json" ->> 'tier') IS DISTINCT FROM 'free'
   AND NOT ("quota_json" ? 'cycle_seconds');

-- 年付档：12 个连续 30 天周期（360 天），每期额度与月付一致
UPDATE "hasn_billing"."billing_plan"
   SET "quota_json" = "quota_json"
     || jsonb_build_object(
          'credits_per_cycle', to_char(COALESCE(("quota_json" ->> 'monthly_credits')::numeric, 0), 'FM9999999990.00000'),
          'cycle_seconds', 2592000,
          'cycle_count', 12,
          'wallet_overflow', true
        )
 WHERE "offering_key" = 'llm:tier'
   AND "cycle" = 'year'
   AND NOT ("quota_json" ? 'cycle_seconds');

-- 积分包：一次性购买、永久有效。字段名是 `credits`（不是 credits_per_cycle），
-- 且**没有** cycle_seconds / cycle_count——订阅周期切换绝不得触碰永久钱包。
UPDATE "hasn_billing"."billing_plan"
   SET "quota_json" = "quota_json"
     || jsonb_build_object(
          'credits', to_char(COALESCE(("quota_json" ->> 'credits')::numeric, 0), 'FM9999999990.00000')
        )
 WHERE "offering_key" = 'credits:topup'
   AND "quota_json" ? 'credits';

-- 纠偏：免费档若被早期执行顺序写成有限周期数，强制改回「无限期循环」。
-- 免费档有 cycle_count 就意味着它会在第 N 期后终止，那不是免费档该有的语义。
UPDATE "hasn_billing"."billing_plan"
   SET "quota_json" = jsonb_set("quota_json", '{cycle_count}', 'null'::jsonb, true)
 WHERE "offering_key" = 'llm:tier'
   AND ("quota_json" ->> 'tier') = 'free'
   AND "quota_json" -> 'cycle_count' IS DISTINCT FROM 'null'::jsonb;
