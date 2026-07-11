-- =====================================================
-- 统一商业化内核 MK-1：hasn_app_entitlement 加列（feature_key 化 + 配额快照）
-- 幂等：可重复执行。schema=public（HasnAppEntitlement 继承 fba Base）
-- 设计事实源：docs/hasn-node设计文档/16-订阅与积分计费/02-统一商业化内核设计.md
-- 施工权威：同模块 实施/92 MK-1
-- =====================================================

-- feature_key：付费墙通用语言（app:<id>/llm:tier/webapp:hosting；feature_registry 校验）
ALTER TABLE "hasn_app_entitlement"
  ADD COLUMN IF NOT EXISTS "feature_key" varchar(64) NOT NULL DEFAULT '';
COMMENT ON COLUMN "hasn_app_entitlement"."feature_key"
  IS '付费墙特征键（付费墙通用语言 app:<id>/llm:tier/webapp:hosting；集中注册表 feature_registry 校验）';

-- quota_json：购买/授予时从 plan.quota_json 固化的配额快照（本周期内不随改价变动）
ALTER TABLE "hasn_app_entitlement"
  ADD COLUMN IF NOT EXISTS "quota_json" jsonb NOT NULL DEFAULT '{}';
COMMENT ON COLUMN "hasn_app_entitlement"."quota_json"
  IS '配额快照（购买/授予时从 plan.quota_json 固化，本权益周期内不随改价变动）';

-- 存量回填：既有应用权益的 feature_key = 'app:' || app_id（内核前所有权益都是应用权益）
UPDATE "hasn_app_entitlement"
  SET "feature_key" = 'app:' || "app_id"
  WHERE ("feature_key" = '' OR "feature_key" IS NULL) AND "app_id" <> '';

-- 判定热路径索引：按 subject + feature_key 查生效权益
CREATE INDEX IF NOT EXISTS "idx_hasn_app_entitlement_subject_feature"
  ON "hasn_app_entitlement" ("subject_type", "subject_id", "feature_key");
