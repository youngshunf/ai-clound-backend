-- 订阅等级配置表
CREATE TABLE "public"."subscription_tier" (
  "id" bigserial PRIMARY KEY,
  "tier_name" varchar(32) COLLATE "pg_catalog"."default" NOT NULL UNIQUE,
  "display_name" varchar(64) COLLATE "pg_catalog"."default" NOT NULL,
  "monthly_credits" numeric(15, 2) NOT NULL DEFAULT 0,
  "monthly_price" numeric(10, 2) NOT NULL DEFAULT 0,
  "features" jsonb NOT NULL DEFAULT '{}',
  "enabled" bool NOT NULL DEFAULT true,
  "sort_order" int4 NOT NULL DEFAULT 0,
  "created_time" timestamptz(6) NOT NULL DEFAULT NOW(),
  "updated_time" timestamptz(6)
);

COMMENT ON COLUMN "public"."subscription_tier"."id" IS '主键 ID';
COMMENT ON COLUMN "public"."subscription_tier"."tier_name" IS '等级标识: free/basic/pro/enterprise';
COMMENT ON COLUMN "public"."subscription_tier"."display_name" IS '显示名称';
COMMENT ON COLUMN "public"."subscription_tier"."monthly_credits" IS '每月赠送积分';
COMMENT ON COLUMN "public"."subscription_tier"."monthly_price" IS '月费';
COMMENT ON COLUMN "public"."subscription_tier"."features" IS '功能特性';
COMMENT ON COLUMN "public"."subscription_tier"."enabled" IS '是否启用';
COMMENT ON COLUMN "public"."subscription_tier"."sort_order" IS '排序权重';
COMMENT ON TABLE "public"."subscription_tier" IS '订阅等级配置表';
