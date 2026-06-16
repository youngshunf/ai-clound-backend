-- =====================================================
-- 回填存量 builtin_catalog 的 target_agent_type / default_enabled（内置任务体系 §3 M3）
-- 把已有内置任务从「全压主脑」纠正为「按类型绑对应内置 agent」。
--
-- daily_briefing：主脑回退（target NULL），人人默认开（default_enabled=true，enabled=true 不变）。
-- growth_*：绑销售顾问内置 agent（sales_advisor），默认关；仍 enabled=false（全局下线，
--           opt-in via 获客应用，seed_builtin_tasks 不会自动播种 enabled=false 的目录条目）。
-- creator_*：绑内容运营官内置 agent（content_operator），默认关；同样 enabled=false。
-- 仅回填新增的两列，不改 enabled，幂等。
-- =====================================================
UPDATE "hasn_task"."builtin_catalog"
   SET "target_agent_type" = NULL, "default_enabled" = TRUE, "updated_time" = now()
 WHERE "builtin_key" = 'daily_briefing';

UPDATE "hasn_task"."builtin_catalog"
   SET "target_agent_type" = 'sales_advisor', "default_enabled" = FALSE, "updated_time" = now()
 WHERE "builtin_key" IN ('growth_daily_briefing', 'growth_weekly_pipeline',
                         'growth_silent_revive', 'growth_opportunity_reminder');

UPDATE "hasn_task"."builtin_catalog"
   SET "target_agent_type" = 'content_operator', "default_enabled" = FALSE, "updated_time" = now()
 WHERE "builtin_key" IN ('creator_operate', 'creator_reflect', 'creator_topic_radar');
