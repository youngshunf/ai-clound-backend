-- =====================================================
-- 云端常驻节点：**单节点内存上限**按订阅档落地（设计 §13 H9-b）
--
-- 背景：主人在云端节点的 WebUI 装了图坊/语音引擎之后，内存需求会跳一档。容器里的 daemon
-- 改不了自己的 cgroup 上限（Docker 在 create 时就钉死了），只能由 hosting-agent 从宿主侧
-- 重建容器来调。「能调到多大」是计费问题，主人 2026-08-02 拍板：**按订阅档给单节点内存上限**。
--
-- 与既有 `max_cloud_nodes` 的关键区别 —— 两者语义不同，求和方式也不同：
--   * `max_cloud_nodes` 是**数量**，混合结构下「档位附赠 ＋ 加购」**求和**；
--   * `max_node_memory_mb` 是**每个节点多大**的天花板，档位与加购之间取 **max 而不是求和**。
--     买两份加购不该让单个节点变成两倍大——那是买了两个节点，不是买了一个双倍大的节点。
--
-- 档位阶梯（free/lite 本就 0 个节点，写 0 表示「谈不上单节点多大」）：
--   free  = 0        lite  = 0
--   pro   = 4096MiB  （hosting-agent 默认档 2048 的两倍，够装下一个下发型引擎）
--   max   = 8192MiB
--   ultra = 16384MiB
--   cloud:node 加购 = 4096MiB（与 pro 同价同折扣，天花板也对齐 pro）
--
-- 这几个数字是**首版取值**，依据是「默认 2048 装不下一个引擎，翻倍够用」这一条工程判断，
-- 不是实测水位。等有了真实的引擎驻留内存分布再按数据调——调的时候只需重跑一条同形迁移，
-- 服务层不用动。
--
-- 本迁移做三件事（与 2026-08-01-cloud-node-billing-tiers.sql 同构）：
--   A. 给 `llm:tier` 各档 `quota_json` 补 `max_node_memory_mb`
--   B. **回填存量 `user_subscription.plan_snapshot`**——配额是购买时固化进快照的，
--      服务层优先读快照；漏了这一路 = 存量付费用户一个字节也调不上去
--   C. 回填 `hasn_app_entitlement.quota_json`（cloud_node 加购权益的天花板）
--
-- 幂等：全部 `WHERE 键不存在`，重跑命中 0 行；已有值一律不覆盖（运营可能已为大客户单独调高）。
--
-- 事实源：docs/hasn-node设计文档/云端节点托管/00-无头hasn-node托管总体设计.md §13 H9-b
-- =====================================================

-- ============ 0) 改动前影响面计数（部署时肉眼可见，别盲改） ============
DO $$
DECLARE
  v_plan_total   int;
  v_plan_missing int;
  v_sub_total    int;
  v_sub_missing  int;
  v_ent_total    int;
  v_ent_missing  int;
BEGIN
  SELECT count(*), count(*) FILTER (WHERE NOT ("quota_json" ? 'max_node_memory_mb'))
    INTO v_plan_total, v_plan_missing
    FROM "hasn_billing"."billing_plan" WHERE "offering_key" = 'llm:tier';

  SELECT count(*),
         count(*) FILTER (
           WHERE NOT (COALESCE(NULLIF("plan_snapshot", 'null'::jsonb), '{}'::jsonb) ? 'max_node_memory_mb')
         )
    INTO v_sub_total, v_sub_missing
    FROM "hasn_billing"."user_subscription";

  SELECT count(*), count(*) FILTER (WHERE NOT ("quota_json" ? 'max_node_memory_mb'))
    INTO v_ent_total, v_ent_missing
    FROM "public"."hasn_app_entitlement" WHERE "feature_key" = 'cloud_node';

  RAISE NOTICE '[改前] llm:tier 档位 % 行，其中缺 max_node_memory_mb 待补 % 行', v_plan_total, v_plan_missing;
  RAISE NOTICE '[改前] user_subscription % 行，其中 plan_snapshot 缺 max_node_memory_mb 待回填 % 行', v_sub_total, v_sub_missing;
  RAISE NOTICE '[改前] cloud_node 权益 % 行，其中 quota_json 缺 max_node_memory_mb 待回填 % 行', v_ent_total, v_ent_missing;
END $$;

-- ============ A) llm:tier 各档补 max_node_memory_mb ============
-- 档位归属以 quota_json->>'tier' 为准（而非 plan_key），这样 free / <tier>_yearly 都能正确归位；
-- 与既有 max_cloud_nodes 迁移保持完全一致的判档口径，避免两个键落在不同档上。
-- 未知档位（将来新增却忘了在此登记）落到 0 —— **fail closed，宁可调不上去也不白送内存**，
-- 与 cloud_node_service._tier_grant 的 TIER_GRANT_FALLBACK=0 同一取向。
UPDATE "hasn_billing"."billing_plan"
   SET "quota_json" = jsonb_set(
         "quota_json", '{max_node_memory_mb}',
         to_jsonb(
           CASE COALESCE("quota_json"->>'tier', "plan_key")
             WHEN 'pro'   THEN 4096
             WHEN 'max'   THEN 8192
             WHEN 'ultra' THEN 16384
             ELSE 0
           END
         )
       ),
       "updated_time" = now()
 WHERE "offering_key" = 'llm:tier'
   AND NOT ("quota_json" ? 'max_node_memory_mb');

