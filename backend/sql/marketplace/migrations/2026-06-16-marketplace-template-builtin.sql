-- =====================================================
-- marketplace_template 增 builtin / builtin_key（内置 agent 模板标志）
-- 设计：docs/hasn-node设计文档/12-任务系统实施方案/08-内置定时任务体系与内置Agent设计.md §4.2
--
-- builtin=true 表示注册时自动创建；builtin_key 是内置任务 target_agent_type 的匹配纽带。
-- 表已在 hasn_marketplace schema（2026-06-14 SET SCHEMA）。幂等。
-- =====================================================
ALTER TABLE "hasn_marketplace"."marketplace_template"
  ADD COLUMN IF NOT EXISTS "builtin" boolean NOT NULL DEFAULT false;
ALTER TABLE "hasn_marketplace"."marketplace_template"
  ADD COLUMN IF NOT EXISTS "builtin_key" varchar(64);

COMMENT ON COLUMN "hasn_marketplace"."marketplace_template"."builtin"
  IS '是否内置 agent 模板（注册时自动创建）';
COMMENT ON COLUMN "hasn_marketplace"."marketplace_template"."builtin_key"
  IS '内置 agent 类型键（内置任务 target_agent_type 按此匹配）';

-- builtin_key 全仓唯一（仅非空时约束）
CREATE UNIQUE INDEX IF NOT EXISTS "uq_marketplace_template_builtin_key"
  ON "hasn_marketplace"."marketplace_template" ("builtin_key")
  WHERE "builtin_key" IS NOT NULL;
