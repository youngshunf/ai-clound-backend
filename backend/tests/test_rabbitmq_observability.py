"""方案 B RabbitMQ 指标与 OpenTelemetry 安全契约测试。"""

from __future__ import annotations

import re

from pathlib import Path

import pytest

from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import SimpleSpanProcessor
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter

from backend.app.hasn_im import consumer_worker
from backend.app.hasn_im.observability import metrics
from backend.common.observability import otel
from backend.common.observability.otel import (
    build_rabbitmq_span_attributes,
    extract_rabbitmq_trace_context,
    inject_rabbitmq_trace_headers,
    mark_messaging_span,
    websocket_send_span,
)

_BACKEND_ROOT = Path(__file__).resolve().parents[1]
_REPO_ROOT = Path(__file__).resolve().parents[2]
_TRACEPARENT = re.compile(r'^00-[0-9a-f]{32}-[0-9a-f]{16}-[0-9a-f]{2}$')


def test_rabbitmq_trace_context_round_trip_uses_w3c_headers_only() -> None:
    """Rabbit header 只传播 traceparent/tracestate，禁止携带 baggage 中的业务身份。"""
    tracer = TracerProvider().get_tracer(__name__)
    with tracer.start_as_current_span('integration-event') as producer:
        headers = inject_rabbitmq_trace_headers()

    assert set(headers) <= {'traceparent', 'tracestate'}
    assert _TRACEPARENT.fullmatch(headers['traceparent'])

    context = extract_rabbitmq_trace_context(headers)
    with tracer.start_as_current_span('rabbit-consume', context=context) as consumer:
        assert consumer.get_span_context().trace_id == producer.get_span_context().trace_id


def test_rabbitmq_span_attributes_reject_secrets_and_high_cardinality() -> None:
    """Span 属性只允许稳定边界维度，不接收 DSN、消息正文或资源 ID。"""
    attributes = build_rabbitmq_span_attributes(
        operation='publish',
        destination='huanxing.realtime',
        result='confirmed',
        redelivered=False,
    )

    assert attributes == {
        'messaging.system': 'rabbitmq',
        'messaging.destination.name': 'huanxing.realtime',
        'messaging.operation.type': 'publish',
        'hasn.messaging.result': 'confirmed',
        'messaging.message.redelivered': False,
    }
    forbidden = {'owner_id', 'node_id', 'message_id', 'event_id', 'payload', 'body', 'dsn', 'password'}
    assert forbidden.isdisjoint(attributes)
    with pytest.raises(ValueError, match='RabbitMQ destination'):
        build_rabbitmq_span_attributes(
            operation='publish',
            destination='amqp://guest:secret@rabbit/',
        )


def test_rabbitmq_observability_metrics_use_low_cardinality_labels() -> None:
    """confirm、ack 与 redelivery 指标不得引入实体 ID 标签。"""
    expected = {
        metrics.HASN_RABBITMQ_PUBLISH_CONFIRM_TOTAL: {'result'},
        metrics.HASN_RABBITMQ_DELIVERY_ACK_TOTAL: {'result'},
        metrics.HASN_RABBITMQ_REDELIVERY_TOTAL: set(),
    }
    for metric, labels in expected.items():
        assert set(metric._labelnames) == labels


def test_rabbitmq_trace_boundaries_are_wired_without_payload_attributes() -> None:
    """集成事件、Rabbit publish/consume 与 WS send 四个边界必须形成一条 trace。"""
    framework = (_BACKEND_ROOT / 'app/hasn_im/consumers/framework.py').read_text(encoding='utf-8')
    rabbit = (_BACKEND_ROOT / 'app/hasn_im/adapters/routing/rabbitmq_realtime_wakeup_bus.py').read_text(
        encoding='utf-8'
    )
    delivery = (_BACKEND_ROOT / 'app/hasn_im/adapters/routing/delivery_bus.py').read_text(encoding='utf-8')
    consumer_worker = (_BACKEND_ROOT / 'app/hasn_im/consumer_worker.py').read_text(encoding='utf-8')

    assert 'integration_event_consume_span' in framework
    assert 'rabbitmq_publish_span' in rabbit
    assert 'rabbitmq_consume_span' in rabbit
    assert 'websocket_send_span' in delivery
    assert 'init_worker_tracing' in consumer_worker
    for source in (framework, rabbit, delivery):
        assert "set_attribute('payload'" not in source
        assert "set_attribute('body'" not in source
        assert "set_attribute('password'" not in source


def test_messaging_span_records_error_type_without_exception_text(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """消息 span 只保留异常类型，禁止自动记录可能含 DSN 或正文的异常事件。"""
    exporter = InMemorySpanExporter()
    provider = TracerProvider()
    provider.add_span_processor(SimpleSpanProcessor(exporter))
    monkeypatch.setattr(otel.trace, 'get_tracer', provider.get_tracer)

    with pytest.raises(RuntimeError, match='secret'):
        with websocket_send_span() as span:
            error = RuntimeError('amqp://guest:secret@rabbit/ payload=secret')
            mark_messaging_span(span, result='send_error', error=error)
            raise error

    [recorded] = exporter.get_finished_spans()
    assert recorded.status.status_code.name == 'ERROR'
    assert recorded.attributes['error.type'] == 'RuntimeError'
    assert recorded.events == ()
    assert 'secret' not in str(recorded.attributes)


def test_im_consumer_worker_initializes_tracing_only_when_enabled(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """独立消费者必须显式初始化 tracer，probe/关闭态不得建立 exporter。"""
    initialized: list[object] = []
    resource_names: list[str] = []
    resource = object()
    monkeypatch.setattr(
        consumer_worker,
        'init_resource',
        lambda name: resource_names.append(name) or resource,
    )
    monkeypatch.setattr(
        consumer_worker,
        'init_tracer',
        lambda selected: initialized.append(selected),
    )

    monkeypatch.setattr(consumer_worker.settings, 'GRAFANA_METRICS_ENABLE', False)
    consumer_worker.init_worker_tracing()
    assert initialized == []
    assert resource_names == []

    monkeypatch.setattr(consumer_worker.settings, 'GRAFANA_METRICS_ENABLE', True)
    consumer_worker.init_worker_tracing()
    assert initialized == [resource]
    assert resource_names == ['hasn_im_consumer_worker']


def test_rabbitmq_prometheus_proxy_only_binds_observability_private_network() -> None:
    """Prometheus 代理只能暴露在固定 Docker 私网，RabbitMQ 15692 继续保持 loopback。"""
    socket_unit = (_REPO_ROOT / 'deploy/rabbitmq/huanxing-rabbitmq-prometheus-proxy.socket').read_text(encoding='utf-8')
    service_unit = (_REPO_ROOT / 'deploy/rabbitmq/huanxing-rabbitmq-prometheus-proxy.service').read_text(
        encoding='utf-8'
    )

    assert 'ListenStream=172.24.0.1:15693' in socket_unit
    assert 'FreeBind=true' in socket_unit
    assert '0.0.0.0' not in socket_unit
    assert 'systemd-socket-proxyd 127.0.0.1:15692' in service_unit
    assert 'NoNewPrivileges=true' in service_unit
