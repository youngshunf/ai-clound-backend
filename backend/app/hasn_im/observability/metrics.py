"""hasn_im.observability.metrics · §12.2 指标全集 + 初始门槛（R2-14）

doc16 §12.2 定义的云端 IM 服务化核心指标全集，逐条 1:1 落为 prometheus_client 指标对象，注册进
默认 REGISTRY，经既有 `/metrics` 端点导出（同 `common/observability/prometheus.py` 的 push.* 范式：
业务语义指标不带 `fba_` 前缀）。

**高基数 ID 铁律（§12.2）**：`高基数 ID 不得作为 metric label，只进入脱敏 trace/log`。故本模块所有
label 仅取**低基数枚举维度**（stage/result/consumer/producer），绝不含 owner_id/conversation_id/
hasn_id/event_id 等高基数 ID——守卫测试 `test_observability_metrics.py` 会逐指标校验 label 名不落
在禁用 ID 前缀集合，防止有人后续图省事把 ID 塞进 label 把时序炸掉。

**初始门槛（§12.2「建议初始门槛」）**：以 `INITIAL_THRESHOLDS` 常量成文，供告警规则与 Runbook 引用，
不散落硬编码。R2 演练可据此校准，R3 前固化进告警配置。

**接线点（wiring，供各 R2 服务在热路径调用）**：
- `hasn_im_send_latency_seconds{stage,result}`：send 契约每阶段（validate/persist/append/commit）耗时；
- `hasn_im_idempotency_total{result}`：幂等命中（deduped）/新建（created）计数——append/ensure-direct 处；
- `hasn_im_integration_event_head`：integration_events 分片头 event_seq（消费者水位对账用）；
- `hasn_im_consumer_lag{consumer}` / `_failure_total` / `hasn_im_dead_letter_total{consumer}`：消费者框架；
- `hasn_sync_projection_lag_seconds{producer}` / `hasn_sync_pull_lag_seconds` /
  `hasn_sync_cursor_expired_total`：sync 内核；
- `hasn_realtime_delivery_total{result}` / `hasn_push_delivery_total{result}`：best-effort 两消费者；
- `hasn_producer_outbox_oldest_age_seconds{producer}`：业务 outbox relay；
- `hasn_unread_reconcile_mismatch_total`：R4 unread reconciler 对账不一致计数。
"""

from __future__ import annotations

from prometheus_client import Counter, Gauge, Histogram

# ── send 路径 ──────────────────────────────────────────────────────────────

# send commit + ACK 各阶段耗时（p95/p99 门槛：不劣于现有基线）
HASN_IM_SEND_LATENCY_SECONDS = Histogram(
    name='hasn_im_send_latency_seconds',
    documentation='IM 严格 send 契约各阶段耗时（秒）·按 stage(validate/persist/append/commit) 与 result(ok/error)',
    labelnames=['stage', 'result'],
)

# 幂等判定结果：created(新建) / deduped(命中既有)
HASN_IM_IDEMPOTENCY_TOTAL = Counter(
    name='hasn_im_idempotency_total',
    documentation='IM 幂等键判定计数·按 result(created/deduped)',
    labelnames=['result'],
)

# ── integration event 日志 / 消费者 ─────────────────────────────────────────

# integration_events 当前分片头 event_seq（消费者水位对账基线）
HASN_IM_INTEGRATION_EVENT_HEAD = Gauge(
    name='hasn_im_integration_event_head',
    documentation='integration_events 当前最大 event_seq（分片头·消费者水位对账基线）',
)

# 各消费者滞后（head - last_acked_seq）
HASN_IM_CONSUMER_LAG = Gauge(
    name='hasn_im_consumer_lag',
    documentation='IM integration event 消费者滞后（head - last_acked_seq）·按 consumer',
    labelnames=['consumer'],
)

# 各消费者失败计数（durable 消费者失败即需关注）
HASN_IM_CONSUMER_FAILURE_TOTAL = Counter(
    name='hasn_im_consumer_failure_total',
    documentation='IM 消费者处理失败计数·按 consumer',
    labelnames=['consumer'],
)

# 各消费者 dead letter 计数（重试耗尽入 DLQ）
HASN_IM_DEAD_LETTER_TOTAL = Counter(
    name='hasn_im_dead_letter_total',
    documentation='IM 消费者 dead letter（重试耗尽入 DLQ）计数·按 consumer',
    labelnames=['consumer'],
)

# ── sync 内核 ───────────────────────────────────────────────────────────────

# sync 投影滞后（秒）·按 producer（p99 门槛 ≤5s）
HASN_SYNC_PROJECTION_LAG_SECONDS = Gauge(
    name='hasn_sync_projection_lag_seconds',
    documentation='sync 投影滞后（秒）·按 producer',
    labelnames=['producer'],
)

# daemon 周期 pull 滞后（秒）
HASN_SYNC_PULL_LAG_SECONDS = Gauge(
    name='hasn_sync_pull_lag_seconds',
    documentation='daemon sync 周期 pull 滞后（秒）',
)

# sync cursor 过期计数（daemon full-refresh 触发信号）
HASN_SYNC_CURSOR_EXPIRED_TOTAL = Counter(
    name='hasn_sync_cursor_expired_total',
    documentation='sync cursor 过期（触发 daemon full-refresh）计数',
)

