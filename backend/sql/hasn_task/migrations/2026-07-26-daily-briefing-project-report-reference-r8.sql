-- =====================================================
-- daily_briefing r8：项目周报只作真实引用，不在每日简报中编造项目进展。
--
-- 每日简报分身须先读取项目权威摘要；只有读到本项目真实周报 document 时，才可引用其摘要和
-- hasn://artifact/{id}。没有周报、项目不可读或读失败都必须如实省略该项目进展，而不是补写猜测。
-- =====================================================

UPDATE "hasn_task"."builtin_catalog"
SET
    system_prompt = system_prompt || E'\n\n【项目周报引用】\n每日简报若要提及某项目的“本周进展”，必须先调用 `hasn.project.list` 与 `hasn.project.get` 读取该主人可见项目的权威 `reports`。仅当 `reports` 中存在真实周报时，才可在简报中引用该周报的 `summary`（为空则只说“已有周报”）及其 `resource_uri`（`hasn://artifact/{id}`），并明确这是哪一份报告；不得从项目名、里程碑、会话或猜测中拼造“本周进展”。项目没有周报、读取失败或无权读取时，不要声称有项目进展，只如实说明没有可引用的周报。',
    revision = 8,
    updated_time = now()
WHERE builtin_key = 'daily_briefing'
  AND revision < 8;
