-- =====================================================
-- 唤星订阅目录收敛为五档：free / lite / pro / max / ultra
--
-- 本迁移会：
--   1. 幂等改写五档月付和四档年付共 9 条 plan；
--   2. 将历史 advanced / flagship 及其年付档下架，保留可回滚数据；
--   3. 迁移存量合同和订单中的旧键引用；
--   4. 用事务内后置条件防止六档或权益数字漂移被静默写入。
--
-- 事实源：docs/产品与技术/技术设计/02-平台能力/订阅、权益与计费/03-订阅档位与权益定档.md
-- =====================================================

DO $$
DECLARE
  v_active_monthly int;
  v_legacy_refs int;
BEGIN
  SELECT count(*) INTO v_active_monthly
    FROM hasn_billing.billing_plan
   WHERE offering_key = 'llm:tier'
     AND status = 'active'
     AND plan_key NOT LIKE '%\_yearly' ESCAPE '\';

  SELECT
    (SELECT count(*) FROM hasn_billing.user_subscription WHERE tier IN ('advanced', 'flagship'))
    + (SELECT count(*) FROM hasn_billing.pay_order WHERE target_tier IN ('advanced', 'flagship'))
    INTO v_legacy_refs;

  RAISE NOTICE '[改前] llm:tier 上架月档 = % 条；旧档位引用 = % 条', v_active_monthly, v_legacy_refs;
END $$;

WITH tier_defs(
  plan_key, tier_key, display_name, price_amount, cycle, cycle_count,
  credits_per_cycle, storage_bytes, max_agents, max_cloud_nodes,
  max_node_memory_mb, storage_label, agents_label, support_label, sort_order
) AS (
  VALUES
    ('free',         'free',  '免费版', 0.00::numeric,    'month', NULL::int, '100.00000',   10737418240::bigint,   1,  0,     0, '10 GB',  '1'::text,  '社区支持', 0),
    ('lite',         'lite',  '轻享版', 49.00::numeric,   'month', 1,         '500.00000',   53687091200::bigint,   2,  0,     0, '50 GB',  '2',        '邮件支持', 1),
    ('lite_yearly',  'lite',  '轻享版', 470.00::numeric,  'year',  12,        '500.00000',   53687091200::bigint,   2,  0,     0, '50 GB',  '2',        '邮件支持', 1),
    ('pro',          'pro',   '专业版', 99.00::numeric,   'month', 1,         '1200.00000',  214748364800::bigint,  5,  1,  4096, '200 GB', '5',        '优先支持', 2),
    ('pro_yearly',   'pro',   '专业版', 950.00::numeric,  'year',  12,        '1200.00000',  214748364800::bigint,  5,  1,  4096, '200 GB', '5',        '优先支持', 2),
    ('max',          'max',   '高级版', 299.00::numeric,  'month', 1,         '4000.00000',  536870912000::bigint, 10,  1,  8192, '500 GB', '10',       '优先支持', 3),
    ('max_yearly',   'max',   '高级版', 2870.00::numeric, 'year',  12,        '4000.00000',  536870912000::bigint, 10,  1,  8192, '500 GB', '10',       '优先支持', 3),
    ('ultra',        'ultra', '旗舰版', 699.00::numeric,  'month', 1,         '10000.00000', 1099511627776::bigint, -1, 1, 16384, '1 TB',   '不限',     '专属客服', 4),
    ('ultra_yearly', 'ultra', '旗舰版', 6710.00::numeric, 'year',  12,        '10000.00000', 1099511627776::bigint, -1, 1, 16384, '1 TB',   '不限',     '专属客服', 4)
), payloads AS (
  SELECT
    plan_key,
    price_amount,
    cycle,
    jsonb_build_object(
      'tier', tier_key,
      'credits_per_cycle', credits_per_cycle,
      'cycle_seconds', 2592000,
      'cycle_count', cycle_count,
      'wallet_overflow', true,
      'storage_bytes', storage_bytes,
      'max_agents', max_agents,
      'max_cloud_nodes', max_cloud_nodes,
      'max_node_memory_mb', max_node_memory_mb
    ) AS quota_json,
    jsonb_build_object(
      'tier_name', tier_key,
      'display_name', display_name,
      'yearly_discount', 0.8,
      'features', jsonb_build_object(
        '每月积分', credits_per_cycle::numeric,
        '云存储', storage_label,
        '分身数量', CASE WHEN agents_label = '不限' THEN to_jsonb(agents_label) ELSE to_jsonb(agents_label::int) END,
        '客服支持', support_label
      )
    ) AS display_json,
    sort_order
  FROM tier_defs
)
INSERT INTO hasn_billing.billing_plan(
  offering_key, plan_key, price_amount, price_unit, cycle,
  quota_json, trial_json, grace_json, display_json, status, sort_order
)
SELECT
  'llm:tier', plan_key, price_amount, 'cny', cycle,
  quota_json, '{}'::jsonb, '{}'::jsonb, display_json, 'active', sort_order
FROM payloads
ON CONFLICT (offering_key, plan_key) DO UPDATE SET
  price_amount = EXCLUDED.price_amount,
  price_unit = EXCLUDED.price_unit,
  cycle = EXCLUDED.cycle,
  quota_json = hasn_billing.billing_plan.quota_json || EXCLUDED.quota_json,
  display_json = hasn_billing.billing_plan.display_json || EXCLUDED.display_json,
  status = 'active',
  sort_order = EXCLUDED.sort_order,
  updated_time = now();

