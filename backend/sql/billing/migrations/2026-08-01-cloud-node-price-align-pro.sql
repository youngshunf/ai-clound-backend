-- 云端常驻节点加购（`cloud:node`）定价改为与 `pro` 同价同折扣：¥99/月 · ¥950/年
--
-- 背景：原定 ¥128/月 · ¥1228/年，其定价锚点是**当时的** `pro=¥128`。五档定档把 `pro`
-- 改成 ¥99/¥950 后，「与 pro 同价」与「¥128」这两个理由就分叉了。主人 2026-08-01
-- 拍板取「与 pro 同价」这一条。
--
-- 为什么要单独一条迁移、而不是只改 `2026-08-01-cloud-node-billing-tiers.sql`：
-- 那条 seed 是 `ON CONFLICT DO NOTHING` 的幂等插入，**在已经跑过它的库上重跑命中 0 行**，
-- 光改源文件里的数值对存量库毫无作用（新建库才会拿到新价）。所以源文件改了以照顾新库，
-- 这里再补一条 UPDATE 把存量库校准过来。
--
-- `WHERE price_amount = <旧价>` 是刻意的三重保护：
--   1. 已经拿到新 seed（99/950）的库不会被重复写；
--   2. 运营后来在 admin 改过价的库不会被这条迁移悄悄按回去；
--   3. 重跑本迁移命中 0 行，保持幂等。
--
-- `display_json.yearly_discount` 不动：950 / (99 × 12) = 0.7997，仍是 0.8 档。
-- `quota_json` / `trial_json` 不动：一份加购仍是 1 个节点、仍不给试用。
--
-- 改价只影响**新购与续费**；已购周期内的权益行固化的是购买时的 plan 快照（doc02 §3.2）。

UPDATE "hasn_billing"."billing_plan"
SET "price_amount" = 99.00,
    "updated_time" = now()
WHERE "offering_key" = 'cloud:node'
  AND "plan_key" = 'monthly'
  AND "price_amount" = 128.00;

UPDATE "hasn_billing"."billing_plan"
SET "price_amount" = 950.00,
    "updated_time" = now()
WHERE "offering_key" = 'cloud:node'
  AND "plan_key" = 'yearly'
  AND "price_amount" = 1228.00;
