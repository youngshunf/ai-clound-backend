-- =====================================================
-- doc94 C1：合同与履约状态的 expand 迁移
--
-- 三件事：
--   1. pay_order / pay_refund 把「支付状态」与「履约状态」拆成两个可观察状态——
--      用户端只有 payment_status=paid AND fulfillment_status=succeeded 才显示「购买完成」，
--      否则显示「已付款，额度发放中」，绝不假报完成；
--   2. user_subscription 从「兼任余额表」改成纯商业合同表：加合同号、周期参数、
--      plan 快照与 NewAPI 投影标识；
--   3. 旧余额列本阶段**只读冻结、不 drop**（expand 阶段），drop 留给 D1，
--      且必须在 daemon 与 WebUI 改指之后。
--
-- 幂等：可重复执行。schema=hasn_billing。
-- 依赖：backend/sql/billing/credit_grant_event.sql 先建表。
-- =====================================================

-- ── pay_order：履约状态与支付状态分离 ────────────────────────────────────
ALTER TABLE "hasn_billing"."pay_order"
  ADD COLUMN IF NOT EXISTS "fulfillment_status"     varchar(16) NOT NULL DEFAULT 'not_required',
  ADD COLUMN IF NOT EXISTS "fulfilled_at"           timestamptz(6),
  ADD COLUMN IF NOT EXISTS "fulfillment_error_code" varchar(64),
  ADD COLUMN IF NOT EXISTS "fulfillment_event_id"   varchar(36);

COMMENT ON COLUMN "hasn_billing"."pay_order"."fulfillment_status"
  IS '履约状态 (not_required:无需履约:grey/pending:待履约:blue/processing:履约中:orange/succeeded:已到账:green/retrying:重试中:orange/dead:死信:red/reversed:已回收:grey)';
COMMENT ON COLUMN "hasn_billing"."pay_order"."fulfilled_at" IS '履约完成时间（额度真正到账的时刻）';
COMMENT ON COLUMN "hasn_billing"."pay_order"."fulfillment_error_code" IS '履约失败机器码';
COMMENT ON COLUMN "hasn_billing"."pay_order"."fulfillment_event_id" IS '关联的履约事件 ID';

CREATE INDEX IF NOT EXISTS "ix_pay_order_fulfillment_status"
  ON "hasn_billing"."pay_order" ("fulfillment_status");

-- ── pay_refund：额度回收与失败补偿 ──────────────────────────────────────
ALTER TABLE "hasn_billing"."pay_refund"
  ADD COLUMN IF NOT EXISTS "fulfillment_status"  varchar(16) NOT NULL DEFAULT 'not_required',
  ADD COLUMN IF NOT EXISTS "revoke_event_id"     varchar(36),
  ADD COLUMN IF NOT EXISTS "compensate_event_id" varchar(36);

COMMENT ON COLUMN "hasn_billing"."pay_refund"."fulfillment_status"
  IS '额度回收状态 (not_required:无需回收:grey/pending:待回收:blue/processing:回收中:orange/succeeded:已回收:green/retrying:重试中:orange/dead:死信:red)';
COMMENT ON COLUMN "hasn_billing"."pay_refund"."revoke_event_id" IS '额度回收事件 ID';
COMMENT ON COLUMN "hasn_billing"."pay_refund"."compensate_event_id" IS '渠道退款失败后的反向补偿事件 ID';

-- ── user_subscription：改成商业合同表 ───────────────────────────────────
ALTER TABLE "hasn_billing"."user_subscription"
  ADD COLUMN IF NOT EXISTS "contract_no"              varchar(64),
  ADD COLUMN IF NOT EXISTS "offering_key"             varchar(64),
  ADD COLUMN IF NOT EXISTS "plan_key"                 varchar(64),
  ADD COLUMN IF NOT EXISTS "contract_start_at"        timestamptz(6),
  ADD COLUMN IF NOT EXISTS "contract_end_at"          timestamptz(6),
  ADD COLUMN IF NOT EXISTS "cycle_seconds"            bigint      NOT NULL DEFAULT 2592000,
  ADD COLUMN IF NOT EXISTS "cycle_count"              integer,
  ADD COLUMN IF NOT EXISTS "plan_snapshot"            jsonb,
  ADD COLUMN IF NOT EXISTS "source_order_no"          varchar(64),
  ADD COLUMN IF NOT EXISTS "external_subscription_id" varchar(128),
  ADD COLUMN IF NOT EXISTS "fulfillment_status"       varchar(16) NOT NULL DEFAULT 'not_required',
  ADD COLUMN IF NOT EXISTS "free_policy_version"      integer     NOT NULL DEFAULT 1,
  ADD COLUMN IF NOT EXISTS "free_grant_epoch"         integer     NOT NULL DEFAULT 0;

