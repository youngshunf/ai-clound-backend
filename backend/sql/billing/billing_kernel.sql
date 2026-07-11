-- =====================================================
-- 统一商业化内核数据层（模块 16 doc02 §3.2）
-- 商品目录：一切可售卖物统一登记为 offering + plan
-- schema=hasn_billing（对齐 billing 模块既有约定，见 model/_base.py）
-- 设计事实源：docs/hasn-node设计文档/16-订阅与积分计费/02-统一商业化内核设计.md
-- 施工权威：同模块 实施/92 MK-1
-- =====================================================

-- 商品目录：offering（一切可售卖物的稳定业务身份）
CREATE TABLE IF NOT EXISTS "hasn_billing"."billing_offering" (
  "id"            bigserial      PRIMARY KEY,
  "key"           varchar(64)    NOT NULL UNIQUE,
  "kind"          varchar(16)    NOT NULL,
  "feature_key"   varchar(64)    NOT NULL,
  "display_name"  varchar(128)   NOT NULL,
  "status"        varchar(16)    NOT NULL DEFAULT 'active',
  "source"        varchar(32)    NOT NULL DEFAULT 'platform',
  "sort_order"    int4           NOT NULL DEFAULT 0,
  "created_time"  timestamptz(6) NOT NULL DEFAULT NOW(),
  "updated_time"  timestamptz(6)
);

COMMENT ON TABLE "hasn_billing"."billing_offering" IS '商品目录（一切可售卖物：LLM档/积分包/应用/席位/应用内档位）';
COMMENT ON COLUMN "hasn_billing"."billing_offering"."id" IS '主键 ID';
COMMENT ON COLUMN "hasn_billing"."billing_offering"."key" IS '商品业务键（全端稳定，如 app:quant / llm:tier / webapp:hosting）';
COMMENT ON COLUMN "hasn_billing"."billing_offering"."kind" IS '商品种类 (llm_tier:LLM订阅档:blue/credit_pack:积分包:cyan/app:应用:green/seat:企业席位:purple/feature_plan:功能档位:orange)';
COMMENT ON COLUMN "hasn_billing"."billing_offering"."feature_key" IS '付费墙特征键（付费墙通用语言，如 app:<id> / llm:tier / webapp:hosting；集中注册表 feature_registry 校验）';
COMMENT ON COLUMN "hasn_billing"."billing_offering"."display_name" IS '显示名称';
COMMENT ON COLUMN "hasn_billing"."billing_offering"."status" IS '状态 (active:上架:green/inactive:下架:gray)';
COMMENT ON COLUMN "hasn_billing"."billing_offering"."source" IS '商品来源（预留分成维度，platform:平台自营）';
COMMENT ON COLUMN "hasn_billing"."billing_offering"."sort_order" IS '排序权重';
COMMENT ON COLUMN "hasn_billing"."billing_offering"."created_time" IS '创建时间';
COMMENT ON COLUMN "hasn_billing"."billing_offering"."updated_time" IS '更新时间';

-- 商品档位：plan（价格 + 配额快照 + 试用/宽限策略；改价只影响新购续费）
CREATE TABLE IF NOT EXISTS "hasn_billing"."billing_plan" (
  "id"            bigserial      PRIMARY KEY,
  "offering_key"  varchar(64)    NOT NULL,
  "plan_key"      varchar(64)    NOT NULL,
  "price_amount"  numeric(10, 2) NOT NULL DEFAULT 0,
  "price_unit"    varchar(16)    NOT NULL DEFAULT 'cny',
  "cycle"         varchar(16)    NOT NULL DEFAULT 'once',
  "quota_json"    jsonb          NOT NULL DEFAULT '{}',
  "trial_json"    jsonb          NOT NULL DEFAULT '{}',
  "grace_json"    jsonb          NOT NULL DEFAULT '{}',
  "status"        varchar(16)    NOT NULL DEFAULT 'active',
  "sort_order"    int4           NOT NULL DEFAULT 0,
  "created_time"  timestamptz(6) NOT NULL DEFAULT NOW(),
  "updated_time"  timestamptz(6)
);

-- 同一 offering 下 plan_key 唯一
CREATE UNIQUE INDEX IF NOT EXISTS "uq_billing_plan_offering_plan"
  ON "hasn_billing"."billing_plan" ("offering_key", "plan_key");
CREATE INDEX IF NOT EXISTS "idx_billing_plan_offering"
  ON "hasn_billing"."billing_plan" ("offering_key");

COMMENT ON TABLE "hasn_billing"."billing_plan" IS '商品档位（价格+配额快照+试用/宽限策略）';
COMMENT ON COLUMN "hasn_billing"."billing_plan"."id" IS '主键 ID';
COMMENT ON COLUMN "hasn_billing"."billing_plan"."offering_key" IS '所属 offering 业务键（指向 billing_offering.key）';
COMMENT ON COLUMN "hasn_billing"."billing_plan"."plan_key" IS '档位键（如 monthly/yearly/standard/once）';
COMMENT ON COLUMN "hasn_billing"."billing_plan"."price_amount" IS '价格（price_unit 单位，如元；改价只影响新购续费）';
COMMENT ON COLUMN "hasn_billing"."billing_plan"."price_unit" IS '计价单位 (cny:人民币元:blue/credits:积分:cyan)';
COMMENT ON COLUMN "hasn_billing"."billing_plan"."cycle" IS '计费周期 (once:一次买断:gray/month:月:blue/year:年:green)';
COMMENT ON COLUMN "hasn_billing"."billing_plan"."quota_json" IS '配额包快照（站点数/内存/卷/席位数/max_agents…；购买时固化进权益行）';
COMMENT ON COLUMN "hasn_billing"."billing_plan"."trial_json" IS '试用策略（enabled/days/times）';
COMMENT ON COLUMN "hasn_billing"."billing_plan"."grace_json" IS '宽限策略（remind_days/grace_days，到期提醒节奏+宽限天数）';
COMMENT ON COLUMN "hasn_billing"."billing_plan"."status" IS '状态 (active:上架:green/inactive:下架:gray)';
COMMENT ON COLUMN "hasn_billing"."billing_plan"."sort_order" IS '排序权重';
COMMENT ON COLUMN "hasn_billing"."billing_plan"."created_time" IS '创建时间';
COMMENT ON COLUMN "hasn_billing"."billing_plan"."updated_time" IS '更新时间';
