-- =====================================================
-- HASN 客户端上行 outbox 幂等/冲突表（S1 codegen 输入 + S4 业务写入）
-- =====================================================
CREATE TABLE "public"."hasn_sync_inbox_events" (
  "id"              bigserial PRIMARY KEY,
  "client_event_id" varchar(80) NOT NULL,
  "owner_id"        varchar(40) NOT NULL,
  "hasn_id"         varchar(40) NOT NULL,
  "node_id"         varchar(40) NOT NULL,
  "event_type"      varchar(50) NOT NULL,
  "payload"         jsonb NOT NULL DEFAULT '{}',
  "dedupe_key"      varchar(120),
  "status"          varchar(20) NOT NULL DEFAULT 'accepted',
  "server_revision" bigint,
  "conflict_reason" varchar(120),
  "attempt_count"   integer NOT NULL DEFAULT 0,
  "next_attempt_at" timestamptz(6),
  "locked_by"       varchar(64),
  "locked_at"       timestamptz(6),
  "last_error"      text,
  "applied_at"      timestamptz(6),
  "dead_at"         timestamptz(6),
  "received_at"     timestamptz(6) NOT NULL DEFAULT now(),
  "created_time"    timestamptz(6) NOT NULL DEFAULT now(),
  "updated_time"    timestamptz(6),
  CONSTRAINT "uq_hasn_sync_inbox_client_event" UNIQUE ("owner_id", "node_id", "client_event_id")
);

CREATE INDEX "idx_hasn_sync_inbox_owner_status" ON "public"."hasn_sync_inbox_events" ("owner_id", "status", "received_at" DESC);
CREATE INDEX "idx_hasn_sync_inbox_hasn" ON "public"."hasn_sync_inbox_events" ("hasn_id", "received_at" DESC);
CREATE INDEX "idx_hasn_sync_inbox_dedupe" ON "public"."hasn_sync_inbox_events" ("owner_id", "dedupe_key") WHERE "dedupe_key" IS NOT NULL;
CREATE INDEX "idx_hasn_sync_inbox_worker_claim" ON "public"."hasn_sync_inbox_events" ("status", "next_attempt_at", "locked_at", "received_at", "id");

COMMENT ON TABLE "public"."hasn_sync_inbox_events" IS 'HASN 客户端上行 outbox 幂等/冲突表';
COMMENT ON COLUMN "public"."hasn_sync_inbox_events"."id" IS '主键 ID';
COMMENT ON COLUMN "public"."hasn_sync_inbox_events"."client_event_id" IS '客户端事件 ID';
COMMENT ON COLUMN "public"."hasn_sync_inbox_events"."owner_id" IS '事件所属 Owner hasn_id';
COMMENT ON COLUMN "public"."hasn_sync_inbox_events"."hasn_id" IS '事件主体 hasn_id（Human 或 owned Agent）';
COMMENT ON COLUMN "public"."hasn_sync_inbox_events"."node_id" IS '上报 Node ID';
COMMENT ON COLUMN "public"."hasn_sync_inbox_events"."event_type" IS '事件类型 (ack:确认:green/read:已读:blue/edit:编辑:orange/recall:撤回:red/local_state:本地状态:gray)';
COMMENT ON COLUMN "public"."hasn_sync_inbox_events"."payload" IS '客户端上行载荷（不得包含 workspace/endpoint/PID/CLI args/OAuth path）';
COMMENT ON COLUMN "public"."hasn_sync_inbox_events"."dedupe_key" IS '业务幂等键';
COMMENT ON COLUMN "public"."hasn_sync_inbox_events"."status" IS '处理状态（accepted/processing/retry/applied/dead/conflict）';
COMMENT ON COLUMN "public"."hasn_sync_inbox_events"."server_revision" IS '对应服务端 revision';
COMMENT ON COLUMN "public"."hasn_sync_inbox_events"."conflict_reason" IS '冲突原因';
COMMENT ON COLUMN "public"."hasn_sync_inbox_events"."attempt_count" IS '业务应用尝试次数；每次领取原子加一';
COMMENT ON COLUMN "public"."hasn_sync_inbox_events"."next_attempt_at" IS '失败后的下次可领取时间';
COMMENT ON COLUMN "public"."hasn_sync_inbox_events"."locked_by" IS '当前领取该事件的 worker 实例 ID';
COMMENT ON COLUMN "public"."hasn_sync_inbox_events"."locked_at" IS '当前 worker 租约起始时间';
COMMENT ON COLUMN "public"."hasn_sync_inbox_events"."last_error" IS '最近一次业务应用失败摘要';
COMMENT ON COLUMN "public"."hasn_sync_inbox_events"."applied_at" IS '业务写已提交且 sync ACK 已落库的时间';
COMMENT ON COLUMN "public"."hasn_sync_inbox_events"."dead_at" IS '重试耗尽进入 dead 状态的时间';
COMMENT ON COLUMN "public"."hasn_sync_inbox_events"."received_at" IS '服务端接收时间';
COMMENT ON COLUMN "public"."hasn_sync_inbox_events"."created_time" IS '创建时间';
COMMENT ON COLUMN "public"."hasn_sync_inbox_events"."updated_time" IS '更新时间';
