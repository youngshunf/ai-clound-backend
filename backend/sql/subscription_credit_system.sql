-- =====================================================
-- 订阅积分系统数据库表设计
-- 适用于 PostgreSQL
-- Date: 2026-01-27
-- =====================================================

-- =====================================================
-- 表 1: user_subscription (用户订阅表)
-- =====================================================
DROP TABLE IF EXISTS "public"."user_subscription";
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
COMMENT ON COLUMN "public"."user_subscription"."user_id" IS '用户 ID (外键 sys_user.id)';
COMMENT ON COLUMN "public"."user_subscription"."tier" IS '订阅等级: free/basic/pro/enterprise';
COMMENT ON COLUMN "public"."user_subscription"."monthly_credits" IS '每月积分配额';
COMMENT ON COLUMN "public"."user_subscription"."current_credits" IS '当前剩余积分 (月度积分 + 购买积分)';
COMMENT ON COLUMN "public"."user_subscription"."used_credits" IS '本周期已使用积分';
COMMENT ON COLUMN "public"."user_subscription"."purchased_credits" IS '购买的额外积分 (不会过期)';
COMMENT ON COLUMN "public"."user_subscription"."billing_cycle_start" IS '计费周期开始时间 (按用户注册日期)';
COMMENT ON COLUMN "public"."user_subscription"."billing_cycle_end" IS '计费周期结束时间 (30天后)';
COMMENT ON COLUMN "public"."user_subscription"."status" IS '订阅状态: active/expired/cancelled';
COMMENT ON COLUMN "public"."user_subscription"."auto_renew" IS '是否自动续费';
COMMENT ON COLUMN "public"."user_subscription"."created_time" IS '创建时间';
COMMENT ON COLUMN "public"."user_subscription"."updated_time" IS '更新时间';
COMMENT ON TABLE "public"."user_subscription" IS '用户订阅表 - 管理用户的订阅等级和积分余额';

-- 索引
CREATE UNIQUE INDEX "ix_user_subscription_user_id" ON "public"."user_subscription" USING btree (
  "user_id" "pg_catalog"."int8_ops" ASC NULLS LAST
);
CREATE INDEX "ix_user_subscription_status" ON "public"."user_subscription" USING btree (
  "status" COLLATE "pg_catalog"."default" "pg_catalog"."text_ops" ASC NULLS LAST
);
CREATE INDEX "ix_user_subscription_tier" ON "public"."user_subscription" USING btree (
  "tier" COLLATE "pg_catalog"."default" "pg_catalog"."text_ops" ASC NULLS LAST
);
CREATE INDEX "ix_user_subscription_billing_cycle" ON "public"."user_subscription" USING btree (
  "billing_cycle_end" "pg_catalog"."timestamptz_ops" ASC NULLS LAST
);

-- =====================================================
-- 表 2: credit_transaction (积分交易记录表)
-- =====================================================
DROP TABLE IF EXISTS "public"."credit_transaction";
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
COMMENT ON COLUMN "public"."credit_transaction"."transaction_type" IS '交易类型: usage/purchase/refund/monthly_grant/bonus/adjustment';
COMMENT ON COLUMN "public"."credit_transaction"."credits" IS '积分变动数量 (正数=增加, 负数=扣除)';
COMMENT ON COLUMN "public"."credit_transaction"."balance_before" IS '交易前余额';
COMMENT ON COLUMN "public"."credit_transaction"."balance_after" IS '交易后余额';
COMMENT ON COLUMN "public"."credit_transaction"."reference_id" IS '关联 ID (如 usage_log.request_id, payment.order_id)';
COMMENT ON COLUMN "public"."credit_transaction"."reference_type" IS '关联类型: llm_usage/payment/system/manual';
COMMENT ON COLUMN "public"."credit_transaction"."description" IS '交易描述';
COMMENT ON COLUMN "public"."credit_transaction"."metadata" IS '扩展元数据 (JSON)';
COMMENT ON COLUMN "public"."credit_transaction"."created_time" IS '交易时间';
COMMENT ON TABLE "public"."credit_transaction" IS '积分交易记录表 - 审计所有积分变动';

