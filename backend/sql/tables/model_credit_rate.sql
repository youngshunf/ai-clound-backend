-- 模型积分费率表
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
COMMENT ON COLUMN "public"."model_credit_rate"."model_id" IS '模型 ID';
COMMENT ON COLUMN "public"."model_credit_rate"."base_credit_per_1k_tokens" IS '基准积分费率';
COMMENT ON COLUMN "public"."model_credit_rate"."input_multiplier" IS '输入倍率';
COMMENT ON COLUMN "public"."model_credit_rate"."output_multiplier" IS '输出倍率';
COMMENT ON COLUMN "public"."model_credit_rate"."enabled" IS '是否启用';
COMMENT ON TABLE "public"."model_credit_rate" IS '模型积分费率表';
