-- =====================================================
-- 订阅五档「分身数量」权益上调：free 20 / lite 30 / pro 50 / max 100（ultra 仍为「不限」）
--
-- 本迁移会：
--   1. 幂等改写五档（月付 + 年付共 9 条 plan）的 quota_json.max_agents 与
--      display_json.features.分身数量 —— 两者必须同步改，否则 feature_registry 的
--      「卡片数字必须由配额派生」一致性守卫立刻报红；
--   2. 把**仍在生效**的存量合同（user_subscription）的 max_agents 与 plan_snapshot.max_agents
--      一并上调 —— 配额是购买时固化进合同的，只改目录对存量用户一行都不生效，
--      卡片写 20 而门禁仍按 1 拒绝，就成了空头承诺；
--   3. 事务内后置条件复核，防止目录与卡片再次漂移。
--
-- 只上调、不下调：仅当存量值落在**退役的旧默认值集合** `(1, 2, 5, 10)` 内时才回填，运营在 admin
-- 手工调过的其它值（如 7）不覆盖（与 2026-08-01-cloud-node-price-align-pro.sql 同一手法）。
-- 判据取「整个旧默认值集合」而不是「该档自己的旧默认值」，是因为实测存在 `tier='pro'` 却
-- `max_agents=1`（列默认值，建合同时没写档位配额）的漂移合同——按档对号入座会把它们漏在 1 上，
-- 卡片写 50、门禁拦 1。ultra 档同样回填：它的目标值是 `-1`（不限）。
--
-- ⚠️ 顺序依赖：本迁移必须在 2026-08-12-tier-five-plan-redefine.sql **之后**应用（按文件日期顺序
-- 跑全量即可）。那份定档迁移带 ON CONFLICT DO UPDATE 且自身可重跑，**单独重跑它会把本次上调
-- 覆盖回 1/2/5/10**——要重建目录时请两份按序连跑。
--
-- 事实源：docs/产品与技术/技术设计/02-平台能力/订阅、权益与计费/03-订阅档位与权益定档.md
-- =====================================================

DO $$
DECLARE
  v_plan_rows text;
  v_contract_rows int;
BEGIN
  SELECT string_agg(format('%s=%s', quota_json->>'tier', quota_json->>'max_agents'), ' ' ORDER BY sort_order)
    INTO v_plan_rows
    FROM hasn_billing.billing_plan
   WHERE offering_key = 'llm:tier'
     AND status = 'active'
     AND plan_key NOT LIKE '%\_yearly' ESCAPE '\';

  SELECT count(*) INTO v_contract_rows
    FROM hasn_billing.user_subscription
   WHERE status IN ('active', 'cancel_at_period_end')
     AND tier IN ('free', 'lite', 'pro', 'max', 'ultra')
     AND max_agents IN (1, 2, 5, 10);

  RAISE NOTICE '[改前] 月档配额 = %；待回填的生效合同 = % 条', v_plan_rows, v_contract_rows;
END $$;

-- ① 商品目录：配额与卡片同批改（月付 + 年付按 quota_json.tier 一起命中）。
UPDATE hasn_billing.billing_plan p
   SET quota_json = jsonb_set(p.quota_json, '{max_agents}', to_jsonb(m.new_agents)),
       display_json = COALESCE(p.display_json, '{}'::jsonb)
         || jsonb_build_object(
              'features',
              COALESCE(p.display_json->'features', '{}'::jsonb)
                || jsonb_build_object('分身数量', to_jsonb(m.new_agents))
            ),
       updated_time = now()
  FROM (VALUES
    ('free'::text,  20::int),
    ('lite',        30),
    ('pro',         50),
    ('max',        100)
  ) AS m(tier, new_agents)
 WHERE p.offering_key = 'llm:tier'
   AND p.quota_json->>'tier' = m.tier
   AND (
     (p.quota_json->>'max_agents')::int IS DISTINCT FROM m.new_agents
     OR p.display_json->'features'->'分身数量' IS DISTINCT FROM to_jsonb(m.new_agents)
   );

