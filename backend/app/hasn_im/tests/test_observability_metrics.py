"""R2-14 验收：§12.2 指标全集 + label 卫生 + 初始门槛（纯内省·无需 PG）。

钉死三件事，任一漂移即红：
1. **指标全集**：`IM_METRICS` 逐条暴露名与 §12.2 清单**一一对应**（不多不少），且 label 集合精确匹配；
2. **高基数 ID 铁律**：所有 label 名不落在禁用 ID 前缀集合（owner/conversation/hasn/event/... _id）——
   §12.2「高基数 ID 不得作为 metric label」；有人后续把 ID 塞进 label 立即被拦；
3. **可见性**：清单内全部指标都注册进 prometheus 默认 REGISTRY（经 /metrics 可导出）；
4. **初始门槛**：`INITIAL_THRESHOLDS` 含 §12.2「建议初始门槛」各键，供告警/Runbook 引用。
"""

from __future__ import annotations

import pytest

from prometheus_client import REGISTRY, Counter
from prometheus_client.metrics import MetricWrapperBase

from backend.app.hasn_im.observability import metrics as m

# §12.2 指标全集期望表：暴露名 → (类型标记, label 集合)。逐行对应设计 §12.2 代码块。
EXPECTED = {
    'hasn_im_send_latency_seconds': ('histogram', {'stage', 'result'}),
    'hasn_im_idempotency_total': ('counter', {'result'}),
    'hasn_im_integration_event_head': ('gauge', set()),
    'hasn_im_consumer_lag': ('gauge', {'consumer'}),
    'hasn_im_consumer_failure_total': ('counter', {'consumer'}),
    'hasn_im_dead_letter_total': ('counter', {'consumer'}),
    'hasn_sync_projection_lag_seconds': ('gauge', {'producer'}),
    'hasn_sync_pull_lag_seconds': ('gauge', set()),
    'hasn_sync_cursor_expired_total': ('counter', set()),
    'hasn_realtime_delivery_total': ('counter', {'result'}),
    'hasn_offline_shadow_messages': ('gauge', {'result'}),
    'hasn_realtime_wakeup_publish_total': ('counter', {'transport', 'result'}),
    'hasn_realtime_wakeup_consume_total': ('counter', {'transport', 'result'}),
    'hasn_realtime_wakeup_schema_error_total': ('counter', {'transport'}),
    'hasn_realtime_wakeup_latency_seconds': ('histogram', {'transport'}),
    'hasn_rabbitmq_publish_confirm_total': ('counter', {'result'}),
    'hasn_rabbitmq_delivery_ack_total': ('counter', {'result'}),
    'hasn_rabbitmq_redelivery_total': ('counter', set()),
    'hasn_push_delivery_total': ('counter', {'result'}),
    'hasn_producer_outbox_oldest_age_seconds': ('gauge', {'producer'}),
    'hasn_unread_reconcile_mismatch_total': ('counter', set()),
}

# 禁用的高基数 ID label 名（前缀/全名）：出现即违反 §12.2 铁律。
_FORBIDDEN_ID_LABELS = {
    'owner_id',
    'conversation_id',
    'hasn_id',
    'agent_id',
    'message_id',
    'event_id',
    'user_id',
    'aggregate_id',
    'source_event_id',
    'trace_id',
    'owner',
    'conversation',
    'hasn',
    'message',
    'aggregate',
}


def _exposed_name(metric: MetricWrapperBase) -> str:
    """还原指标的**暴露序列名**：Counter 暴露 `_name`+`_total`，Gauge/Histogram 即 `_name`。"""
    return f'{metric._name}_total' if isinstance(metric, Counter) else metric._name


def test_metric_set_matches_spec_exactly() -> None:
    """IM_METRICS 暴露名集合与 §12.2 清单**恰好一致**（不多不少）。"""
    actual = {_exposed_name(met) for met in m.IM_METRICS}
    assert actual == set(EXPECTED), f'§12.2 指标集漂移：缺 {set(EXPECTED) - actual}，多 {actual - set(EXPECTED)}'
    # 数量与清单条目数一致（防重复登记同名对象）
    assert len(m.IM_METRICS) == len(EXPECTED)


def test_each_metric_labels_match_spec() -> None:
    """逐指标：label 集合精确匹配 §12.2。"""
    for met in m.IM_METRICS:
        name = _exposed_name(met)
        _kind, want_labels = EXPECTED[name]
        assert set(met._labelnames) == want_labels, f'{name} label 不符：期望 {want_labels}，实得 {met._labelnames}'


def test_no_high_cardinality_id_labels() -> None:
    """§12.2 铁律：任何指标不得以高基数 ID 作 label。"""
    for met in m.IM_METRICS:
        for label in met._labelnames:
            assert label not in _FORBIDDEN_ID_LABELS, (
                f'{_exposed_name(met)} 使用了高基数 ID label「{label}」——违反 §12.2，'
                f'高基数 ID 只进脱敏 trace/log，不进 metric label'
            )


def test_all_registered_in_default_registry() -> None:
    """清单内全部指标都在默认 REGISTRY（经 /metrics 可导出 = 演练环境可见）。"""
    family_names = {f.name for f in REGISTRY.collect()}
    for met in m.IM_METRICS:
        # 族名 = Counter 去 _total 后的 _name（prometheus 约定），Gauge/Histogram 即 _name
        assert met._name in family_names, f'{met._name} 未注册进默认 REGISTRY（/metrics 不可见）'


def test_initial_thresholds_present() -> None:
    """§12.2「建议初始门槛」各键成文，供告警/Runbook 引用。"""
    for key in (
        'sync_projection_lag_p99_seconds',
        'integration_consumer_oldest_pending_seconds',
        'producer_outbox_oldest_pending_seconds_default',
        'permission_violation_or_permanent_loss',
        'send_commit_ack_p95_p99',
    ):
        assert key in m.INITIAL_THRESHOLDS, f'初始门槛缺键 {key}'
    # 关键数值门槛与 §12.2 一致
    assert m.INITIAL_THRESHOLDS['sync_projection_lag_p99_seconds'] == pytest.approx(5.0)
    assert m.INITIAL_THRESHOLDS['integration_consumer_oldest_pending_seconds'] == pytest.approx(30.0)
    assert m.INITIAL_THRESHOLDS['permission_violation_or_permanent_loss'] == 0
