CREATE TABLE "public"."hasn_relation_command_outbox" (
    "id" bigserial NOT NULL,
    "command_id" varchar(40) NOT NULL,
    "command_type" varchar(64) NOT NULL,
    "owner_hasn_id" varchar(40) NOT NULL,
    "peer_hasn_id" varchar(40) NOT NULL,
    "idempotency_key" varchar(160) NOT NULL,
    "status" varchar(16) NOT NULL DEFAULT 'pending',
    "attempt_count" integer NOT NULL DEFAULT 0,
    "next_retry_at" timestamptz NOT NULL DEFAULT now(),
    "lease_until" timestamptz,
    "last_error" text,
    "completed_at" timestamptz,
    "created_time" timestamptz NOT NULL DEFAULT now(),
    "updated_time" timestamptz NOT NULL DEFAULT now(),
    CONSTRAINT "pk_hasn_relation_command_outbox" PRIMARY KEY ("id"),
    CONSTRAINT "uq_hasn_relation_command_outbox_command_id" UNIQUE ("command_id"),
    CONSTRAINT "uq_hasn_relation_command_outbox_idempotency" UNIQUE ("idempotency_key"),
    CONSTRAINT "ck_hasn_relation_command_outbox_type"
        CHECK ("command_type" IN ('ensure_owner_agent_control_edge')),
    CONSTRAINT "ck_hasn_relation_command_outbox_status"
        CHECK ("status" IN ('pending', 'processing', 'completed', 'dead_letter')),
    CONSTRAINT "ck_hasn_relation_command_outbox_attempt_count"
        CHECK ("attempt_count" >= 0)
);

COMMENT ON TABLE "public"."hasn_relation_command_outbox" IS
    '身份事实投影为 IM 关系的可靠命令队列';
COMMENT ON COLUMN "public"."hasn_relation_command_outbox"."command_id" IS
    '命令公开标识';
COMMENT ON COLUMN "public"."hasn_relation_command_outbox"."command_type" IS
    '关系命令类型';
COMMENT ON COLUMN "public"."hasn_relation_command_outbox"."owner_hasn_id" IS
    '控制边主人 HASN ID';
COMMENT ON COLUMN "public"."hasn_relation_command_outbox"."peer_hasn_id" IS
    '主人名下分身 HASN ID';
COMMENT ON COLUMN "public"."hasn_relation_command_outbox"."idempotency_key" IS
    '跨重试稳定幂等键';
COMMENT ON COLUMN "public"."hasn_relation_command_outbox"."status" IS
    '投递状态：pending/processing/completed/dead_letter';
COMMENT ON COLUMN "public"."hasn_relation_command_outbox"."attempt_count" IS
    '已失败次数';
COMMENT ON COLUMN "public"."hasn_relation_command_outbox"."next_retry_at" IS
    '下次允许领取时间';
COMMENT ON COLUMN "public"."hasn_relation_command_outbox"."lease_until" IS
    '处理租约截止时间';
COMMENT ON COLUMN "public"."hasn_relation_command_outbox"."last_error" IS
    '最近一次失败诊断';
COMMENT ON COLUMN "public"."hasn_relation_command_outbox"."completed_at" IS
    '投递完成时间';
COMMENT ON COLUMN "public"."hasn_relation_command_outbox"."created_time" IS
    '记录创建时间';
COMMENT ON COLUMN "public"."hasn_relation_command_outbox"."updated_time" IS
    '状态更新时间';

CREATE INDEX "idx_hasn_relation_command_outbox_claim"
    ON "public"."hasn_relation_command_outbox" ("status", "next_retry_at", "lease_until");
