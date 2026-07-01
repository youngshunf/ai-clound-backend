-- =====================================================
-- 给 hasn_agents 加云端权威字段，使 daemon 完全镜像云端
--
-- 1) tags             jsonb 数组，默认 []
-- 2) capability_set_id varchar(80)，可空
-- 3) persona_ref       varchar(120)，可空
-- 4) status 值集扩展：原 active/disabled/revoked + archived/deleted（生命周期态）
--
-- 背景：daemon 的 update_agent / delete_agent(archive) 改成"云端先落库"，
-- 上述字段必须在 hasn_agents 表中可写，daemon 不再做本地独占写。
-- =====================================================

ALTER TABLE "public"."hasn_agents"
  ADD COLUMN IF NOT EXISTS "tags" jsonb NOT NULL DEFAULT '[]'::jsonb,
  ADD COLUMN IF NOT EXISTS "capability_set_id" varchar(80),
  ADD COLUMN IF NOT EXISTS "persona_ref" varchar(120);

COMMENT ON COLUMN "public"."hasn_agents"."tags"
  IS 'Agent 标签数组（云端权威，daemon 仅镜像）';
COMMENT ON COLUMN "public"."hasn_agents"."capability_set_id"
  IS 'Agent 能力集 ID（与 hasn_agent_capabilities 关联，云端权威）';
COMMENT ON COLUMN "public"."hasn_agents"."persona_ref"
  IS 'Agent persona 引用（template / persona 资产 ID，云端权威）';

COMMENT ON COLUMN "public"."hasn_agents"."status"
  IS '状态/生命周期 (active:活跃:green/disabled:已停用:orange/revoked:已吊销:red/archived:已归档:gray/deleted:已删除:gray)';
