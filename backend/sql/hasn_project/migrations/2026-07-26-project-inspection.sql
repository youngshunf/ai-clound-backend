-- C11：给已部署环境补齐项目巡检建议权威表。
-- 新表本体见 ../hasn_project_inspection.sql；本迁移可重复执行，不覆盖既有建议和处理状态。

CREATE SCHEMA IF NOT EXISTS "hasn_project";

CREATE TABLE IF NOT EXISTS "hasn_project"."hasn_project_inspection" (
  "id"                    uuid           PRIMARY KEY DEFAULT gen_random_uuid(),
  "owner_id"              varchar(40)    NOT NULL,
  "project_id"            uuid           NOT NULL REFERENCES "hasn_project"."hasn_project"("id") ON DELETE CASCADE,
  "agent_id"              varchar(40)    NOT NULL,
  "fingerprint"           varchar(128)   NOT NULL,
  "suggestion"            text           NOT NULL,
  "suggested_instruction" text,
  "status"                varchar(16)    NOT NULL DEFAULT 'unread',
  "inspected_time"        timestamptz(6) NOT NULL DEFAULT now(),
  "handled_time"          timestamptz(6),
  "work_session_id"       varchar(64),
  "plan_todo_id"          bigint,
  "created_time"          timestamptz(6) NOT NULL DEFAULT now(),
  "updated_time"          timestamptz(6),
  CONSTRAINT "uq_hasn_project_inspection_owner_project_fingerprint"
    UNIQUE ("owner_id", "project_id", "fingerprint"),
  CONSTRAINT "chk_hasn_project_inspection_status"
    CHECK ("status" IN ('unread', 'dispatched', 'dismissed', 'reminded'))
);

CREATE INDEX IF NOT EXISTS "idx_hasn_project_inspection_owner_project_status"
  ON "hasn_project"."hasn_project_inspection" ("owner_id", "project_id", "status", "inspected_time" DESC);