# ── best-effort 投递（realtime / push） ─────────────────────────────────────

HASN_REALTIME_DELIVERY_TOTAL = Counter(
    name='hasn_realtime_delivery_total',
    documentation='realtime（WS）投递计数·按 result(ok/miss/error)',
    labelnames=['result'],
)

# dual 模式离线恢复影子对账；稳定 ID 只在 Redis/PG 内比较，不进入标签。
HASN_OFFLINE_SHADOW_MESSAGES = Gauge(
    name='hasn_offline_shadow_messages',
    documentation='Redis offline 与 PostgreSQL sync 七天影子对账数量·按低基数结果分类',
    labelnames=['result'],
)

# realtime wake-up transport 迁移指标；event_id 只进日志和内存对账，不进入 label。
HASN_REALTIME_WAKEUP_PUBLISH_TOTAL = Counter(
    name='hasn_realtime_wakeup_publish_total',
    documentation='realtime wake-up 发布计数·按 transport 与 result',
    labelnames=['transport', 'result'],
)

HASN_REALTIME_WAKEUP_CONSUME_TOTAL = Counter(
    name='hasn_realtime_wakeup_consume_total',
    documentation='realtime wake-up 消费计数·按 transport 与 result',
    labelnames=['transport', 'result'],
)

HASN_REALTIME_WAKEUP_SCHEMA_ERROR_TOTAL = Counter(
    name='hasn_realtime_wakeup_schema_error_total',
    documentation='realtime wake-up schema 非法计数·按 transport',
    labelnames=['transport'],
)

HASN_REALTIME_WAKEUP_LATENCY_SECONDS = Histogram(
    name='hasn_realtime_wakeup_latency_seconds',
    documentation='realtime wake-up 从发布到消费的端到端延迟（秒）·按 transport',
    labelnames=['transport'],
)

REALTIME_WAKEUP_METRICS = (
    HASN_REALTIME_WAKEUP_PUBLISH_TOTAL,
    HASN_REALTIME_WAKEUP_CONSUME_TOTAL,
    HASN_REALTIME_WAKEUP_SCHEMA_ERROR_TOTAL,
    HASN_REALTIME_WAKEUP_LATENCY_SECONDS,
)

HASN_PUSH_DELIVERY_TOTAL = Counter(
    name='hasn_push_delivery_total',
    documentation='push（离线推送）投递计数·按 result(ok/miss/error)',
    labelnames=['result'],
)

# ── 业务 outbox / unread 对账 ───────────────────────────────────────────────

# 业务生产方 outbox 最老未投递条目年龄（秒）·按 producer（系统通知默认门槛 ≤30s）
HASN_PRODUCER_OUTBOX_OLDEST_AGE_SECONDS = Gauge(
    name='hasn_producer_outbox_oldest_age_seconds',
    documentation='业务生产方 outbox 最老未投递条目年龄（秒）·按 producer',
    labelnames=['producer'],
)

# unread reconciler 对账不一致计数（R4）
HASN_UNREAD_RECONCILE_MISMATCH_TOTAL = Counter(
    name='hasn_unread_reconcile_mismatch_total',
    documentation='unread 权威游标与投影对账不一致计数（reconciler 修复信号）',
)


# 指标全集清单（供守卫测试内省 + Runbook/告警引用）：逐条对应 §12.2 一行。
IM_METRICS = (
    HASN_IM_SEND_LATENCY_SECONDS,
    HASN_IM_IDEMPOTENCY_TOTAL,
    HASN_IM_INTEGRATION_EVENT_HEAD,
    HASN_IM_CONSUMER_LAG,
    HASN_IM_CONSUMER_FAILURE_TOTAL,
    HASN_IM_DEAD_LETTER_TOTAL,
    HASN_SYNC_PROJECTION_LAG_SECONDS,
    HASN_SYNC_PULL_LAG_SECONDS,
    HASN_SYNC_CURSOR_EXPIRED_TOTAL,
    HASN_REALTIME_DELIVERY_TOTAL,
    HASN_OFFLINE_SHADOW_MESSAGES,
    *REALTIME_WAKEUP_METRICS,
    HASN_PUSH_DELIVERY_TOTAL,
    HASN_PRODUCER_OUTBOX_OLDEST_AGE_SECONDS,
    HASN_UNREAD_RECONCILE_MISMATCH_TOTAL,
)

# §12.2「建议初始门槛」—— 成文为常量，供告警规则与 §12.3 Runbook 引用，不散落硬编码。
# 值语义见各键注释；R2 演练校准、R3 前固化进告警配置。
INITIAL_THRESHOLDS = {
    # sync projection lag p99 ≤ 5 秒
    'sync_projection_lag_p99_seconds': 5.0,
    # integration consumer oldest pending ≤ 30 秒，超过告警
    'integration_consumer_oldest_pending_seconds': 30.0,
    # 业务 outbox oldest pending：系统通知默认 ≤ 30 秒（其余按业务等级另配）
    'producer_outbox_oldest_pending_seconds_default': 30.0,
    # 权限越权、message/event 永久缺失：0 容忍
    'permission_violation_or_permanent_loss': 0,
    # send commit + ACK p95/p99：不劣于现有基线（无绝对值，占位表述，R2 演练取基线后填）
    'send_commit_ack_p95_p99': 'no_worse_than_baseline',
}
