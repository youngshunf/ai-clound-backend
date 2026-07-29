-- 生成：uv run fba codegen generate --sql-file backend/sql/notification/hasn_notification_im_command_outbox.sql --app notification --execute
CREATE TABLE "public"."hasn_notification_im_command_outbox" (
    "id" bigserial NOT NULL,
    "command_id" varchar(40) NOT NULL,
    "producer" varchar(40) NOT NULL,
    "conversation_id" uuid NOT NULL,
    "command_type" varchar(64) NOT NULL,
    "payload" jsonb NOT NULL,
    "payload_hash" char(64) NOT NULL,
    "idempotency_key" varchar(160) NOT NULL,
    "status" varchar(16) NOT NULL DEFAULT 'pending',
    "attempt_count" integer NOT NULL DEFAULT 0,
    "next_attempt_at" timestamptz NOT NULL DEFAULT now(),
    "lease_until" timestamptz,
    "locked_by" varchar(160),
    "last_error" text,
    "message_id" bigint,
    "trace_id" varchar(80),
    "causation_id" varchar(80),
    "completed_at" timestamptz,
    "created_time" timestamptz NOT NULL DEFAULT now(),
    "updated_time" timestamptz NOT NULL DEFAULT now(),
    CONSTRAINT "pk_hasn_notification_im_command_outbox" PRIMARY KEY ("id"),
    CONSTRAINT "uq_hasn_notification_im_command_outbox_command_id"
        UNIQUE ("command_id"),
    CONSTRAINT "uq_hasn_notification_im_command_outbox_idempotency"
        UNIQUE ("idempotency_key"),
    CONSTRAINT "ck_hasn_notification_im_command_outbox_producer"
        CHECK ("producer" = 'notification'),
    CONSTRAINT "ck_hasn_notification_im_command_outbox_type"
        CHECK ("command_type" = 'send_message'),
    CONSTRAINT "ck_hasn_notification_im_command_outbox_status"
        CHECK ("status" IN ('pending', 'processing', 'completed', 'dead_letter')),
    CONSTRAINT "ck_hasn_notification_im_command_outbox_attempt_count"
        CHECK ("attempt_count" >= 0)
);

COMMENT ON TABLE "public"."hasn_notification_im_command_outbox" IS
    '通知业务状态触发 IM 卡片的事务命令队列';
COMMENT ON COLUMN "public"."hasn_notification_im_command_outbox"."command_id" IS
    '命令公开标识';
COMMENT ON COLUMN "public"."hasn_notification_im_command_outbox"."producer" IS
    '生产方固定标识 notification';
COMMENT ON COLUMN "public"."hasn_notification_im_command_outbox"."conversation_id" IS
    'ensure 后取得的权威会话 ID';
COMMENT ON COLUMN "public"."hasn_notification_im_command_outbox"."command_type" IS
    '命令类型，当前仅 send_message';
COMMENT ON COLUMN "public"."hasn_notification_im_command_outbox"."payload" IS
    '版本化的认证主体与发送命令 JSON';
COMMENT ON COLUMN "public"."hasn_notification_im_command_outbox"."payload_hash" IS
    '规范化命令载荷 SHA-256，用于同键异载荷冲突检测';
COMMENT ON COLUMN "public"."hasn_notification_im_command_outbox"."idempotency_key" IS
    '跨 relay 重试稳定的 IM 幂等键';
COMMENT ON COLUMN "public"."hasn_notification_im_command_outbox"."status" IS
    '投递状态：pending/processing/completed/dead_letter';
COMMENT ON COLUMN "public"."hasn_notification_im_command_outbox"."attempt_count" IS
    '已失败次数';
COMMENT ON COLUMN "public"."hasn_notification_im_command_outbox"."next_attempt_at" IS
    '下次允许领取时间';
COMMENT ON COLUMN "public"."hasn_notification_im_command_outbox"."lease_until" IS
    '处理中租约截止时间';
COMMENT ON COLUMN "public"."hasn_notification_im_command_outbox"."locked_by" IS
    '当前 relay 实例标识';
COMMENT ON COLUMN "public"."hasn_notification_im_command_outbox"."last_error" IS
    '最近一次失败诊断';
COMMENT ON COLUMN "public"."hasn_notification_im_command_outbox"."message_id" IS
    '成功投递后的权威消息 ID';
COMMENT ON COLUMN "public"."hasn_notification_im_command_outbox"."trace_id" IS
    '跨服务追踪标识';
COMMENT ON COLUMN "public"."hasn_notification_im_command_outbox"."causation_id" IS
    '触发本命令的业务事实标识';
COMMENT ON COLUMN "public"."hasn_notification_im_command_outbox"."completed_at" IS
    '投递完成时间';
COMMENT ON COLUMN "public"."hasn_notification_im_command_outbox"."created_time" IS
    '记录创建时间';
COMMENT ON COLUMN "public"."hasn_notification_im_command_outbox"."updated_time" IS
    '状态更新时间';

CREATE INDEX "idx_hasn_notification_im_command_outbox_claim"
    ON "public"."hasn_notification_im_command_outbox"
    ("status", "next_attempt_at", "lease_until");
