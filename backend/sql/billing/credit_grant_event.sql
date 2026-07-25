-- =====================================================
-- doc94 C1：履约事件表（事务 outbox + 云端审计）
--
-- 它同时承担两个角色，但都不是「积分流水」：
--   1. **事务 outbox**：云端事务里只写命令（status=pending），提交后由 worker 投递给 NewAPI；
--      支付回调的数据库事务里绝不发 HTTP。
--   2. **云端审计**：记录发了什么命令、NewAPI 回执是什么、失败在哪一步。
--
-- 铁律：本表**不保存权威余额**。credit_amount 是不可变的发放/回收参数，
-- applied_credits 是 NewAPI 回执的实际入账额；两者都不能被用来推算用户余额——
-- 余额只有 NewAPI 一个权威。
--
-- 幂等键取自 doc94 §2.1 的固定全集，不得现场自创：
--   payment:{order_no}:wallet
--   subscription:{contract_no}:activate | subscription:{contract_no}:expire
--   refund:{refund_no}:wallet-revoke | refund:{refund_no}:subscription-expire
--   compensation:{refund_no}:wallet-restore
--   free:{user_id}:{policy_version}:{epoch}:activate
--   admin:{grant_no}:wallet-grant | admin:{revoke_no}:wallet-revoke
--   bonus:{campaign_key}:{campaign_version}:{user_id}
--
-- 幂等：可重复执行。schema=hasn_billing。
-- =====================================================

CREATE TABLE IF NOT EXISTS "hasn_billing"."credit_grant_event" (
  "id"                  bigserial PRIMARY KEY,
  "event_id"            varchar(36)  NOT NULL,
  "idempotency_key"     varchar(160) NOT NULL,
  "event_type"          varchar(32)  NOT NULL,
  "app_code"            varchar(32)  NOT NULL DEFAULT 'huanxing',
  "user_id"             bigint       NOT NULL,
  "newapi_user_id"      bigint       NOT NULL,
  "order_no"            varchar(64),
  "refund_no"           varchar(64),
  "subscription_id"     bigint,
  "contract_no"         varchar(64),
  "credit_amount"       numeric(18,5),
  "applied_credits"     numeric(18,5),
  "payload"             jsonb        NOT NULL DEFAULT '{}'::jsonb,
  "payload_hash"        varchar(64)  NOT NULL DEFAULT '',
  "status"              varchar(16)  NOT NULL DEFAULT 'pending',
  "attempt_count"       integer      NOT NULL DEFAULT 0,
  "next_attempt_at"     timestamptz(6),
  "last_error_code"     varchar(64),
  "last_error_message"  text,
  "response_snapshot"   jsonb,
  "delivered_at"        timestamptz(6),
  "created_time"        timestamptz(6) NOT NULL DEFAULT NOW(),
  "updated_time"        timestamptz(6)
);