-- 索引
CREATE INDEX "ix_credit_transaction_user_id" ON "public"."credit_transaction" USING btree (
  "user_id" "pg_catalog"."int8_ops" ASC NULLS LAST
);
CREATE INDEX "ix_credit_transaction_type" ON "public"."credit_transaction" USING btree (
  "transaction_type" COLLATE "pg_catalog"."default" "pg_catalog"."text_ops" ASC NULLS LAST
);
CREATE INDEX "ix_credit_transaction_reference" ON "public"."credit_transaction" USING btree (
  "reference_id" COLLATE "pg_catalog"."default" "pg_catalog"."text_ops" ASC NULLS LAST,
  "reference_type" COLLATE "pg_catalog"."default" "pg_catalog"."text_ops" ASC NULLS LAST
);
CREATE INDEX "ix_credit_transaction_created_time" ON "public"."credit_transaction" USING btree (
  "created_time" "pg_catalog"."timestamptz_ops" DESC NULLS LAST
);

-- =====================================================
-- 表 3: model_credit_rate (模型积分费率表)
-- =====================================================
DROP TABLE IF EXISTS "public"."model_credit_rate";
CREATE TABLE "public"."model_credit_rate" (
  "id" bigserial PRIMARY KEY,
  "model_id" int8 NOT NULL,
  "base_credit_per_1k_tokens" numeric(10, 4) NOT NULL DEFAULT 1.0,
  "input_multiplier" numeric(5, 2) NOT NULL DEFAULT 1.0,
  "output_multiplier" numeric(5, 2) NOT NULL DEFAULT 1.0,
  "enabled" bool NOT NULL DEFAULT true,
  "created_time" timestamptz(6) NOT NULL DEFAULT NOW(),
  "updated_time" timestamptz(6)
);

COMMENT ON COLUMN "public"."model_credit_rate"."id" IS '主键 ID';
COMMENT ON COLUMN "public"."model_credit_rate"."model_id" IS '模型 ID (外键 llm_model_config.id)';
COMMENT ON COLUMN "public"."model_credit_rate"."base_credit_per_1k_tokens" IS '基准积分费率 (每 1K tokens)';
COMMENT ON COLUMN "public"."model_credit_rate"."input_multiplier" IS '输入 tokens 倍率';
COMMENT ON COLUMN "public"."model_credit_rate"."output_multiplier" IS '输出 tokens 倍率';
COMMENT ON COLUMN "public"."model_credit_rate"."enabled" IS '是否启用';
COMMENT ON COLUMN "public"."model_credit_rate"."created_time" IS '创建时间';
COMMENT ON COLUMN "public"."model_credit_rate"."updated_time" IS '更新时间';
COMMENT ON TABLE "public"."model_credit_rate" IS '模型积分费率表 - 定义不同模型的积分消耗规则';

-- 索引
CREATE UNIQUE INDEX "ix_model_credit_rate_model_id" ON "public"."model_credit_rate" USING btree (
  "model_id" "pg_catalog"."int8_ops" ASC NULLS LAST
);
CREATE INDEX "ix_model_credit_rate_enabled" ON "public"."model_credit_rate" USING btree (
  "enabled" "pg_catalog"."bool_ops" ASC NULLS LAST
);

-- =====================================================
-- 表 4: subscription_tier (订阅等级配置表)
-- =====================================================
DROP TABLE IF EXISTS "public"."subscription_tier";
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
COMMENT ON COLUMN "public"."subscription_tier"."monthly_price" IS '月费 (USD)';
COMMENT ON COLUMN "public"."subscription_tier"."features" IS '功能特性 (JSON)';
COMMENT ON COLUMN "public"."subscription_tier"."enabled" IS '是否启用';
COMMENT ON COLUMN "public"."subscription_tier"."sort_order" IS '排序权重';
COMMENT ON COLUMN "public"."subscription_tier"."created_time" IS '创建时间';
COMMENT ON COLUMN "public"."subscription_tier"."updated_time" IS '更新时间';
COMMENT ON TABLE "public"."subscription_tier" IS '订阅等级配置表 - 定义不同订阅等级的权益';

-- 索引
CREATE UNIQUE INDEX "ix_subscription_tier_name" ON "public"."subscription_tier" USING btree (
  "tier_name" COLLATE "pg_catalog"."default" "pg_catalog"."text_ops" ASC NULLS LAST
);
CREATE INDEX "ix_subscription_tier_enabled" ON "public"."subscription_tier" USING btree (
  "enabled" "pg_catalog"."bool_ops" ASC NULLS LAST
);
CREATE INDEX "ix_subscription_tier_sort_order" ON "public"."subscription_tier" USING btree (
  "sort_order" "pg_catalog"."int4_ops" ASC NULLS LAST
);

