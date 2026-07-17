-- =====================================================
-- 会议副驾 v5：meeting_transcript_segments 转写记录定稿分段（会议结果域 §6.0.7）
-- 独立 PG schema=hasn_copilot。定稿分段是会议转写的云端权威快照——daemon 把本机实时转写
--   在会内/会后按 record_version 幂等上推（同 record_version+seq 覆盖），云端只存定稿结果。
-- 主键 UUID；meeting_id 逻辑指向 meetings.id（同 schema，不建物理 FK 以免跨 schema 约束耦合）。
-- 幂等键 (meeting_id, record_version, seq)：同版本同序号覆盖，避免重复上推产生重复分段。
-- 生成：uv run fba codegen generate --sql-file backend/sql/hasn_copilot/meeting_transcript_segments.sql --app hasn_copilot --schema hasn_copilot --execute
-- 设计事实源：docs/hasn-node设计文档/会议副驾/实施/03-v5原型1比1还原UI实施方案.md §6.0.7
-- =====================================================
CREATE SCHEMA IF NOT EXISTS "hasn_copilot";

CREATE TABLE "hasn_copilot"."meeting_transcript_segments" (
  "id"             uuid           PRIMARY KEY DEFAULT gen_random_uuid(),
  "meeting_id"     uuid           NOT NULL,
  "record_version" integer        NOT NULL,
  "seq"            integer        NOT NULL,
  "track"          varchar(16),
  "speaker_label"  varchar(64),
  "speaker_source" varchar(16),
  "text"           text           NOT NULL,
  "started_ms"     bigint         NOT NULL,
  "ended_ms"       bigint,
  "created_time"   timestamptz(6) NOT NULL DEFAULT now(),
  "updated_time"   timestamptz(6)
);

CREATE UNIQUE INDEX "idx_meeting_seg_ver_seq" ON "hasn_copilot"."meeting_transcript_segments" ("meeting_id", "record_version", "seq");

COMMENT ON TABLE  "hasn_copilot"."meeting_transcript_segments" IS '会议转写记录定稿分段（云端权威快照）';
COMMENT ON COLUMN "hasn_copilot"."meeting_transcript_segments"."id" IS '分段主键 ID（UUID）';
COMMENT ON COLUMN "hasn_copilot"."meeting_transcript_segments"."meeting_id" IS '所属会议 ID（逻辑指向 hasn_copilot.meetings.id）';
COMMENT ON COLUMN "hasn_copilot"."meeting_transcript_segments"."record_version" IS '所属转写记录定稿版本号（与 meetings.record_version 对齐）';
COMMENT ON COLUMN "hasn_copilot"."meeting_transcript_segments"."seq" IS '分段序号（本 record_version 内递增，幂等键之一）';
COMMENT ON COLUMN "hasn_copilot"."meeting_transcript_segments"."track" IS '采集轨 (system:系统声:blue/mic:麦克风:green)';
COMMENT ON COLUMN "hasn_copilot"."meeting_transcript_segments"."speaker_label" IS '说话人标签（如 说话人1）';
COMMENT ON COLUMN "hasn_copilot"."meeting_transcript_segments"."speaker_source" IS '说话人证据层级（推断来源，如 vad/cluster/manual）';
COMMENT ON COLUMN "hasn_copilot"."meeting_transcript_segments"."text" IS '定稿文本';
COMMENT ON COLUMN "hasn_copilot"."meeting_transcript_segments"."started_ms" IS '起始时间（相对会议起点毫秒）';
COMMENT ON COLUMN "hasn_copilot"."meeting_transcript_segments"."ended_ms" IS '结束时间（相对会议起点毫秒）';
COMMENT ON COLUMN "hasn_copilot"."meeting_transcript_segments"."created_time" IS '创建时间';
COMMENT ON COLUMN "hasn_copilot"."meeting_transcript_segments"."updated_time" IS '更新时间';
