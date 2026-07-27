-- R3：任务运行与执行帧命令同事务落库，中心调度器不再在业务事务内直推 Redis/WS。
CREATE TABLE IF NOT EXISTS "hasn_task"."task_dispatch_outbox" (
    "id" bigserial NOT NULL,
    "command_id" varchar(40) NOT NULL,
    "run_id" bigint NOT NULL,
    "task_id" bigint NOT NULL,
    "target_owner_id" varchar(64) NOT NULL,
    "method" varchar(64) NOT NULL,
    "payload" jsonb NOT NULL,
    "payload_hash" char(64) NOT NULL,
    "idempotency_key" varchar(160) NOT NULL,
    "status" varchar(16) NOT NULL DEFAULT 'pending',
    "attempt_count" integer NOT NULL DEFAULT 0,
    "next_attempt_at" timestamptz NOT NULL DEFAULT now(),
    "lease_until" timestamptz,
    "locked_by" varchar(160),
    "last_error" text,
    "completed_at" timestamptz,
    "created_time" timestamptz NOT NULL DEFAULT now(),
    "updated_time" timestamptz NOT NULL DEFAULT now(),
    CONSTRAINT "pk_task_dispatch_outbox" PRIMARY KEY ("id"),
    CONSTRAINT "fk_task_dispatch_outbox_run"
        FOREIGN KEY ("run_id") REFERENCES "hasn_task"."run" ("id") ON DELETE CASCADE,
    CONSTRAINT "uq_task_dispatch_outbox_command_id" UNIQUE ("command_id"),
    CONSTRAINT "uq_task_dispatch_outbox_run" UNIQUE ("run_id"),
    CONSTRAINT "uq_task_dispatch_outbox_idempotency" UNIQUE ("idempotency_key"),
    CONSTRAINT "ck_task_dispatch_outbox_method"
        CHECK ("method" = 'hasn.task.exec'),
    CONSTRAINT "ck_task_dispatch_outbox_status"
        CHECK ("status" IN ('pending', 'processing', 'completed', 'dead_letter')),
    CONSTRAINT "ck_task_dispatch_outbox_attempt_count"
        CHECK ("attempt_count" >= 0)
);

COMMENT ON TABLE "hasn_task"."task_dispatch_outbox" IS
    '中心任务调度器向主人节点可靠投递任务执行帧的事务队列';
COMMENT ON COLUMN "hasn_task"."task_dispatch_outbox"."command_id" IS
    '派发命令公开标识';
COMMENT ON COLUMN "hasn_task"."task_dispatch_outbox"."run_id" IS
    '同事务创建的任务运行 ID，同时作为单次派发唯一键';
COMMENT ON COLUMN "hasn_task"."task_dispatch_outbox"."task_id" IS
    '任务定义 ID';
COMMENT ON COLUMN "hasn_task"."task_dispatch_outbox"."target_owner_id" IS
    '接收任务执行帧的主人 HASN ID';
COMMENT ON COLUMN "hasn_task"."task_dispatch_outbox"."method" IS
    'HASN 协议方法，固定 hasn.task.exec';
COMMENT ON COLUMN "hasn_task"."task_dispatch_outbox"."payload" IS
    '完整任务执行参数 JSON，不包含 HASN 外层信封';
COMMENT ON COLUMN "hasn_task"."task_dispatch_outbox"."payload_hash" IS
    '规范化目标、方法与载荷的 SHA-256';
COMMENT ON COLUMN "hasn_task"."task_dispatch_outbox"."idempotency_key" IS
    '由权威 run ID 派生的稳定派发幂等键';
COMMENT ON COLUMN "hasn_task"."task_dispatch_outbox"."status" IS
    '投递状态：pending/processing/completed/dead_letter';
COMMENT ON COLUMN "hasn_task"."task_dispatch_outbox"."attempt_count" IS
    '已失败次数';
COMMENT ON COLUMN "hasn_task"."task_dispatch_outbox"."next_attempt_at" IS
    '下次允许领取时间';
COMMENT ON COLUMN "hasn_task"."task_dispatch_outbox"."lease_until" IS
    '处理中租约截止时间';
COMMENT ON COLUMN "hasn_task"."task_dispatch_outbox"."locked_by" IS
    '当前 relay 实例标识';
COMMENT ON COLUMN "hasn_task"."task_dispatch_outbox"."last_error" IS
    '最近一次失败诊断';
COMMENT ON COLUMN "hasn_task"."task_dispatch_outbox"."completed_at" IS
    '已交给实时投递层的时间';
COMMENT ON COLUMN "hasn_task"."task_dispatch_outbox"."created_time" IS
    '记录创建时间';
COMMENT ON COLUMN "hasn_task"."task_dispatch_outbox"."updated_time" IS
    '状态更新时间';

CREATE INDEX IF NOT EXISTS "idx_task_dispatch_outbox_claim"
    ON "hasn_task"."task_dispatch_outbox"
    ("status", "next_attempt_at", "lease_until");
