-- 修复已执行第一阶段迁移的环境：为历史当前态补一条不可变参与记录，保持统一查询可见。
-- 不读取或复制旧 metadata，确保本地绝对路径不会被重新写入云端。
INSERT INTO "public"."hasn_artifact_contributions" (
    "contribution_id",
    "artifact_id",
    "owner_hasn_id",
    "agent_hasn_id",
    "work_session_id",
    "project_id",
    "action",
    "source_kind",
    "source_tool",
    "source_app_id",
    "dispatch_id",
    "source_event_id",
    "idempotency_key",
    "conversation_id",
    "message_id",
    "occurred_time",
    "metadata"
)
SELECT
    'con_' || substring(md5('legacy:' || a."artifact_id") FROM 1 FOR 32),
    a."artifact_id",
    a."owner_hasn_id",
    COALESCE(NULLIF(a."agent_hasn_id", ''), 'legacy_agent'),
    a."session_id",
    a."project_id",
    CASE WHEN a."action" IN ('create', 'update') THEN a."action" ELSE 'create' END,
    CASE
        WHEN a."resource_uri" IS NOT NULL THEN 'app_write'
        WHEN a."local_locator_key" IS NOT NULL THEN 'runtime_file'
        WHEN a."source_kind" = 'agent_note' THEN 'agent_note'
        WHEN a."source_kind" = 'external_import' THEN 'external_import'
        ELSE 'platform_tool'
    END,
    a."source_tool",
    a."source_app_id",
    a."dispatch_id",
    a."origin_ref",
    'legacy:' || a."artifact_id",
    a."conversation_id",
    a."message_id",
    COALESCE(a."updated_time", a."created_time", NOW()),
    '{}'::JSONB
FROM "public"."hasn_artifacts" AS a
ON CONFLICT ("owner_hasn_id", "agent_hasn_id", "idempotency_key") DO NOTHING;
