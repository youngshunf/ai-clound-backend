-- 用户积分余额表（分批管理积分有效期）
CREATE TABLE "public"."user_credit_balance" (
  "id" bigserial PRIMARY KEY,
  "user_id" int8 NOT NULL,
  "credit_type" varchar(32) COLLATE "pg_catalog"."default" NOT NULL,
  "original_amount" numeric(15, 2) NOT NULL,
  "used_amount" numeric(15, 2) NOT NULL DEFAULT 0,
  "remaining_amount" numeric(15, 2) NOT NULL,
  "expires_at" timestamptz(6),
  "granted_at" timestamptz(6) NOT NULL DEFAULT NOW(),
  "source_type" varchar(32) COLLATE "pg_catalog"."default" NOT NULL,
  "source_reference_id" varchar(64) COLLATE "pg_catalog"."default",
  "description" varchar(256) COLLATE "pg_catalog"."default",
  "created_time" timestamptz(6) NOT NULL DEFAULT NOW(),
  "updated_time" timestamptz(6)
);

-- 索引
CREATE INDEX "idx_credit_balance_user_id" ON "public"."user_credit_balance" ("user_id");
CREATE INDEX "idx_credit_balance_expires" ON "public"."user_credit_balance" ("user_id", "expires_at") WHERE remaining_amount > 0;

COMMENT ON COLUMN "public"."user_credit_balance"."id" IS '主键 ID';
COMMENT ON COLUMN "public"."user_credit_balance"."user_id" IS '用户 ID';
COMMENT ON COLUMN "public"."user_credit_balance"."credit_type" IS '积分类型 (monthly:月度赠送:blue/purchased:购买积分:green/bonus:活动赠送:orange)';
COMMENT ON COLUMN "public"."user_credit_balance"."original_amount" IS '原始积分数量';
COMMENT ON COLUMN "public"."user_credit_balance"."used_amount" IS '已使用积分';
COMMENT ON COLUMN "public"."user_credit_balance"."remaining_amount" IS '剩余积分数量';
COMMENT ON COLUMN "public"."user_credit_balance"."expires_at" IS '过期时间';
COMMENT ON COLUMN "public"."user_credit_balance"."granted_at" IS '发放时间';
COMMENT ON COLUMN "public"."user_credit_balance"."source_type" IS '来源类型 (subscription_grant:订阅发放/subscription_upgrade:升级发放/purchase:购买/bonus:赠送/refund:退款返还)';
COMMENT ON COLUMN "public"."user_credit_balance"."source_reference_id" IS '关联订单号';
COMMENT ON COLUMN "public"."user_credit_balance"."description" IS '描述';
COMMENT ON TABLE "public"."user_credit_balance" IS '用户积分余额';