-- ② 存量合同的配额列：只回填仍停在退役旧默认值上的行（admin 手工调过的其它值不动）。
UPDATE hasn_billing.user_subscription s
   SET max_agents = m.new_agents,
       updated_time = now()
  FROM (VALUES
    ('free'::text,  20::int),
    ('lite',        30),
    ('pro',         50),
    ('max',        100),
    ('ultra',       -1)
  ) AS m(tier, new_agents)
 WHERE s.tier = m.tier
   AND s.status IN ('active', 'cancel_at_period_end')
   AND s.max_agents IN (1, 2, 5, 10)
   AND s.max_agents <> m.new_agents;

-- ③ 存量合同的快照：只改**显式写着旧值**的键，不给原本没有该键的快照凭空造字段（零 fake）。
-- 用 jsonb 直接比值而不是 `::int` 转换：快照是自由 JSON，遇到非数字值转换会整条语句报错。
UPDATE hasn_billing.user_subscription s
   SET plan_snapshot = jsonb_set(s.plan_snapshot, '{max_agents}', to_jsonb(m.new_agents)),
       updated_time = now()
  FROM (VALUES
    ('free'::text,  20::int),
    ('lite',        30),
    ('pro',         50),
    ('max',        100),
    ('ultra',       -1)
  ) AS m(tier, new_agents)
 WHERE s.tier = m.tier
   AND s.status IN ('active', 'cancel_at_period_end')
   AND s.plan_snapshot->'max_agents' IN ('1'::jsonb, '2'::jsonb, '5'::jsonb, '10'::jsonb)
   AND s.plan_snapshot->'max_agents' IS DISTINCT FROM to_jsonb(m.new_agents);

DO $$
DECLARE
  v_bad int;
  v_stale_contracts int;
BEGIN
  -- 复核只判「本迁移管的这五档对不对」，不判「一共有几档」：档位集合是 2026-08-12 定档迁移的
  -- 职责，在这里再写一个 `count = 9` 只会在将来正常加档时假红。
  -- 目录复核：五档（月付 + 年付）的配额与卡片数字必须都等于本次定档值。
  SELECT count(*) INTO v_bad
    FROM hasn_billing.billing_plan p
    JOIN (VALUES
      ('free'::text,  20::int),
      ('lite',        30),
      ('pro',         50),
      ('max',        100),
      ('ultra',       -1)
    ) AS m(tier, new_agents) ON p.quota_json->>'tier' = m.tier
   WHERE p.offering_key = 'llm:tier'
     AND p.status = 'active'
     AND (
       (p.quota_json->>'max_agents')::int IS DISTINCT FROM m.new_agents
       OR p.display_json->'features'->'分身数量' IS DISTINCT FROM CASE
            WHEN m.new_agents = -1 THEN to_jsonb('不限'::text)
            ELSE to_jsonb(m.new_agents)
          END
     );

  -- 合同复核：五档的生效合同不应再有停在退役旧默认值上的（admin 自定义值不在该集合内，不会落进这里）。
  SELECT count(*) INTO v_stale_contracts
    FROM hasn_billing.user_subscription s
   WHERE s.status IN ('active', 'cancel_at_period_end')
     AND s.tier IN ('free', 'lite', 'pro', 'max', 'ultra')
     AND s.max_agents IN (1, 2, 5, 10);

  IF v_bad <> 0 OR v_stale_contracts <> 0 THEN
    RAISE EXCEPTION '分身数量上调复核失败: payload_bad=% stale_contracts=%', v_bad, v_stale_contracts;
  END IF;
  RAISE NOTICE '[改后] 五档分身数量 = free 20 / lite 30 / pro 50 / max 100 / ultra 不限，目录与存量合同复核通过';
END $$;
