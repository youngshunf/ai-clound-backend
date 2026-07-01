-- =====================================================
-- 回滚 V021：撤销 hasn_agents 的云端权威字段扩展
-- =====================================================

ALTER TABLE "public"."hasn_agents"
  DROP COLUMN IF EXISTS "tags",
  DROP COLUMN IF EXISTS "capability_set_id",
  DROP COLUMN IF EXISTS "persona_ref";

COMMENT ON COLUMN "public"."hasn_agents"."status"
  IS '状态 (active:活跃:green/disabled:已停用:orange/revoked:已吊销:red)';
