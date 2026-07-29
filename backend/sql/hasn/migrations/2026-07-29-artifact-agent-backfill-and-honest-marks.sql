-- P2-8e：产物历史分身回填与 A15 诚实标记（设计 02 §8.1 / 实施 04 §11.1）。
--
-- 背景：历史当前态行 agent_hasn_id 为空，但参与记录带着真实分身（dev 实测 115 行全部可恢复）；
-- 第一阶段回填又对空分身行 COALESCE 出 'legacy_agent' 占位分身。设计 A15 要求：可恢复的历史必须
-- 按事实恢复；无法恢复的历史行 latest_contribution 留空 + meta_data.migration_lost_history=true，
-- UI 明示「参与记录不可考」，不得为了填满字段编造分身或动作。本迁移按此收敛：
--   1. 能从参与记录恢复分身的行 → 回填真实分身（最早一条非空分身参与记录 = 产出者）；
--   2. 回填后仍不可恢复的行 → 撤掉第一阶段编造的 legacy_agent 参与记录，latest_contribution
--      依 A15 合法留空（占位分身本身就是编造，留着会让 UI 展示一个不存在的发起者）；
--   3. 同行打 migration_lost_history=true，UI 据以明示「参与记录不可考」。
-- 三步均幂等，可重复执行。

-- 1. 可恢复分身回填：取该产物最早一条非空分身参与记录（产出者），只填空缺不覆盖已有值。
UPDATE "public"."hasn_artifacts" AS a
SET "agent_hasn_id" = recover."agent_hasn_id"
FROM (
    SELECT DISTINCT ON ("artifact_id") "artifact_id", "agent_hasn_id"
    FROM "public"."hasn_artifact_contributions"
    WHERE "agent_hasn_id" <> ''
    ORDER BY "artifact_id", "occurred_time" ASC, "contribution_id" ASC
) AS recover
WHERE a."artifact_id" = recover."artifact_id"
  AND (a."agent_hasn_id" IS NULL OR a."agent_hasn_id" = '');

-- 2. 撤掉第一阶段为空分身行编造的 legacy_agent 参与记录（仅限回填后仍无真实分身可恢复的行）。
DELETE FROM "public"."hasn_artifact_contributions" AS c
USING "public"."hasn_artifacts" AS a
WHERE c."artifact_id" = a."artifact_id"
  AND c."agent_hasn_id" = 'legacy_agent'
  AND c."idempotency_key" = 'legacy:' || a."artifact_id"
  AND (a."agent_hasn_id" IS NULL OR a."agent_hasn_id" = '')
  AND NOT EXISTS (
      SELECT 1
      FROM "public"."hasn_artifact_contributions" AS real
      WHERE real."artifact_id" = a."artifact_id"
        AND real."agent_hasn_id" NOT IN ('', 'legacy_agent')
  );

-- 3. 仍不可恢复的历史行打诚实标记（A15）：UI 读 meta_data.migration_lost_history 明示
--    「参与记录不可考」；已有标记不重复写。
UPDATE "public"."hasn_artifacts" AS a
SET "metadata" = COALESCE(a."metadata", '{}'::jsonb) || '{"migration_lost_history": true}'::jsonb
WHERE (a."agent_hasn_id" IS NULL OR a."agent_hasn_id" = '')
  AND NOT EXISTS (
      SELECT 1
      FROM "public"."hasn_artifact_contributions" AS c
      WHERE c."artifact_id" = a."artifact_id"
        AND c."agent_hasn_id" <> ''
  )
  AND COALESCE(a."metadata" ->> 'migration_lost_history', 'false') <> 'true';
