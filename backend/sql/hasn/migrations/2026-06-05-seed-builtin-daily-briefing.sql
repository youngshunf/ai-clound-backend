-- =====================================================
-- 内置任务目录种子：每日关注简报（daily_briefing）
-- 官方维护的首个内置任务；daemon 拉取后绑主脑、cron 每日触发
-- 见设计文档 13-工作台/04 §3.3 / §5
-- 幂等：ON CONFLICT(builtin_key) DO UPDATE（只覆盖定义型字段）
-- =====================================================
INSERT INTO "public"."hasn_builtin_task_catalog"
    ("builtin_key", "name", "description", "schedule_type", "schedule_config",
     "skill_bundle", "system_prompt", "enabled", "revision")
VALUES (
    'daily_briefing',
    '每日关注简报',
    '每天由主脑分析主人的任务、社交、应用动态与计划，产出一份结构化的「今日关注」简报。',
    'cron',
    '{"expr":"0 8 * * *"}',
    'huanxing/workbench-briefing',
    E'你是主人的「主脑」分身，负责每天为主人盘点并产出一份《每日关注简报》。\n\n'
    '【任务】采集四个来源并归纳出主人今天最该关注/处理的事，以及可主动推进的计划：\n'
    '1) 任务执行情况（失败/进展/待确认）；2) 会话与社交往来（被拦截/联系请求/重要往来）；\n'
    '3) AI-Native 应用动态（如知识库、CRM 等只读 Tool）；4) 主人的计划与目标。\n\n'
    '【产出】你必须调用工具 `hasn.workbench.briefing.publish` 提交一份结构化 BriefingDocument，'
    '禁止把简报写成自由聊天文本。文档包含：summary（一句话总览）、focus_items[]（关注项：'
    'category=task|social|app|plan|risk、urgency=high|medium|low、title、summary、source.deep_link、'
    'evidence[]、actions[]）、plans[]（计划：title、horizon=today|week、steps[]、actions[]）。'
    'actions 每项 kind 取 open_app|run_task|open_route|dismiss。\n\n'
    '【零 fake 铁律】只写你真实采集到的事实；某个来源不可达就在简报里如实标注「该来源本次未接入」，'
    '绝不编造关注项或佐证。没有值得关注的事就产出空的 focus_items 并在 summary 里说明今天一切正常。',
    TRUE,
    1
)
ON CONFLICT ("builtin_key") DO UPDATE SET
    "name"            = EXCLUDED."name",
    "description"     = EXCLUDED."description",
    "schedule_type"   = EXCLUDED."schedule_type",
    "schedule_config" = EXCLUDED."schedule_config",
    "skill_bundle"    = EXCLUDED."skill_bundle",
    "system_prompt"   = EXCLUDED."system_prompt",
    "enabled"         = EXCLUDED."enabled",
    "revision"        = EXCLUDED."revision",
    "updated_time"    = now();
