-- =====================================================
-- hasn_task.builtin_catalog 增 default_enabled / target_agent_type
-- 设计：docs/hasn-node设计文档/12-任务系统实施方案/08-内置定时任务体系与内置Agent设计.md §4.3
--
-- default_enabled：满足「有些默认开、有些需手动开」（INSERT 那一刻决定 task.enabled）。
-- target_agent_type：按 agent 类型适配（指向 marketplace_template.builtin_key）；NULL=绑主脑。
-- 幂等。
-- =====================================================
ALTER TABLE "hasn_task"."builtin_catalog"
  ADD COLUMN IF NOT EXISTS "default_enabled" boolean NOT NULL DEFAULT true;
ALTER TABLE "hasn_task"."builtin_catalog"
  ADD COLUMN IF NOT EXISTS "target_agent_type" varchar(64);

COMMENT ON COLUMN "hasn_task"."builtin_catalog"."default_enabled"
  IS '播种时默认启用态（false=需用户手动开启）';
COMMENT ON COLUMN "hasn_task"."builtin_catalog"."target_agent_type"
  IS '承接该任务的内置 agent 类型键(builtin_key)；NULL 表示绑定主脑';
