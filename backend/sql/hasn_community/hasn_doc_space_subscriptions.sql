-- 生成：
-- uv run fba codegen generate --sql-file backend/sql/hasn_community/hasn_doc_space_subscriptions.sql --app hasn_community --schema hasn_community --execute
CREATE TABLE "hasn_community"."hasn_doc_space_subscriptions" (
  "id"                  bigserial PRIMARY KEY,
  "subscription_id"     varchar(40) NOT NULL UNIQUE,
  "space_id"            varchar(40) NOT NULL,
  "subscriber_hasn_id"  varchar(40) NOT NULL,
  "created_time"        timestamptz(6) NOT NULL DEFAULT now(),
  "updated_time"        timestamptz(6),
  CONSTRAINT "uq_doc_space_subscriptions_space_subscriber"
    UNIQUE ("space_id", "subscriber_hasn_id")
);

CREATE INDEX "idx_doc_space_subscriptions_subscriber"
  ON "hasn_community"."hasn_doc_space_subscriptions"
  ("subscriber_hasn_id", "created_time" DESC, "subscription_id" DESC);

CREATE INDEX "idx_doc_space_subscriptions_space"
  ON "hasn_community"."hasn_doc_space_subscriptions" ("space_id");

COMMENT ON TABLE "hasn_community"."hasn_doc_space_subscriptions" IS '社区文集订阅关系表';
COMMENT ON COLUMN "hasn_community"."hasn_doc_space_subscriptions"."id" IS '内部自增主键';
COMMENT ON COLUMN "hasn_community"."hasn_doc_space_subscriptions"."subscription_id" IS '订阅关系权威 UUID';
COMMENT ON COLUMN "hasn_community"."hasn_doc_space_subscriptions"."space_id" IS '文集权威 space_id';
COMMENT ON COLUMN "hasn_community"."hasn_doc_space_subscriptions"."subscriber_hasn_id" IS '订阅者 hasn_id';
COMMENT ON COLUMN "hasn_community"."hasn_doc_space_subscriptions"."created_time" IS '订阅时间';
COMMENT ON COLUMN "hasn_community"."hasn_doc_space_subscriptions"."updated_time" IS '更新时间';
