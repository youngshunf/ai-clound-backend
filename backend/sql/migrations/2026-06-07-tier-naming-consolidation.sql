-- =====================================================
-- B0''：tier 命名归一（桌面端订阅与积分计费 §4.3）
-- =====================================================
-- 权威枚举（app_code='huanxing'）：
--   free（微星） / pro（明星） / advanced（恒星） / flagship（超新星）
-- 事实源：实库 subscription_tier(app_code='huanxing') + 官网兜底 Pricing.tsx 一致。
--
-- 消除的三处历史漂移（均为注释/旧 seed，非实库数据）：
--   1) CreatePayOrderParam.tier 注释 star_glow/star_shine/star_glory（已改）
--   2) app/pay/model/pay_contract.py 注释 star_*（已改）
--   3) init_subscription_data.sql 旧 seed free/basic/pro/enterprise→free/pro/max/ultra（通用模板，非 huanxing）
--
-- 本迁移：
--   A. 幂等 upsert 权威 huanxing 套餐（版本控制内固化权威枚举；存量已一致→无害刷新）
--   B. app_code 严格隔离的安全网：把任何历史遗留 tier 值映射到权威枚举，
--      **绝不触碰 zhixiaoya**（其合法使用 free/pro/max/ultra）。
--
-- 幂等：可重复执行。存量 huanxing 数据已全部为权威值，B 段对当前数据是 no-op。
-- =====================================================

BEGIN;

-- ───────────────────────── A. 权威 huanxing 套餐 upsert ─────────────────────────
-- 唯一约束 uq_subscription_tier_name_app (tier_name, app_code)。
INSERT INTO subscription_tier
    (tier_name, app_code, display_name, monthly_credits, monthly_price, yearly_price, yearly_discount, max_agents, enabled, sort_order, created_time)
VALUES
    ('free',     'huanxing', '微星',   100,   0,    NULL,  NULL, 1,  true, 1, NOW()),
    ('pro',      'huanxing', '明星',   1000,  128,  1228,  0.8,  2,  true, 2, NOW()),
    ('advanced', 'huanxing', '恒星',   5000,  238,  2284,  0.8,  5,  true, 3, NOW()),
    ('flagship', 'huanxing', '超新星', 50000, 598,  5740,  0.8,  10, true, 4, NOW())
ON CONFLICT (tier_name, app_code) DO UPDATE SET
    display_name    = EXCLUDED.display_name,
    monthly_price   = EXCLUDED.monthly_price,
    yearly_price    = EXCLUDED.yearly_price,
    yearly_discount = EXCLUDED.yearly_discount,
    max_agents      = EXCLUDED.max_agents,
    enabled         = EXCLUDED.enabled,
    sort_order      = EXCLUDED.sort_order,
    updated_time    = NOW();

-- ───────────────────── B. 安全网：历史遗留值 → 权威枚举（app_code 隔离） ─────────────────────
-- 映射表（仅 huanxing 语义遗留值）：
--   basic / star_glow  → pro
--   max   / star_shine → advanced
--   enterprise / star_glory → flagship
-- pro/free 在新旧枚举同名，无需映射。

-- B.1 user_subscription（有 app_code 列）
UPDATE user_subscription SET tier = CASE tier
        WHEN 'basic'      THEN 'pro'
        WHEN 'star_glow'  THEN 'pro'
        WHEN 'max'        THEN 'advanced'
        WHEN 'star_shine' THEN 'advanced'
        WHEN 'enterprise' THEN 'flagship'
        WHEN 'star_glory' THEN 'flagship'
        ELSE tier END
WHERE app_code = 'huanxing'
  AND tier IN ('basic', 'star_glow', 'max', 'star_shine', 'enterprise', 'star_glory');

-- B.2 pay_order.target_tier（按 extra_data->>'app_code' 过滤；无 app_code 默认 huanxing）
UPDATE pay_order SET target_tier = CASE target_tier
        WHEN 'basic'      THEN 'pro'
        WHEN 'star_glow'  THEN 'pro'
        WHEN 'max'        THEN 'advanced'
        WHEN 'star_shine' THEN 'advanced'
        WHEN 'enterprise' THEN 'flagship'
        WHEN 'star_glory' THEN 'flagship'
        ELSE target_tier END
WHERE order_type IN ('subscribe', 'upgrade', 'auto_renew')
  AND COALESCE(extra_data->>'app_code', 'huanxing') = 'huanxing'
  AND target_tier IN ('basic', 'star_glow', 'max', 'star_shine', 'enterprise', 'star_glory');

-- B.3 pay_contract.tier（无 app_code 列 → 仅映射 star_* 不歧义的唤星历史枚举；
-- basic/max/enterprise 在 pay_contract 留作 zhixiaoya 可能合法值，不动）。
UPDATE pay_contract SET tier = CASE tier
        WHEN 'star_glow'  THEN 'pro'
        WHEN 'star_shine' THEN 'advanced'
        WHEN 'star_glory' THEN 'flagship'
        ELSE tier END
WHERE tier IN ('star_glow', 'star_shine', 'star_glory');

COMMIT;
