-- =====================================================
-- public.hasn_agents 增 builtin_agent_key（内置 agent 类型键）
-- 设计：docs/hasn-node设计文档/12-任务系统实施方案/08-内置定时任务体系与内置Agent设计.md §4.5
--
-- 身份表 hasn_agents 留 public schema（继承 fba Base）。
-- 注册创建内置 agent 时，从模板把 builtin_key 写到此列，作为内置任务绑定算法的匹配字段
-- （比解析 template_id 干净）。非内置 agent 为 NULL。幂等。
-- =====================================================
ALTER TABLE "public"."hasn_agents"
  ADD COLUMN IF NOT EXISTS "builtin_agent_key" varchar(64);
COMMENT ON COLUMN "public"."hasn_agents"."builtin_agent_key"
  IS '内置 agent 类型键（来自模板 builtin_key）；非内置 agent 为 NULL';

CREATE INDEX IF NOT EXISTS "idx_hasn_agents_builtin_key"
  ON "public"."hasn_agents" ("owner_id", "builtin_agent_key");