COMMENT ON TABLE  "hasn_billing"."credit_grant_event" IS '履约事件表（事务 outbox + 云端审计，不保存权威余额）';
COMMENT ON COLUMN "hasn_billing"."credit_grant_event"."id" IS '主键 ID';
COMMENT ON COLUMN "hasn_billing"."credit_grant_event"."event_id" IS '投递给 NewAPI 的 event_id（UUID 字符串，全局唯一；超时重投必须复用同一个，禁止换 ID 重发）';
COMMENT ON COLUMN "hasn_billing"."credit_grant_event"."idempotency_key" IS '业务幂等键（取自固定全集，不得现场自创）';
COMMENT ON COLUMN "hasn_billing"."credit_grant_event"."event_type" IS '事件类型 (wallet_grant:钱包发放:green/wallet_revoke:钱包回收:orange/subscription_activate:订阅生效:blue/subscription_expire:订阅到期:grey)';
COMMENT ON COLUMN "hasn_billing"."credit_grant_event"."app_code" IS '应用标识';
COMMENT ON COLUMN "hasn_billing"."credit_grant_event"."user_id" IS '唤星用户 ID';
COMMENT ON COLUMN "hasn_billing"."credit_grant_event"."newapi_user_id" IS '履约目标 NewAPI 用户 ID（快照）';
COMMENT ON COLUMN "hasn_billing"."credit_grant_event"."order_no" IS '关联支付订单号';
COMMENT ON COLUMN "hasn_billing"."credit_grant_event"."refund_no" IS '关联退款单号';
COMMENT ON COLUMN "hasn_billing"."credit_grant_event"."subscription_id" IS '关联订阅合同主键';
COMMENT ON COLUMN "hasn_billing"."credit_grant_event"."contract_no" IS '关联订阅合同号';
COMMENT ON COLUMN "hasn_billing"."credit_grant_event"."credit_amount" IS '不可变的发放/回收参数积分数（不是余额）';
COMMENT ON COLUMN "hasn_billing"."credit_grant_event"."applied_credits" IS 'NewAPI 回执的实际入账/回收积分（审计以此为准）';
COMMENT ON COLUMN "hasn_billing"."credit_grant_event"."payload" IS '投递给 NewAPI 的请求快照';
COMMENT ON COLUMN "hasn_billing"."credit_grant_event"."payload_hash" IS '投递载荷指纹，用于冲突检测';
COMMENT ON COLUMN "hasn_billing"."credit_grant_event"."status" IS '状态 (pending:待投递:blue/processing:投递中:orange/succeeded:已完成:green/retrying:重试中:orange/dead:死信:red/cancelled:已取消:grey)';
COMMENT ON COLUMN "hasn_billing"."credit_grant_event"."attempt_count" IS '已投递尝试次数';
COMMENT ON COLUMN "hasn_billing"."credit_grant_event"."next_attempt_at" IS '下次投递时间（指数退避 + 抖动）';
COMMENT ON COLUMN "hasn_billing"."credit_grant_event"."last_error_code" IS '最后一次失败的机器错误码';
COMMENT ON COLUMN "hasn_billing"."credit_grant_event"."last_error_message" IS '最后一次失败原因（敏感值已脱敏）';
COMMENT ON COLUMN "hasn_billing"."credit_grant_event"."response_snapshot" IS 'NewAPI 回执快照，仅供排障，不得用于判余额';
COMMENT ON COLUMN "hasn_billing"."credit_grant_event"."delivered_at" IS '投递成功时间';
COMMENT ON COLUMN "hasn_billing"."credit_grant_event"."created_time" IS '创建时间';
COMMENT ON COLUMN "hasn_billing"."credit_grant_event"."updated_time" IS '更新时间';

-- event_id 唯一：它是 NewAPI 侧幂等履约的资源 ID，超时重投必须复用同一个，禁止换 ID 重发。
CREATE UNIQUE INDEX IF NOT EXISTS "uk_credit_grant_event_event_id"
  ON "hasn_billing"."credit_grant_event" ("event_id");

-- 幂等键唯一：同一业务动作重复触发只能留下一条命令。
CREATE UNIQUE INDEX IF NOT EXISTS "uk_credit_grant_event_idempotency"
  ON "hasn_billing"."credit_grant_event" ("idempotency_key");

-- outbox worker 的抢占扫描：按状态 + 下次投递时间取任务。
CREATE INDEX IF NOT EXISTS "ix_credit_grant_event_pending_scan"
  ON "hasn_billing"."credit_grant_event" ("status", "next_attempt_at");

CREATE INDEX IF NOT EXISTS "ix_credit_grant_event_user"
  ON "hasn_billing"."credit_grant_event" ("user_id");
CREATE INDEX IF NOT EXISTS "ix_credit_grant_event_order_no"
  ON "hasn_billing"."credit_grant_event" ("order_no");
CREATE INDEX IF NOT EXISTS "ix_credit_grant_event_refund_no"
  ON "hasn_billing"."credit_grant_event" ("refund_no");
CREATE INDEX IF NOT EXISTS "ix_credit_grant_event_contract_no"
  ON "hasn_billing"."credit_grant_event" ("contract_no");