COMMENT ON COLUMN "hasn_billing"."user_subscription"."contract_no" IS '稳定合同号（幂等键组件）';
COMMENT ON COLUMN "hasn_billing"."user_subscription"."offering_key" IS '商品目录引用';
COMMENT ON COLUMN "hasn_billing"."user_subscription"."plan_key" IS '商品档位引用';
COMMENT ON COLUMN "hasn_billing"."user_subscription"."contract_start_at" IS '合同起始时间';
COMMENT ON COLUMN "hasn_billing"."user_subscription"."contract_end_at" IS '合同结束时间（免费档为空，表示无商业到期）';
COMMENT ON COLUMN "hasn_billing"."user_subscription"."cycle_seconds" IS '周期长度秒数，恒为 2592000（30 天），含免费档';
COMMENT ON COLUMN "hasn_billing"."user_subscription"."cycle_count" IS '周期数：月付 1、年付 12、免费档为空（无限期循环）';
COMMENT ON COLUMN "hasn_billing"."user_subscription"."plan_snapshot" IS '购买时固化的合同参数（积分额度、Agent 数、价格等）';
COMMENT ON COLUMN "hasn_billing"."user_subscription"."source_order_no" IS '创建本合同的支付订单号（免费档可空）';
COMMENT ON COLUMN "hasn_billing"."user_subscription"."external_subscription_id" IS 'NewAPI 投影标识（不是余额）';
COMMENT ON COLUMN "hasn_billing"."user_subscription"."fulfillment_status"
  IS '履约状态 (not_required:无需履约:grey/pending:待履约:blue/processing:履约中:orange/succeeded:已生效:green/retrying:重试中:orange/dead:死信:red)';
COMMENT ON COLUMN "hasn_billing"."user_subscription"."free_policy_version" IS '免费政策版本，随免费政策变更递增';
COMMENT ON COLUMN "hasn_billing"."user_subscription"."free_grant_epoch" IS '免费额度授予轮次，每次「失效→重新授予」+1；缺它则政策撤销后永远发不出第二次';

-- 合同号与 NewAPI 投影标识唯一。允许多行为 NULL（存量合同尚未回填），
-- PostgreSQL 的 UNIQUE 索引本就允许多个 NULL，无需部分索引。
CREATE UNIQUE INDEX IF NOT EXISTS "uk_user_subscription_contract_no"
  ON "hasn_billing"."user_subscription" ("contract_no");
CREATE UNIQUE INDEX IF NOT EXISTS "uk_user_subscription_external_id"
  ON "hasn_billing"."user_subscription" ("external_subscription_id");

-- 存量约束 uq_user_subscription_user_app 把「一个用户在一个应用下只能有一行订阅」写死了，
-- 与合同表模型直接冲突：合同要留历史（expired/cancelled/refunded），提前续费还要多出一份
-- scheduled 未来合同。因此把「全局唯一」换成下面两条**按状态**的部分唯一索引：
-- 同一时刻至多一份 active、至多一份 scheduled，历史合同不受限。
-- ⚠️ 读路径从此必须按 status 选当前合同，不能再假设「查出来只有一行」。
ALTER TABLE "hasn_billing"."user_subscription"
  DROP CONSTRAINT IF EXISTS "uq_user_subscription_user_app";

-- 同用户同应用只允许一份「在生效窗口内」的合同（active）与一份未来合同（scheduled）。
-- 升级/续费必须显式处理旧合同，不能靠再插一行绕过。
-- 「取消自动续费」的合同**仍然生效**（用户已付过这一期的钱），因此必须与 active
-- 一起纳入唯一约束，否则用户取消续费后再买一份就会同时持有两个可用订阅池。
CREATE UNIQUE INDEX IF NOT EXISTS "uk_user_subscription_one_active"
  ON "hasn_billing"."user_subscription" ("app_code", "user_id")
  WHERE "status" IN ('active', 'cancel_at_period_end');
CREATE UNIQUE INDEX IF NOT EXISTS "uk_user_subscription_one_scheduled"
  ON "hasn_billing"."user_subscription" ("app_code", "user_id")
  WHERE "status" = 'scheduled';

CREATE INDEX IF NOT EXISTS "ix_user_subscription_fulfillment_status"
  ON "hasn_billing"."user_subscription" ("fulfillment_status");

-- 周期口径回填：存量合同一律按 30 天固定周期表达。
-- 年付按 12 期、月付按 1 期；免费档 cycle_count 留空表示无限期循环。
UPDATE "hasn_billing"."user_subscription"
   SET "cycle_seconds" = 2592000
 WHERE "cycle_seconds" IS DISTINCT FROM 2592000;

UPDATE "hasn_billing"."user_subscription"
   SET "cycle_count" = CASE
         WHEN "tier" = 'free' THEN NULL
         WHEN "subscription_type" = 'yearly' THEN 12
         ELSE 1
       END
 WHERE "cycle_count" IS NULL
   AND "tier" IS NOT NULL;

-- 合同时间锚点回填：沿用既有订阅起止时间，不新造事实。
UPDATE "hasn_billing"."user_subscription"
   SET "contract_start_at" = "subscription_start_date"
 WHERE "contract_start_at" IS NULL
   AND "subscription_start_date" IS NOT NULL;

UPDATE "hasn_billing"."user_subscription"
   SET "contract_end_at" = "subscription_end_date"
 WHERE "contract_end_at" IS NULL
   AND "subscription_end_date" IS NOT NULL;

-- 注意：旧余额列（monthly_credits/current_credits/used_credits/purchased_credits/next_grant_date）
-- 在本阶段**只读冻结**，不在此处 drop。drop 属于 D1 contract migration，
-- 且必须排在「daemon 路由改指 + WebUI hook 切换」之后，否则会同时打断两层。
