-- sync inbox 业务应用的事务内幂等回执。
CREATE TABLE "public"."hasn_sync_business_receipts" (
  "id"              bigserial PRIMARY KEY,
  "idempotency_key" varchar(256) NOT NULL,
  "owner_id"        varchar(40) NOT NULL,
  "node_id"         varchar(40) NOT NULL,
  "client_event_id" varchar(80) NOT NULL,
  "event_type"      varchar(80) NOT NULL,
  "applied_at"      timestamptz(6) NOT NULL DEFAULT now(),
  "created_time"    timestamptz(6) NOT NULL DEFAULT now(),
  "updated_time"    timestamptz(6),
  CONSTRAINT "uq_hasn_sync_business_receipt_key" UNIQUE ("idempotency_key"),
  CONSTRAINT "uq_hasn_sync_business_receipt_event"
    UNIQUE ("owner_id", "node_id", "client_event_id")
);

CREATE INDEX "idx_hasn_sync_business_receipts_owner_applied"
  ON "public"."hasn_sync_business_receipts" ("owner_id", "applied_at" DESC);

COMMENT ON TABLE "public"."hasn_sync_business_receipts" IS 'sync inbox 业务应用的事务内幂等回执';
COMMENT ON COLUMN "public"."hasn_sync_business_receipts"."id" IS '数据库主键';
COMMENT ON COLUMN "public"."hasn_sync_business_receipts"."idempotency_key" IS 'worker 派生的稳定幂等键';
COMMENT ON COLUMN "public"."hasn_sync_business_receipts"."owner_id" IS '主人隔离键';
COMMENT ON COLUMN "public"."hasn_sync_business_receipts"."node_id" IS '上报节点 ID';
COMMENT ON COLUMN "public"."hasn_sync_business_receipts"."client_event_id" IS '客户端事件 ID';
COMMENT ON COLUMN "public"."hasn_sync_business_receipts"."event_type" IS '已应用的业务事件类型';
COMMENT ON COLUMN "public"."hasn_sync_business_receipts"."applied_at" IS '业务事务提交时间';
COMMENT ON COLUMN "public"."hasn_sync_business_receipts"."created_time" IS '创建时间';
COMMENT ON COLUMN "public"."hasn_sync_business_receipts"."updated_time" IS '更新时间';
