-- =====================================================
-- AI-Native 应用权益（云端权威）
-- 谁「有权使用」某个付费 app：购买 / 试用 / 管理员授予
-- tier_gated 类不落行（运行期实时读订阅档，对齐 billing 哲学）
-- 设计事实源：docs/hasn-node设计文档/14-AI-Native应用平台/16-应用目录与商业化管理统一设计.md §4.2
-- =====================================================
CREATE TABLE IF NOT EXISTS "public"."hasn_app_entitlement" (
  "id"            bigserial    PRIMARY KEY,
  "app_id"        varchar(64)  NOT NULL,
  "subject_type"  varchar(16)  NOT NULL,
  "subject_id"    varchar(40)  NOT NULL,
  "source"        varchar(16)  NOT NULL,
  "status"        varchar(16)  NOT NULL DEFAULT 'active',
  "order_ref"     varchar(64),
  "granted_at"    timestamptz(6) NOT NULL DEFAULT now(),
  "expires_at"    timestamptz(6),
  "created_time"  timestamptz(6) NOT NULL DEFAULT now(),
  "updated_time"  timestamptz(6)
);

-- 同一主体对同一 app 只保留一条 active 权益（重复购买=续期更新而非新行）
CREATE UNIQUE INDEX IF NOT EXISTS "uq_app_entitlement_active"
  ON "public"."hasn_app_entitlement" ("app_id", "subject_type", "subject_id")
  WHERE "status" = 'active';
CREATE INDEX IF NOT EXISTS "idx_app_entitlement_subject"
  ON "public"."hasn_app_entitlement" ("subject_type", "subject_id", "status");

COMMENT ON TABLE "public"."hasn_app_entitlement" IS 'AI-Native 应用权益（云端权威）';
COMMENT ON COLUMN "public"."hasn_app_entitlement"."id" IS '主键 ID';
COMMENT ON COLUMN "public"."hasn_app_entitlement"."app_id" IS '应用唯一标识';
COMMENT ON COLUMN "public"."hasn_app_entitlement"."subject_type" IS '权益主体 (owner:个人:blue/enterprise:企业:purple)';
COMMENT ON COLUMN "public"."hasn_app_entitlement"."subject_id" IS '主体 ID（owner=hasn_id / enterprise=enterprise_id）';
COMMENT ON COLUMN "public"."hasn_app_entitlement"."source" IS '权益来源 (purchase:购买:green/trial:试用:orange/admin_grant:管理员授予:blue)';
COMMENT ON COLUMN "public"."hasn_app_entitlement"."status" IS '权益状态 (active:生效:green/expired:已过期:gray/revoked:已撤销:red)';
COMMENT ON COLUMN "public"."hasn_app_entitlement"."order_ref" IS '关联支付订单号（purchase 时）';
COMMENT ON COLUMN "public"."hasn_app_entitlement"."granted_at" IS '授予时间';
COMMENT ON COLUMN "public"."hasn_app_entitlement"."expires_at" IS '过期时间（null=永久买断）';
COMMENT ON COLUMN "public"."hasn_app_entitlement"."created_time" IS '创建时间';
COMMENT ON COLUMN "public"."hasn_app_entitlement"."updated_time" IS '更新时间';
