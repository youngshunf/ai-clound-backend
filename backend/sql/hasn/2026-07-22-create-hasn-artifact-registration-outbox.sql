CREATE TABLE "public"."hasn_artifact_registration_outbox" (
    "id"                  BIGSERIAL PRIMARY KEY,
    "outbox_id"           VARCHAR(40) NOT NULL,
    "owner_hasn_id"       VARCHAR(40) NOT NULL,
    "artifact_id"         VARCHAR(40),
    "idempotency_key"     VARCHAR(768) NOT NULL,
    "payload"             JSONB NOT NULL,
    "status"              VARCHAR(16) NOT NULL DEFAULT 'pending',
    "attempt_count"       INTEGER NOT NULL DEFAULT 0,
    "next_retry_at"       TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    "lease_until"         TIMESTAMPTZ,
    "last_error"          TEXT,
    "created_time"        TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    "updated_time"        TIMESTAMPTZ,
    CONSTRAINT "uq_hasn_artifact_registration_outbox_id" UNIQUE ("outbox_id"),
    CONSTRAINT "uq_hasn_artifact_registration_outbox_idempotency"
        UNIQUE ("owner_hasn_id", "idempotency_key"),
    CONSTRAINT "fk_hasn_artifact_registration_outbox_artifact"
        FOREIGN KEY ("artifact_id") REFERENCES "public"."hasn_artifacts" ("artifact_id"),
    CONSTRAINT "ck_hasn_artifact_registration_outbox_status"
        CHECK ("status" IN ('pending', 'processing', 'completed', 'dead_letter')),
    CONSTRAINT "ck_hasn_artifact_registration_outbox_attempt_count"
        CHECK ("attempt_count" >= 0)
);

COMMENT ON TABLE "public"."hasn_artifact_registration_outbox" IS 'Agent 产物登记可靠投递与修复队列';
COMMENT ON COLUMN "public"."hasn_artifact_registration_outbox"."id" IS '数据库主键';
COMMENT ON COLUMN "public"."hasn_artifact_registration_outbox"."outbox_id" IS '队列记录公开标识';
COMMENT ON COLUMN "public"."hasn_artifact_registration_outbox"."owner_hasn_id" IS '主人隔离键';
COMMENT ON COLUMN "public"."hasn_artifact_registration_outbox"."artifact_id" IS '已归一产物公开标识';
COMMENT ON COLUMN "public"."hasn_artifact_registration_outbox"."idempotency_key" IS '登记来源幂等键';
COMMENT ON COLUMN "public"."hasn_artifact_registration_outbox"."payload" IS '不含正文和本地绝对路径的修复载荷';
COMMENT ON COLUMN "public"."hasn_artifact_registration_outbox"."status" IS '投递状态 (pending:待处理:processing:处理中:completed:已完成:dead_letter:终局失败)';
COMMENT ON COLUMN "public"."hasn_artifact_registration_outbox"."attempt_count" IS '已尝试次数';
COMMENT ON COLUMN "public"."hasn_artifact_registration_outbox"."next_retry_at" IS '下次可领取时间';
COMMENT ON COLUMN "public"."hasn_artifact_registration_outbox"."lease_until" IS '处理租约截止时间';
COMMENT ON COLUMN "public"."hasn_artifact_registration_outbox"."last_error" IS '最近失败诊断';
COMMENT ON COLUMN "public"."hasn_artifact_registration_outbox"."created_time" IS '记录创建时间';
COMMENT ON COLUMN "public"."hasn_artifact_registration_outbox"."updated_time" IS '状态更新时间';

CREATE INDEX "idx_hasn_artifact_registration_outbox_claim"
    ON "public"."hasn_artifact_registration_outbox" ("status", "next_retry_at")
    WHERE "status" IN ('pending', 'processing');