-- =====================================================
-- 表 5: credit_package (积分包配置表)
-- =====================================================
DROP TABLE IF EXISTS "public"."credit_package";
CREATE TABLE "public"."credit_package" (
  "id" bigserial PRIMARY KEY,
  "package_name" varchar(64) COLLATE "pg_catalog"."default" NOT NULL,
  "credits" numeric(15, 2) NOT NULL,
  "price" numeric(10, 2) NOT NULL,
  "bonus_credits" numeric(15, 2) NOT NULL DEFAULT 0,
  "description" varchar(512) COLLATE "pg_catalog"."default",
  "enabled" bool NOT NULL DEFAULT true,
  "sort_order" int4 NOT NULL DEFAULT 0,
  "created_time" timestamptz(6) NOT NULL DEFAULT NOW(),
  "updated_time" timestamptz(6)
);

COMMENT ON COLUMN "public"."credit_package"."id" IS '主键 ID';
COMMENT ON COLUMN "public"."credit_package"."package_name" IS '积分包名称';
COMMENT ON COLUMN "public"."credit_package"."credits" IS '基础积分数量';
COMMENT ON COLUMN "public"."credit_package"."price" IS '价格 (USD)';
COMMENT ON COLUMN "public"."credit_package"."bonus_credits" IS '额外赠送积分';
COMMENT ON COLUMN "public"."credit_package"."description" IS '描述';
COMMENT ON COLUMN "public"."credit_package"."enabled" IS '是否启用';
COMMENT ON COLUMN "public"."credit_package"."sort_order" IS '排序权重';
COMMENT ON COLUMN "public"."credit_package"."created_time" IS '创建时间';
COMMENT ON COLUMN "public"."credit_package"."updated_time" IS '更新时间';
COMMENT ON TABLE "public"."credit_package" IS '积分包配置表 - 定义可购买的积分包';

-- 索引
CREATE INDEX "ix_credit_package_enabled" ON "public"."credit_package" USING btree (
  "enabled" "pg_catalog"."bool_ops" ASC NULLS LAST
);
CREATE INDEX "ix_credit_package_sort_order" ON "public"."credit_package" USING btree (
  "sort_order" "pg_catalog"."int4_ops" ASC NULLS LAST
);

-- =====================================================
-- 初始化数据
-- =====================================================

-- 订阅等级配置
INSERT INTO "public"."subscription_tier" ("tier_name", "display_name", "monthly_credits", "monthly_price", "features", "enabled", "sort_order")
VALUES
  ('free', '免费版', 100000, 0, '{"max_models": "all", "support_level": "community", "api_access": true}', true, 1),
  ('basic', '基础版', 1000000, 9.99, '{"max_models": "all", "support_level": "email", "api_access": true, "priority_queue": false}', true, 2),
  ('pro', '专业版', 5000000, 29.99, '{"max_models": "all", "support_level": "priority", "api_access": true, "priority_queue": true}', true, 3),
  ('enterprise', '企业版', 20000000, 99.99, '{"max_models": "all", "support_level": "dedicated", "api_access": true, "priority_queue": true, "custom_features": true}', true, 4);

-- 积分包配置
INSERT INTO "public"."credit_package" ("package_name", "credits", "price", "bonus_credits", "description", "enabled", "sort_order")
VALUES
  ('小额包', 500000, 4.99, 0, '50万积分，适合轻度使用', true, 1),
  ('标准包', 1000000, 8.99, 100000, '100万积分 + 10万赠送', true, 2),
  ('超值包', 3000000, 24.99, 500000, '300万积分 + 50万赠送', true, 3),
  ('企业包', 10000000, 79.99, 2000000, '1000万积分 + 200万赠送', true, 4);

-- 模型积分费率配置 (基准: GPT-3.5 = 1 积分/1K tokens)
-- OpenAI 模型
INSERT INTO "public"."model_credit_rate" ("model_id", "base_credit_per_1k_tokens", "input_multiplier", "output_multiplier", "enabled")
SELECT id, 1.0, 1.0, 1.0, true FROM llm_model_config WHERE model_name = 'gpt-3.5-turbo';

