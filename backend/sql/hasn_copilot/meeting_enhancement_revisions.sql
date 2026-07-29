-- =====================================================
-- 通用语音能力 V2：会议增强候选 revision（云端权威）
-- 独立 PG schema=hasn_copilot。原始实时稿由 meetings.realtime_revision_id 标识且永久保留；
--   每个增强候选以本表 UUID 作为 server_id，supersedes 显式指向来源 revision。
-- 生成：uv run fba codegen generate --sql-file backend/sql/hasn_copilot/meeting_enhancement_revisions.sql --app hasn_copilot --schema hasn_copilot --execute
-- 设计事实源：docs/hasn-node设计文档/通用语音能力/05-多能力模型编排与模型治理V2设计.md §7.3
-- =====================================================
CREATE SCHEMA IF NOT EXISTS "hasn_copilot";

ALTER TABLE "hasn_copilot"."meetings"
  ADD COLUMN IF NOT EXISTS "realtime_revision_id" uuid NOT NULL DEFAULT gen_random_uuid(),
  ADD COLUMN IF NOT EXISTS "preferred_enhancement_revision_id" uuid;

CREATE TABLE "hasn_copilot"."meeting_enhancement_revisions" (
  "id"                          uuid           PRIMARY KEY DEFAULT gen_random_uuid(),
  "meeting_id"                  uuid           NOT NULL,
  "owner_hasn_id"               varchar(64)    NOT NULL,
  "operation_id"                varchar(128)   NOT NULL,
  "revision_number"             integer        NOT NULL,
  "supersedes"                  uuid           NOT NULL,
  "status"                      varchar(32)    NOT NULL DEFAULT 'pending_confirmation',
  "source_record_version"       integer        NOT NULL,
  "transcript_json"             jsonb,
  "speaker_annotations_json"    jsonb,
  "alignment_json"              jsonb,
  "model_run_id"                varchar(128),
  "model_evidence_json"         jsonb          NOT NULL DEFAULT '{}',
  "created_by_agent_hasn_id"    varchar(64),
  "work_session_id"             varchar(64),
  "replaced_by"                 uuid,
  "decision_reason"             varchar(256),
  "decided_time"                timestamptz(6),
  "eviction_reason"             varchar(128),
  "evicted_time"                timestamptz(6),
  "created_time"                timestamptz(6) NOT NULL DEFAULT now(),
  "updated_time"                timestamptz(6),
  CONSTRAINT "chk_meeting_enhancement_revision_status"
    CHECK ("status" IN ('pending_confirmation', 'accepted', 'rejected', 'superseded', 'evicted'))
);

CREATE UNIQUE INDEX "idx_meeting_enhancement_revision_operation"
  ON "hasn_copilot"."meeting_enhancement_revisions" ("meeting_id", "operation_id");
CREATE UNIQUE INDEX "idx_meeting_enhancement_revision_number"
  ON "hasn_copilot"."meeting_enhancement_revisions" ("meeting_id", "revision_number");
CREATE UNIQUE INDEX "idx_meeting_enhancement_revision_pending"
  ON "hasn_copilot"."meeting_enhancement_revisions" ("meeting_id")
  WHERE "status" = 'pending_confirmation';
CREATE INDEX "idx_meeting_enhancement_revision_history"
  ON "hasn_copilot"."meeting_enhancement_revisions" ("meeting_id", "revision_number" DESC);
CREATE INDEX "idx_meeting_enhancement_revision_owner"
  ON "hasn_copilot"."meeting_enhancement_revisions" ("owner_hasn_id", "created_time" DESC);

COMMENT ON TABLE "hasn_copilot"."meeting_enhancement_revisions" IS '会议会后增强候选 revision（云端权威，含淘汰审计元数据）';
COMMENT ON COLUMN "hasn_copilot"."meeting_enhancement_revisions"."id" IS '候选权威 server_id（UUID；跨设备引用依据）';
COMMENT ON COLUMN "hasn_copilot"."meeting_enhancement_revisions"."meeting_id" IS '所属会议云端权威 ID';
COMMENT ON COLUMN "hasn_copilot"."meeting_enhancement_revisions"."owner_hasn_id" IS '归属主人 HASN ID（冗余隔离键，所有查询强制带）';
COMMENT ON COLUMN "hasn_copilot"."meeting_enhancement_revisions"."operation_id" IS 'daemon 稳定增强操作 ID（同会议幂等）';
COMMENT ON COLUMN "hasn_copilot"."meeting_enhancement_revisions"."revision_number" IS '会议内单调递增候选序号';
COMMENT ON COLUMN "hasn_copilot"."meeting_enhancement_revisions"."supersedes" IS '来源 revision 的云端权威 ID（原始实时稿或既有候选）';
COMMENT ON COLUMN "hasn_copilot"."meeting_enhancement_revisions"."status" IS '状态 (pending_confirmation:待主人确认:amber/accepted:已接受:green/rejected:已拒绝:gray/superseded:已被新候选替换:blue/evicted:已按保留策略淘汰:red)';
COMMENT ON COLUMN "hasn_copilot"."meeting_enhancement_revisions"."source_record_version" IS '生成候选所依据的原始实时稿 record_version';
COMMENT ON COLUMN "hasn_copilot"."meeting_enhancement_revisions"."transcript_json" IS '候选转写结果；淘汰后清空，仅保留审计元数据';
COMMENT ON COLUMN "hasn_copilot"."meeting_enhancement_revisions"."speaker_annotations_json" IS '候选说话人标注结果；可选输出失败时可为空';
COMMENT ON COLUMN "hasn_copilot"."meeting_enhancement_revisions"."alignment_json" IS '候选强制对齐结果；可选输出失败时可为空';
COMMENT ON COLUMN "hasn_copilot"."meeting_enhancement_revisions"."model_run_id" IS '本次联合或组合推理的 model_run_id';
COMMENT ON COLUMN "hasn_copilot"."meeting_enhancement_revisions"."model_evidence_json" IS '模型、组件版本、能力结果和错误的结构化证据';
COMMENT ON COLUMN "hasn_copilot"."meeting_enhancement_revisions"."created_by_agent_hasn_id" IS '参与创建候选的分身 HASN ID；纯语音引擎写入时为空';
COMMENT ON COLUMN "hasn_copilot"."meeting_enhancement_revisions"."work_session_id" IS '分身参与时绑定的工作会话 ID';
COMMENT ON COLUMN "hasn_copilot"."meeting_enhancement_revisions"."replaced_by" IS '替换当前待确认候选的新候选 server_id';
COMMENT ON COLUMN "hasn_copilot"."meeting_enhancement_revisions"."decision_reason" IS '主人拒绝或系统替换时的稳定原因';
COMMENT ON COLUMN "hasn_copilot"."meeting_enhancement_revisions"."decided_time" IS '主人接受或拒绝时间';
COMMENT ON COLUMN "hasn_copilot"."meeting_enhancement_revisions"."eviction_reason" IS '淘汰原因；首版固定 retention_limit';
COMMENT ON COLUMN "hasn_copilot"."meeting_enhancement_revisions"."evicted_time" IS '按保留策略淘汰时间';
COMMENT ON COLUMN "hasn_copilot"."meeting_enhancement_revisions"."created_time" IS '创建时间';
COMMENT ON COLUMN "hasn_copilot"."meeting_enhancement_revisions"."updated_time" IS '更新时间';