-- ============ B) 回填存量订阅的 plan_snapshot（漏了存量付费用户一个字节也调不上去） ============
-- 判档以 `user_subscription.tier` 列为准（NOT NULL，恒有值）。
-- 重命名前的历史档名 advanced/flagship 一并归位，口径与 max_cloud_nodes 迁移一致
-- （那条把「非 free/lite」全判成 1，这里必须逐档给数，故显式列出历史名）。
UPDATE "hasn_billing"."user_subscription"
   SET "plan_snapshot" = jsonb_set(
         COALESCE(NULLIF("plan_snapshot", 'null'::jsonb), '{}'::jsonb),
         '{max_node_memory_mb}',
         to_jsonb(
           CASE "tier"
             WHEN 'pro'      THEN 4096
             WHEN 'max'      THEN 8192
             WHEN 'ultra'    THEN 16384
             WHEN 'advanced' THEN 4096    -- pro 的旧名
             WHEN 'flagship' THEN 8192    -- max 的旧名
             ELSE 0
           END
         )
       ),
       "updated_time" = now()
 WHERE NOT (COALESCE(NULLIF("plan_snapshot", 'null'::jsonb), '{}'::jsonb) ? 'max_node_memory_mb');

-- ============ C) 回填 cloud_node 加购权益的天花板 ============
-- 加购与 pro 同价同折扣，天花板对齐 pro。注意这个键在权益里是**天花板**不是数量，
-- 买多份不叠加（服务层取 max）——买两份是两个节点，不是一个双倍大的节点。
UPDATE "public"."hasn_app_entitlement"
   SET "quota_json" = jsonb_set("quota_json", '{max_node_memory_mb}', to_jsonb(4096)),
       "updated_time" = now()
 WHERE "feature_key" = 'cloud_node'
   AND NOT ("quota_json" ? 'max_node_memory_mb');

-- ============ 9) 改动后复核（重跑本迁移时 待补/待回填 必须全为 0） ============
DO $$
DECLARE
  v_plan_missing int;
  v_free_mem     int;
  v_pro_mem      int;
  v_sub_missing  int;
  v_ent_missing  int;
BEGIN
  SELECT count(*) FILTER (WHERE NOT ("quota_json" ? 'max_node_memory_mb'))
    INTO v_plan_missing
    FROM "hasn_billing"."billing_plan" WHERE "offering_key" = 'llm:tier';

  SELECT COALESCE(max(("quota_json"->>'max_node_memory_mb')::int), -1) INTO v_free_mem
    FROM "hasn_billing"."billing_plan"
   WHERE "offering_key" = 'llm:tier'
     AND COALESCE("quota_json"->>'tier', "plan_key") IN ('free', 'lite');

  SELECT COALESCE(min(("quota_json"->>'max_node_memory_mb')::int), -1) INTO v_pro_mem
    FROM "hasn_billing"."billing_plan"
   WHERE "offering_key" = 'llm:tier'
     AND COALESCE("quota_json"->>'tier', "plan_key") = 'pro';

  SELECT count(*) FILTER (
           WHERE NOT (COALESCE(NULLIF("plan_snapshot", 'null'::jsonb), '{}'::jsonb) ? 'max_node_memory_mb')
         )
    INTO v_sub_missing
    FROM "hasn_billing"."user_subscription";

  SELECT count(*) FILTER (WHERE NOT ("quota_json" ? 'max_node_memory_mb'))
    INTO v_ent_missing
    FROM "public"."hasn_app_entitlement" WHERE "feature_key" = 'cloud_node';

  RAISE NOTICE '[改后] llm:tier 仍缺 max_node_memory_mb % 行（应为 0）', v_plan_missing;
  RAISE NOTICE '[改后] free/lite 档上限 %（应为 0，本就没有节点）', v_free_mem;
  RAISE NOTICE '[改后] pro 档上限 %（应为 4096；-1 表示库里还没有 pro 档）', v_pro_mem;
  RAISE NOTICE '[改后] user_subscription 仍缺回填 % 行（应为 0）', v_sub_missing;
  RAISE NOTICE '[改后] cloud_node 权益仍缺回填 % 行（应为 0）', v_ent_missing;

  IF v_plan_missing > 0 OR v_sub_missing > 0 OR v_ent_missing > 0 THEN
    RAISE EXCEPTION '回填不完整：plan 缺 %，subscription 缺 %，entitlement 缺 %',
      v_plan_missing, v_sub_missing, v_ent_missing;
  END IF;
  IF v_free_mem > 0 THEN
    RAISE EXCEPTION 'free/lite 档拿到了非 0 的单节点内存上限（%），它们本就没有节点', v_free_mem;
  END IF;
END $$;
