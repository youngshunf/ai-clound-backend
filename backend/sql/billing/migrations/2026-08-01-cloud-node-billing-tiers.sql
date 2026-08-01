-- =====================================================
-- 云端常驻节点（cloud_node）计费档位落地
--
-- 主人 2026-08-01 拍板的**混合结构**：
--   1. `llm:tier` 五档 free/lite/pro/max/ultra 中 **pro 及以上各档各附赠 1 个**云端节点；
--      `free` 与 `lite` 都是 0 个、不给试用；
--   2. 超出附赠的部分走独立商品 `cloud:node` **按需加购**，¥128/月 · ¥1228/年
--      （与 `llm:tier` 的 pro 同价同折扣）。
--   最终配额 = LLM 档附赠数 ＋ cloud_node 权益里的加购数。
--
-- 本迁移做四件事：
--   A. 新增 offering `cloud:node` + 两个 plan（monthly / yearly）
--   B. 给 `llm:tier` 各档 `quota_json` 补 `max_cloud_nodes`（free/lite=0，pro 及以上=1）
--   C. **回填存量 `user_subscription.plan_snapshot`**——配额是购买时固化进快照的，
--      `cloud_node_service._tier_grant` 优先读快照，只改 `billing_plan` 对存量订阅无效；
--      漏了这一路 = 存量付费用户拿不到附赠、存量免费用户白拿一个节点。
--   D. 回填 `hasn_app_entitlement.quota_json`（cloud_node 权益的加购数快照列）
--
-- 幂等：全部 `ON CONFLICT DO NOTHING` / `WHERE 键不存在`，重跑命中 0 行。
-- 注意：`cloud_node` 与 `webapp:hosting` 是两个不同特征——前者托管主人的**分身节点**
--       （无头 hasn-node 容器），后者托管主人的**网页应用**（doc06 全栈托管）。切勿复用。
--
-- 事实源：docs/hasn-node设计文档/云端节点托管/实施/01-切片实施契约(H1-H8).md
--         docs/hasn-node设计文档/16-订阅与积分计费/02-统一商业化内核设计.md §8.1
-- =====================================================

-- ============ 0) 改动前影响面计数（部署时肉眼可见，别盲改） ============
DO $$
DECLARE
  v_offering     int;
  v_plan_total   int;
  v_plan_missing int;
  v_sub_total    int;
  v_sub_missing  int;
  v_ent_total    int;
  v_ent_missing  int;
BEGIN
  SELECT count(*) INTO v_offering
    FROM "hasn_billing"."billing_offering" WHERE "key" = 'cloud:node';

  SELECT count(*), count(*) FILTER (WHERE NOT ("quota_json" ? 'max_cloud_nodes'))
    INTO v_plan_total, v_plan_missing
    FROM "hasn_billing"."billing_plan" WHERE "offering_key" = 'llm:tier';

  SELECT count(*), count(*) FILTER (
           WHERE NOT (COALESCE(NULLIF("plan_snapshot", 'null'::jsonb), '{}'::jsonb) ? 'max_cloud_nodes')
         )
    INTO v_sub_total, v_sub_missing
    FROM "hasn_billing"."user_subscription";

  SELECT count(*), count(*) FILTER (WHERE NOT ("quota_json" ? 'max_cloud_nodes'))
    INTO v_ent_total, v_ent_missing
    FROM "public"."hasn_app_entitlement" WHERE "feature_key" = 'cloud_node';

  RAISE NOTICE '[改前] offering cloud:node 已存在 = % 行（0 = 待新建）', v_offering;
  RAISE NOTICE '[改前] llm:tier 档位 % 行，其中缺 max_cloud_nodes 待补 % 行', v_plan_total, v_plan_missing;
  RAISE NOTICE '[改前] user_subscription % 行，其中 plan_snapshot 缺 max_cloud_nodes 待回填 % 行', v_sub_total, v_sub_missing;
  RAISE NOTICE '[改前] cloud_node 权益 % 行，其中 quota_json 缺 max_cloud_nodes 待回填 % 行', v_ent_total, v_ent_missing;
END $$;

-- ============ A) 新商品：cloud:node（云端常驻节点，按需加购） ============
INSERT INTO "hasn_billing"."billing_offering"
  ("key", "kind", "feature_key", "display_name", "status", "source", "sort_order")
