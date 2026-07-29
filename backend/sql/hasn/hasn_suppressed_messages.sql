-- =====================================================
-- HASN Runtime 抑制箱 / owner 可拉取消息表（S1 codegen 输入 + S4 业务写入）
-- RuntimeUnavailable 只影响 dispatch_status，不影响 delivery_status。
-- =====================================================
CREATE TABLE "public"."hasn_suppressed_messages" (
  "id"              bigserial PRIMARY KEY,
  "message_id"      int8,
  "owner_id"        varchar(40) NOT NULL,
  "hasn_id"         varchar(40) NOT NULL,
  "conversation_id" uuid NOT NULL,
  "sender_hasn_id"  varchar(40),
  "idempotency_scope" varchar(64),
  "command_hash"    varchar(64),
  "command_payload" jsonb NOT NULL DEFAULT '{}',
  "suppress_reason" varchar(40) NOT NULL,
  "dispatch_status" varchar(30) NOT NULL DEFAULT 'runtime_unavailable',
  "policy_snapshot" jsonb NOT NULL DEFAULT '{}',
  "runtime_summary" jsonb NOT NULL DEFAULT '{}',
  "visible_to_owner" boolean NOT NULL DEFAULT true,
  "resolved_at"     timestamptz(6),
  "created_time"    timestamptz(6) NOT NULL DEFAULT now(),
  "updated_time"    timestamptz(6),
  CONSTRAINT "uq_hasn_suppressed_messages_message" UNIQUE ("message_id")
);

CREATE INDEX "idx_hasn_suppressed_owner" ON "public"."hasn_suppressed_messages" ("owner_id", "created_time" DESC);
CREATE INDEX "idx_hasn_suppressed_hasn" ON "public"."hasn_suppressed_messages" ("hasn_id", "created_time" DESC);
CREATE INDEX "idx_hasn_suppressed_conversation" ON "public"."hasn_suppressed_messages" ("conversation_id", "created_time" DESC);
CREATE INDEX "idx_hasn_suppressed_reason" ON "public"."hasn_suppressed_messages" ("suppress_reason", "created_time" DESC);
CREATE UNIQUE INDEX "uq_hasn_suppressed_idempotency_scope"
  ON "public"."hasn_suppressed_messages" ("idempotency_scope")
  WHERE "idempotency_scope" IS NOT NULL;

COMMENT ON TABLE "public"."hasn_suppressed_messages" IS 'HASN Runtime 抑制箱 / owner 可拉取消息表';
COMMENT ON COLUMN "public"."hasn_suppressed_messages"."id" IS '主键 ID';
COMMENT ON COLUMN "public"."hasn_suppressed_messages"."message_id" IS '放行后生成的权威消息 ID；待放行时为空';
COMMENT ON COLUMN "public"."hasn_suppressed_messages"."owner_id" IS 'Owner hasn_id';
COMMENT ON COLUMN "public"."hasn_suppressed_messages"."hasn_id" IS '被抑制的 Agent/Human inbox 主体 hasn_id';
COMMENT ON COLUMN "public"."hasn_suppressed_messages"."conversation_id" IS '原会话 ID（不得因 Runtime 状态分裂会话）';
COMMENT ON COLUMN "public"."hasn_suppressed_messages"."sender_hasn_id" IS '待放行命令的权威发送方 hasn_id';
COMMENT ON COLUMN "public"."hasn_suppressed_messages"."idempotency_scope" IS '发送方、来源节点与幂等键规范化后的 SHA-256';
COMMENT ON COLUMN "public"."hasn_suppressed_messages"."command_hash" IS '待放行命令规范化载荷 SHA-256，用于同键冲突检测';
COMMENT ON COLUMN "public"."hasn_suppressed_messages"."command_payload" IS '待放行的完整发送命令；放行时重新分配 conversation_seq';
COMMENT ON COLUMN "public"."hasn_suppressed_messages"."suppress_reason" IS '抑制原因 (runtime_unavailable:Runtime不可用:orange/adapter_missing:Adapter缺失:red/handle_unavailable:Handle不可用:orange/owner_confirmation_required:需Owner确认:purple/policy_suppressed:策略抑制:gray)';
COMMENT ON COLUMN "public"."hasn_suppressed_messages"."dispatch_status" IS 'Runtime 调度状态 (runtime_unavailable:Runtime不可用:orange/dispatch_failed:派发失败:red/suppressed_by_policy:策略抑制:purple/pending_runtime:等待Runtime:blue)';
COMMENT ON COLUMN "public"."hasn_suppressed_messages"."policy_snapshot" IS '策略快照摘要';
COMMENT ON COLUMN "public"."hasn_suppressed_messages"."runtime_summary" IS 'Runtime 脱敏摘要；禁止 workspace/endpoint/PID/CLI args/OAuth path';
COMMENT ON COLUMN "public"."hasn_suppressed_messages"."visible_to_owner" IS 'Owner 多端是否可见';
COMMENT ON COLUMN "public"."hasn_suppressed_messages"."resolved_at" IS '抑制状态解除时间';
COMMENT ON COLUMN "public"."hasn_suppressed_messages"."created_time" IS '创建时间';
COMMENT ON COLUMN "public"."hasn_suppressed_messages"."updated_time" IS '更新时间';
