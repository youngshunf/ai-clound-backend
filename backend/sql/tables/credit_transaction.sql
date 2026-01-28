-- 积分交易记录表
CREATE TABLE "public"."credit_transaction" (
  "id" bigserial PRIMARY KEY,
  "user_id" int8 NOT NULL,
  "transaction_type" varchar(32) COLLATE "pg_catalog"."default" NOT NULL,
  "credits" numeric(15, 2) NOT NULL,
  "balance_before" numeric(15, 2) NOT NULL,
  "balance_after" numeric(15, 2) NOT NULL,
  "reference_id" varchar(64) COLLATE "pg_catalog"."default",
  "reference_type" varchar(32) COLLATE "pg_catalog"."default",
  "description" varchar(512) COLLATE "pg_catalog"."default",
  "metadata" jsonb,
  "created_time" timestamptz(6) NOT NULL DEFAULT NOW()
);

COMMENT ON COLUMN "public"."credit_transaction"."id" IS '主键 ID';
COMMENT ON COLUMN "public"."credit_transaction"."user_id" IS '用户 ID';
COMMENT ON COLUMN "public"."credit_transaction"."transaction_type" IS '交易类型 (usage:使用/purchase:购买/refund:退款/monthly_grant:月度赠送/bonus:奖励)';
COMMENT ON COLUMN "public"."credit_transaction"."credits" IS '积分变动数量';
COMMENT ON COLUMN "public"."credit_transaction"."balance_before" IS '交易前余额';
COMMENT ON COLUMN "public"."credit_transaction"."balance_after" IS '交易后余额';
COMMENT ON COLUMN "public"."credit_transaction"."reference_id" IS '关联 ID';
COMMENT ON COLUMN "public"."credit_transaction"."reference_type" IS '关联类型 (llm_usage:LLM调用/payment:支付/system:系统)';
COMMENT ON COLUMN "public"."credit_transaction"."description" IS '交易描述';
COMMENT ON COLUMN "public"."credit_transaction"."metadata" IS '扩展元数据';
COMMENT ON TABLE "public"."credit_transaction" IS '积分交易记录表';