UPDATE hasn_billing.billing_plan
   SET status = 'inactive', updated_time = now()
 WHERE offering_key = 'llm:tier'
   AND plan_key IN ('advanced', 'advanced_yearly', 'flagship', 'flagship_yearly')
   AND status <> 'inactive';

UPDATE hasn_billing.user_subscription
   SET tier = CASE tier WHEN 'advanced' THEN 'max' WHEN 'flagship' THEN 'ultra' ELSE tier END,
       plan_key = CASE plan_key
         WHEN 'advanced' THEN 'max'
         WHEN 'advanced_yearly' THEN 'max_yearly'
         WHEN 'flagship' THEN 'ultra'
         WHEN 'flagship_yearly' THEN 'ultra_yearly'
         ELSE plan_key
       END,
       plan_snapshot = CASE
         WHEN plan_snapshot IS NULL OR plan_snapshot = 'null'::jsonb THEN plan_snapshot
         ELSE jsonb_set(
           plan_snapshot,
           '{tier}',
           to_jsonb(CASE tier WHEN 'advanced' THEN 'max' WHEN 'flagship' THEN 'ultra' ELSE tier END)
         )
       END,
       updated_time = now()
 WHERE tier IN ('advanced', 'flagship')
    OR plan_key IN ('advanced', 'advanced_yearly', 'flagship', 'flagship_yearly');

UPDATE hasn_billing.pay_order
   SET target_tier = CASE target_tier WHEN 'advanced' THEN 'max' WHEN 'flagship' THEN 'ultra' ELSE target_tier END,
       offering_ref = CASE
         WHEN offering_ref IS NULL OR offering_ref = 'null'::jsonb THEN offering_ref
         WHEN offering_ref->>'plan_key' IN ('advanced', 'advanced_yearly', 'flagship', 'flagship_yearly')
           THEN jsonb_set(
             offering_ref,
             '{plan_key}',
             to_jsonb(
               CASE offering_ref->>'plan_key'
                 WHEN 'advanced' THEN 'max'
                 WHEN 'advanced_yearly' THEN 'max_yearly'
                 WHEN 'flagship' THEN 'ultra'
                 WHEN 'flagship_yearly' THEN 'ultra_yearly'
               END
             )
           )
         ELSE offering_ref
       END,
       updated_time = now()
 WHERE target_tier IN ('advanced', 'flagship')
    OR offering_ref->>'plan_key' IN ('advanced', 'advanced_yearly', 'flagship', 'flagship_yearly');

DO $$
DECLARE
  v_active_keys text[];
  v_legacy_active int;
  v_legacy_refs int;
  v_payload_bad int;
BEGIN
  SELECT array_agg(plan_key ORDER BY plan_key) INTO v_active_keys
    FROM hasn_billing.billing_plan
   WHERE offering_key = 'llm:tier' AND status = 'active';

  SELECT count(*) INTO v_legacy_active
    FROM hasn_billing.billing_plan
   WHERE offering_key = 'llm:tier'
     AND status = 'active'
     AND plan_key IN ('advanced', 'advanced_yearly', 'flagship', 'flagship_yearly');

  SELECT
    (SELECT count(*) FROM hasn_billing.user_subscription
      WHERE tier IN ('advanced', 'flagship')
         OR plan_key IN ('advanced', 'advanced_yearly', 'flagship', 'flagship_yearly'))
    + (SELECT count(*) FROM hasn_billing.pay_order
      WHERE target_tier IN ('advanced', 'flagship')
         OR offering_ref->>'plan_key' IN ('advanced', 'advanced_yearly', 'flagship', 'flagship_yearly'))
    INTO v_legacy_refs;

  SELECT count(*) INTO v_payload_bad
    FROM hasn_billing.billing_plan
   WHERE offering_key = 'llm:tier'
     AND status = 'active'
     AND (
       quota_json->>'tier' <> replace(plan_key, '_yearly', '')
       OR (quota_json->>'cycle_seconds')::int <> 2592000
       OR quota_json->>'wallet_overflow' <> 'true'
       OR (display_json->'features'->>'每月积分')::numeric <> (quota_json->>'credits_per_cycle')::numeric
       OR (display_json->'features'->>'分身数量') <> CASE
         WHEN (quota_json->>'max_agents')::int = -1 THEN '不限'
         ELSE quota_json->>'max_agents'
       END
     );

  IF v_active_keys <> ARRAY[
    'free', 'lite', 'lite_yearly', 'max', 'max_yearly',
    'pro', 'pro_yearly', 'ultra', 'ultra_yearly'
  ]::text[] OR v_legacy_active <> 0 OR v_legacy_refs <> 0 OR v_payload_bad <> 0 THEN
    RAISE EXCEPTION '五档订阅迁移复核失败: active_keys=% legacy_active=% legacy_refs=% payload_bad=%',
      v_active_keys, v_legacy_active, v_legacy_refs, v_payload_bad;
  END IF;
  RAISE NOTICE '[改后] 五档订阅目录与旧键引用复核通过';
END $$;
