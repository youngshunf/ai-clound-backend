-- 内置分身已收敛为 assistant、content_operator、analyst 三类，任务目录不得继续绑定已退役的
-- sales_advisor / planner。只修仍保持历史目标类型的官方内置任务，避免覆盖运营后续显式调整。
UPDATE hasn_task.builtin_catalog
SET
    target_agent_type = 'assistant',
    revision = revision + 1,
    updated_time = now()
WHERE builtin_key IN (
    'growth_daily_briefing',
    'growth_weekly_pipeline',
    'growth_opportunity_reminder',
    'growth_silent_revive'
)
  AND target_agent_type = 'sales_advisor';

UPDATE hasn_task.builtin_catalog
SET
    target_agent_type = 'assistant',
    revision = revision + 1,
    updated_time = now()
WHERE builtin_key IN (
    'plan_daily_autoschedule',
    'plan_evening_review',
    'plan_morning_briefing',
    'plan_weekly_review'
)
  AND target_agent_type = 'planner';
