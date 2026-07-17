-- =====================================================
-- 会议副驾 v5：meeting_minutes 纪要正文版本化（会议结果域 §6.0.7）
-- 独立 PG schema=hasn_copilot。纪要是会后结构化产物正文，按 version 版本化——分身重写纪要即写新版本，
--   meetings.minutes_version 指向当前版本。幂等键 (meeting_id, version) 保证同版本重复写入覆盖不重复。
-- 主键 UUID；meeting_id 逻辑指向 meetings.id（同 schema，不建物理 FK）。
-- 生成：uv run fba codegen generate --sql-file backend/sql/hasn_copilot/meeting_minutes.sql --app hasn_copilot --schema hasn_copilot --execute
-- 设计事实源：docs/hasn-node设计文档/会议副驾/实施/03-v5原型1比1还原UI实施方案.md §6.0.7
-- =====================================================
CREATE SCHEMA IF NOT EXISTS "hasn_copilot";

CREATE TABLE "hasn_copilot"."meeting_minutes" (
  "id"                  uuid           PRIMARY KEY DEFAULT gen_random_uuid(),
  "meeting_id"          uuid           NOT NULL,
  "version"             integer        NOT NULL,
  "body_md"             text           NOT NULL,
  "record_view_version" integer,
  "summary_turn_id"     varchar(64),
  "created_time"        timestamptz(6) NOT NULL DEFAULT now(),
  "updated_time"        timestamptz(6)
);

CREATE UNIQUE INDEX "idx_meeting_minutes_ver" ON "hasn_copilot"."meeting_minutes" ("meeting_id", "version");

COMMENT ON TABLE  "hasn_copilot"."meeting_minutes" IS '会议纪要正文版本化（云端权威）';
COMMENT ON COLUMN "hasn_copilot"."meeting_minutes"."id" IS '纪要版本主键 ID（UUID）';
COMMENT ON COLUMN "hasn_copilot"."meeting_minutes"."meeting_id" IS '所属会议 ID（逻辑指向 hasn_copilot.meetings.id）';
COMMENT ON COLUMN "hasn_copilot"."meeting_minutes"."version" IS '纪要版本号（幂等键之一；与 meetings.minutes_version 对齐）';
COMMENT ON COLUMN "hasn_copilot"."meeting_minutes"."body_md" IS '纪要正文（Markdown）';
COMMENT ON COLUMN "hasn_copilot"."meeting_minutes"."record_view_version" IS '生成此纪要时依据的转写记录视图版本（record_version 快照）';
COMMENT ON COLUMN "hasn_copilot"."meeting_minutes"."summary_turn_id" IS '生成此纪要的工作会话轮次 id（溯源）';
COMMENT ON COLUMN "hasn_copilot"."meeting_minutes"."created_time" IS '创建时间';
COMMENT ON COLUMN "hasn_copilot"."meeting_minutes"."updated_time" IS '更新时间';
