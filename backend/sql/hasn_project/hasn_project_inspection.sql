-- =====================================================
-- 平台项目巡检建议（模块 14 doc38 C11，schema=hasn_project）
-- 项目经理分身针对既有项目发布的可操作建议；建议本身是权威业务记录，不是临时 UI 状态。
-- 幂等键为 (owner_id, project_id, fingerprint)：同一项目同一巡检结论重放时更新同一条建议，禁止重复卡片。
-- 生成：uv run fba codegen generate --sql-file backend/sql/hasn_project/hasn_project_inspection.sql --app hasn_project --schema hasn_project --execute
-- =====================================================
CREATE SCHEMA IF NOT EXISTS "hasn_project";

CREATE TABLE "hasn_project"."hasn_project_inspection" (
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

CREATE INDEX "idx_hasn_project_inspection_owner_project_status"
  ON "hasn_project"."hasn_project_inspection" ("owner_id", "project_id", "status", "inspected_time" DESC);

COMMENT ON TABLE "hasn_project"."hasn_project_inspection" IS '平台项目巡检建议（项目经理分身发布的权威建议记录，doc38 C11）';
COMMENT ON COLUMN "hasn_project"."hasn_project_inspection"."id" IS '巡检建议云端权威 UUID';
COMMENT ON COLUMN "hasn_project"."hasn_project_inspection"."owner_id" IS '归属主人 HASN ID（owner 隔离键）';
COMMENT ON COLUMN "hasn_project"."hasn_project_inspection"."project_id" IS '所属平台项目云端权威 UUID';
COMMENT ON COLUMN "hasn_project"."hasn_project_inspection"."agent_id" IS '发布巡检建议的项目经理分身 HASN ID';
COMMENT ON COLUMN "hasn_project"."hasn_project_inspection"."fingerprint" IS '建议幂等指纹（同 owner/项目重放不重复插入）';
COMMENT ON COLUMN "hasn_project"."hasn_project_inspection"."suggestion" IS '给主人展示的巡检建议正文';
COMMENT ON COLUMN "hasn_project"."hasn_project_inspection"."suggested_instruction" IS '按建议派发时预填给分身的执行指令';
COMMENT ON COLUMN "hasn_project"."hasn_project_inspection"."status" IS '状态 (unread:未读:violet/dispatched:已派发:blue/dismissed:已忽略:gray/reminded:已提醒:amber)';
COMMENT ON COLUMN "hasn_project"."hasn_project_inspection"."inspected_time" IS '分身完成本次巡检的时间';
COMMENT ON COLUMN "hasn_project"."hasn_project_inspection"."handled_time" IS '主人处理建议的时间';
COMMENT ON COLUMN "hasn_project"."hasn_project_inspection"."work_session_id" IS '按建议派发后回填的工作会话 ID（逻辑引用 public.hasn_sessions）';
COMMENT ON COLUMN "hasn_project"."hasn_project_inspection"."plan_todo_id" IS '提醒今晚后回填的计划待办 ID（逻辑引用 hasn_plan.todo）';
COMMENT ON COLUMN "hasn_project"."hasn_project_inspection"."created_time" IS '创建时间';
COMMENT ON COLUMN "hasn_project"."hasn_project_inspection"."updated_time" IS '更新时间';
