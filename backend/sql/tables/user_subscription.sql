-- 用户订阅表
CREATE TABLE "public"."user_subscription" (
  "id" bigserial PRIMARY KEY,
  "user_id" int8 NOT NULL,
  "tier" varchar(32) COLLATE "pg_catalog"."default" NOT NULL DEFAULT 'free',
  "monthly_credits" numeric(15, 2) NOT NULL DEFAULT 0,
  "current_credits" numeric(15, 2) NOT NULL DEFAULT 0,
  "used_credits" numeric(15, 2) NOT NULL DEFAULT 0,
  "purchased_credits" numeric(15, 2) NOT NULL DEFAULT 0,
  "billing_cycle_start" timestamptz(6) NOT NULL,
  "billing_cycle_end" timestamptz(6) NOT NULL,
  "status" varchar(32) COLLATE "pg_catalog"."default" NOT NULL DEFAULT 'active',
  "auto_renew" bool NOT NULL DEFAULT true,
  "created_time" timestamptz(6) NOT NULL DEFAULT NOW(),
  "updated_time" timestamptz(6)
);

COMMENT ON COLUMN "public"."user_subscription"."id" IS '主键 ID';
COMMENT ON COLUMN "public"."user_subscription"."user_id" IS '用户 ID';
COMMENT ON COLUMN "public"."user_subscription"."tier" IS '订阅等级: free/basic/pro/enterprise';
COMMENT ON COLUMN "public"."user_subscription"."monthly_credits" IS '每月积分配额';
COMMENT ON COLUMN "public"."user_subscription"."current_credits" IS '当前剩余积分';
COMMENT ON COLUMN "public"."user_subscription"."used_credits" IS '本周期已使用积分';
COMMENT ON COLUMN "public"."user_subscription"."purchased_credits" IS '购买的额外积分';
COMMENT ON COLUMN "public"."user_subscription"."billing_cycle_start" IS '计费周期开始时间';
COMMENT ON COLUMN "public"."user_subscription"."billing_cycle_end" IS '计费周期结束时间';
COMMENT ON COLUMN "public"."user_subscription"."status" IS '订阅状态: active/expired/cancelled';
COMMENT ON COLUMN "public"."user_subscription"."auto_renew" IS '是否自动续费';
COMMENT ON TABLE "public"."user_subscription" IS '用户订阅表';