VALUES ('cloud:node', 'feature_plan', 'cloud_node', '云端常驻节点', 'active', 'platform', 40)
ON CONFLICT ("key") DO NOTHING;

-- 两个档：月付 ¥128 / 年付 ¥1228，与 llm:tier 的 pro 完全同价同折扣（yearly_discount 0.8）。
-- trial_json.enabled=false：主人拍板「不给试用」。
-- grace_json 对齐 llm:tier 现有档位的实测值（库里全是 '{}'），不自造宽限期。
-- quota_json.max_cloud_nodes=1：一份加购 = 1 个节点；买 N 份则累加进权益快照。
INSERT INTO "hasn_billing"."billing_plan"
  ("offering_key", "plan_key", "price_amount", "price_unit", "cycle",
   "quota_json", "trial_json", "grace_json", "display_json", "status", "sort_order")
VALUES
  ('cloud:node', 'monthly', 128.00, 'cny', 'month',
   '{"max_cloud_nodes": 1}'::jsonb,
   '{"enabled": false}'::jsonb,
   '{}'::jsonb,
   jsonb_build_object(
     'tier_name', 'cloud_node',
     'display_name', '云端常驻节点',
     'yearly_discount', 0.8,
     'features', jsonb_build_object(
       '节点数量', 1,
       '常驻在线', true,
       '独立容器', true,
       '数据卷备份', true
     )
   ),
   'active', 1),
  ('cloud:node', 'yearly', 1228.00, 'cny', 'year',
   '{"max_cloud_nodes": 1}'::jsonb,
   '{"enabled": false}'::jsonb,
   '{}'::jsonb,
   jsonb_build_object(
     'tier_name', 'cloud_node',
     'display_name', '云端常驻节点',
     'yearly_discount', 0.8,
     'features', jsonb_build_object(
       '节点数量', 1,
       '常驻在线', true,
       '独立容器', true,
       '数据卷备份', true
     )
   ),
   'active', 2)
ON CONFLICT ("offering_key", "plan_key") DO NOTHING;

-- ============ B) llm:tier 各档补 max_cloud_nodes（free/lite=0，pro 及以上=1） ============
-- 只填空缺（`NOT (quota_json ? 'max_cloud_nodes')`），已有值一律不覆盖——
-- 运营可能已为某个大客户单独调高，迁移无权抹掉。
-- 档位归属以 quota_json->>'tier' 为准（而非 plan_key），这样 free / <tier>_yearly 都能正确归位。
-- **零附赠档是 ('free','lite') 两档**：`lite`（¥49）是五档定档新增的低价档，主人明确只有
-- `pro` 及以上才附赠云端节点。写成「除 free 外全是 1」会让 lite 白得一个常驻容器；
-- 本迁移若在订阅线建出 lite 之后重跑，那一行就会被填成 1，故此处必须显式列出。
UPDATE "hasn_billing"."billing_plan"
   SET "quota_json" = jsonb_set(
         "quota_json", '{max_cloud_nodes}',
         to_jsonb(
           CASE WHEN COALESCE("quota_json"->>'tier', "plan_key") IN ('free', 'lite') THEN 0 ELSE 1 END
         )
       ),
       "updated_time" = now()
 WHERE "offering_key" = 'llm:tier'
   AND NOT ("quota_json" ? 'max_cloud_nodes');

-- ============ C) 回填存量订阅的 plan_snapshot（**关键：漏了存量用户拿不到附赠**） ============
-- `plan_snapshot` 是购买时固化的合同参数，服务层优先读它；只改 billing_plan 对存量订阅无效。
-- 判档以 `user_subscription.tier` 列为准（NOT NULL，恒有值，且与快照里的 tier 一致）：
--   tier IN ('free','lite') → 0；其余（pro/max/ultra 及重命名前的 advanced/flagship）→ 1。
-- 快照可能是 SQL NULL 或 JSON null（库里两种都有），先 COALESCE 成 '{}' 再 set，否则 jsonb_set 返回 NULL。
-- 含已过期 / 已终止的合同一并回填：口径统一，续订恢复时不必再补一次。
UPDATE "hasn_billing"."user_subscription"
   SET "plan_snapshot" = jsonb_set(
         COALESCE(NULLIF("plan_snapshot", 'null'::jsonb), '{}'::jsonb),
         '{max_cloud_nodes}',
         to_jsonb(CASE WHEN "tier" IN ('free', 'lite') THEN 0 ELSE 1 END)
       ),
       "updated_time" = now()
 WHERE NOT (COALESCE(NULLIF("plan_snapshot", 'null'::jsonb), '{}'::jsonb) ? 'max_cloud_nodes');