INSERT INTO "public"."model_credit_rate" ("model_id", "base_credit_per_1k_tokens", "input_multiplier", "output_multiplier", "enabled")
SELECT id, 0.3, 1.0, 1.0, true FROM llm_model_config WHERE model_name = 'gpt-4o-mini';

INSERT INTO "public"."model_credit_rate" ("model_id", "base_credit_per_1k_tokens", "input_multiplier", "output_multiplier", "enabled")
SELECT id, 5.0, 1.0, 4.0, true FROM llm_model_config WHERE model_name = 'gpt-4o';

INSERT INTO "public"."model_credit_rate" ("model_id", "base_credit_per_1k_tokens", "input_multiplier", "output_multiplier", "enabled")
SELECT id, 20.0, 1.0, 3.0, true FROM llm_model_config WHERE model_name = 'gpt-4-turbo';

INSERT INTO "public"."model_credit_rate" ("model_id", "base_credit_per_1k_tokens", "input_multiplier", "output_multiplier", "enabled")
SELECT id, 30.0, 1.0, 4.0, true FROM llm_model_config WHERE model_name = 'o1-preview';

INSERT INTO "public"."model_credit_rate" ("model_id", "base_credit_per_1k_tokens", "input_multiplier", "output_multiplier", "enabled")
SELECT id, 6.0, 1.0, 4.0, true FROM llm_model_config WHERE model_name = 'o1-mini';

-- Anthropic 模型
INSERT INTO "public"."model_credit_rate" ("model_id", "base_credit_per_1k_tokens", "input_multiplier", "output_multiplier", "enabled")
SELECT id, 0.2, 1.0, 1.0, true FROM llm_model_config WHERE model_name = 'claude-3-5-haiku-20241022';

INSERT INTO "public"."model_credit_rate" ("model_id", "base_credit_per_1k_tokens", "input_multiplier", "output_multiplier", "enabled")
SELECT id, 6.0, 1.0, 5.0, true FROM llm_model_config WHERE model_name = 'claude-3-5-sonnet-20241022';

INSERT INTO "public"."model_credit_rate" ("model_id", "base_credit_per_1k_tokens", "input_multiplier", "output_multiplier", "enabled")
SELECT id, 30.0, 1.0, 5.0, true FROM llm_model_config WHERE model_name = 'claude-3-opus-20240229';

-- 国产模型
INSERT INTO "public"."model_credit_rate" ("model_id", "base_credit_per_1k_tokens", "input_multiplier", "output_multiplier", "enabled")
SELECT id, 0.28, 1.0, 1.0, true FROM llm_model_config WHERE model_name = 'deepseek-chat';

INSERT INTO "public"."model_credit_rate" ("model_id", "base_credit_per_1k_tokens", "input_multiplier", "output_multiplier", "enabled")
SELECT id, 1.1, 1.0, 4.0, true FROM llm_model_config WHERE model_name = 'deepseek-reasoner';

INSERT INTO "public"."model_credit_rate" ("model_id", "base_credit_per_1k_tokens", "input_multiplier", "output_multiplier", "enabled")
SELECT id, 1.6, 1.0, 1.0, true FROM llm_model_config WHERE model_name = 'qwen-turbo';

INSERT INTO "public"."model_credit_rate" ("model_id", "base_credit_per_1k_tokens", "input_multiplier", "output_multiplier", "enabled")
SELECT id, 8.0, 1.0, 3.0, true FROM llm_model_config WHERE model_name = 'qwen-plus';

INSERT INTO "public"."model_credit_rate" ("model_id", "base_credit_per_1k_tokens", "input_multiplier", "output_multiplier", "enabled")
SELECT id, 40.0, 1.0, 3.0, true FROM llm_model_config WHERE model_name = 'qwen-max';

-- =====================================================
-- 说明
-- =====================================================
-- 1. 积分计算公式:
--    total_credits = (input_tokens / 1000) * base_credit * input_multiplier + 
--                    (output_tokens / 1000) * base_credit * output_multiplier
--
-- 2. 用户注册时自动创建 user_subscription 记录 (free tier)
-- 3. 每月按用户注册日期刷新积分 (billing_cycle_end)
-- 4. 购买的积分 (purchased_credits) 不会过期
-- 5. 月度积分会在周期结束时清零并重新发放
