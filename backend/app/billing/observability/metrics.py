"""billing.observability.metrics · doc94 §7 履约可观测性指标全集。

NewAPI 是积分余额、用量、扣减与清零的唯一权威，云端只发命令、读回执。这套指标要能回答三个问题：

1. **有没有人付了钱没拿到额度**（`billing_paid_unfulfilled_total`、`credit_outbox_*`）；
2. **有没有重复发放或重复回收**（`newapi_credit_operation_idempotent_replay_total` /
   `..._idempotency_conflict_total`）；
3. **有没有人绕过权威直接改余额**（`legacy_credit_write_attempt_total`、
   `newapi_absolute_quota_write_total`，两者都必须恒为 0）。

**高基数 ID 铁律**：label 只取低基数枚举维度（error_code / failure_code / source / reason），
绝不放 user_id、order_no、event_id——那些只进脱敏日志与 trace。
"""

from __future__ import annotations

from prometheus_client import Counter, Gauge

# ── 支付与履约状态分离 ──────────────────────────────────────────────────────

# 已支付但尚未履约的订单数。任一订单停留超过 5 分钟即告警：
# 「页面显示支付成功、NewAPI 却没收到额度」正是本轮要消灭的故障形态。
BILLING_PAID_UNFULFILLED_TOTAL = Gauge(
    name='billing_paid_unfulfilled_total',
    documentation='已支付但未履约的订单数（payment_status=paid AND fulfillment_status NOT IN (succeeded, not_required)）',
)

# ── 履约 outbox ────────────────────────────────────────────────────────────

CREDIT_OUTBOX_PENDING_TOTAL = Gauge(
    name='credit_outbox_pending_total',
    documentation='待投递的履约事件数（status IN (pending, retrying, processing)）',
)

CREDIT_OUTBOX_OLDEST_AGE_SECONDS = Gauge(
    name='credit_outbox_oldest_age_seconds',
    documentation='最老的未完成履约事件已等待的秒数',
)

CREDIT_OUTBOX_RETRY_TOTAL = Counter(
    name='credit_outbox_retry_total',
    documentation='履约事件重试次数·按 error_code',
    labelnames=['error_code'],
)

CREDIT_OUTBOX_DEAD_TOTAL = Counter(
    name='credit_outbox_dead_total',
    documentation='进入 dead letter 的履约事件数·按 error_code',
    labelnames=['error_code'],
)

# ── NewAPI 履约回执 ────────────────────────────────────────────────────────

NEWAPI_CREDIT_OPERATION_IDEMPOTENT_REPLAY_TOTAL = Counter(
    name='newapi_credit_operation_idempotent_replay_total',
    documentation='NewAPI 幂等重放命中次数（同 event_id 同载荷）',
)

NEWAPI_CREDIT_OPERATION_IDEMPOTENCY_CONFLICT_TOTAL = Counter(
    name='newapi_credit_operation_idempotency_conflict_total',
    documentation='同 event_id 不同载荷导致的 409 冲突次数（调用方生成幂等键有误）',
)

# 终局业务失败计数，与瞬时重试分开观测：前者要人工介入，后者是正常退避。
NEWAPI_CREDIT_OPERATION_FAILED_TOTAL = Counter(
    name='newapi_credit_operation_failed_total',
    documentation='NewAPI 终局业务失败次数·按 failure_code',
    labelnames=['failure_code'],
)

NEWAPI_INSUFFICIENT_QUOTA_TOTAL = Counter(
    name='newapi_insufficient_quota_total',
    documentation='NewAPI 余额不足拒绝次数·按 source(subscription/wallet/composite)',
    labelnames=['source'],
)

NEWAPI_SUBSCRIPTION_RESET_TOTAL = Counter(
    name='newapi_subscription_reset_total',
    documentation='订阅周期清零重置次数',
)

NEWAPI_SUBSCRIPTION_EXPIRE_TOTAL = Counter(
    name='newapi_subscription_expire_total',
    documentation='订阅合同到期次数',
)

BILLING_CREDIT_ACCOUNT_UNAVAILABLE_TOTAL = Counter(
    name='billing_credit_account_unavailable_total',
    documentation='读取 NewAPI 权威账户失败次数（账单中心据此显示 unavailable，而不是回落旧值）',
)

# ── 防复发守卫（两条都必须恒为 0） ──────────────────────────────────────────

# 任何试图直接写云端余额表的调用。P0 之后云端已无余额权威，出现即为回归。
LEGACY_CREDIT_WRITE_ATTEMPT_TOTAL = Counter(
    name='legacy_credit_write_attempt_total',
    documentation='试图直接写云端旧余额表的次数·按 reason；必须恒为 0',
    labelnames=['reason'],
)

# 任何试图对 NewAPI「设置绝对 quota」的调用。这正是造成「超额仍可用」的反向数据流。
NEWAPI_ABSOLUTE_QUOTA_WRITE_TOTAL = Counter(
    name='newapi_absolute_quota_write_total',
    documentation='试图对 NewAPI 设置绝对 quota 的次数·按 reason；必须恒为 0',
    labelnames=['reason'],
)

CREDIT_RECONCILIATION_MISSING_PROJECTION_TOTAL = Counter(
    name='credit_reconciliation_missing_projection_total',
    documentation='对账发现的「云端有合同/事件但 NewAPI 无投影」条数；只触发事件重放，绝不触发余额覆盖',
)