-- ============ D) 回填 cloud_node 权益的配额快照列 ============
-- `hasn_app_entitlement.quota_json` 是加购数的权威快照（`resolve_access` 直接透出给服务层求和）。
-- 一份 cloud:node 权益 = 1 个加购节点；买多份时由发放侧累加，此处只补「一份都没记」的空缺。
UPDATE "public"."hasn_app_entitlement"
   SET "quota_json" = jsonb_set("quota_json", '{max_cloud_nodes}', to_jsonb(1)),
       "updated_time" = now()
 WHERE "feature_key" = 'cloud_node'
   AND NOT ("quota_json" ? 'max_cloud_nodes');

-- ============ 9) 改动后复核（重跑本迁移时 待补/待回填 必须全为 0） ============
DO $$
DECLARE
  v_offering     int;
  v_plan_rows    int;
  v_plan_missing int;
  v_free_nodes   int;
  v_paid_min     int;
  v_sub_missing  int;
  v_sub_free_bad int;
  v_ent_missing  int;
BEGIN
  SELECT count(*) INTO v_offering
    FROM "hasn_billing"."billing_offering" WHERE "key" = 'cloud:node';

  SELECT count(*) INTO v_plan_rows
    FROM "hasn_billing"."billing_plan" WHERE "offering_key" = 'cloud:node';

  SELECT count(*) FILTER (WHERE NOT ("quota_json" ? 'max_cloud_nodes'))
    INTO v_plan_missing
    FROM "hasn_billing"."billing_plan" WHERE "offering_key" = 'llm:tier';

  SELECT count(*) INTO v_free_nodes
    FROM "hasn_billing"."billing_plan"
   WHERE "offering_key" = 'llm:tier'
     AND COALESCE("quota_json"->>'tier', "plan_key") IN ('free', 'lite')
     AND ("quota_json"->>'max_cloud_nodes')::int <> 0;

  SELECT COALESCE(min(("quota_json"->>'max_cloud_nodes')::int), -1) INTO v_paid_min
    FROM "hasn_billing"."billing_plan"
   WHERE "offering_key" = 'llm:tier'
     AND COALESCE("quota_json"->>'tier', "plan_key") NOT IN ('free', 'lite');

  SELECT count(*) FILTER (
           WHERE NOT (COALESCE(NULLIF("plan_snapshot", 'null'::jsonb), '{}'::jsonb) ? 'max_cloud_nodes')
         ),
         count(*) FILTER (WHERE "tier" IN ('free', 'lite') AND ("plan_snapshot"->>'max_cloud_nodes')::int <> 0)
    INTO v_sub_missing, v_sub_free_bad
    FROM "hasn_billing"."user_subscription";

  SELECT count(*) FILTER (WHERE NOT ("quota_json" ? 'max_cloud_nodes'))
    INTO v_ent_missing
    FROM "public"."hasn_app_entitlement" WHERE "feature_key" = 'cloud_node';

  RAISE NOTICE '[改后] offering cloud:node = % 行，其 plan = % 行（应为 1 / 2）', v_offering, v_plan_rows;
  RAISE NOTICE '[改后] llm:tier 仍缺 max_cloud_nodes = % 行（应为 0）；free/lite 档非 0 的 = % 行（应为 0）；pro 及以上最小值 = %（应为 1）',
    v_plan_missing, v_free_nodes, v_paid_min;
  RAISE NOTICE '[改后] user_subscription 仍缺 max_cloud_nodes = % 行（应为 0）；free/lite 订阅非 0 的 = % 行（应为 0）',
    v_sub_missing, v_sub_free_bad;
  RAISE NOTICE '[改后] cloud_node 权益仍缺 max_cloud_nodes = % 行（应为 0）', v_ent_missing;

  IF v_offering <> 1 OR v_plan_rows <> 2 OR v_plan_missing <> 0 OR v_free_nodes <> 0
     OR v_paid_min <> 1 OR v_sub_missing <> 0 OR v_sub_free_bad <> 0 OR v_ent_missing <> 0 THEN
    RAISE EXCEPTION '云端节点计费迁移复核未通过，请检查上方 NOTICE 明细';
  END IF;
END $$;
