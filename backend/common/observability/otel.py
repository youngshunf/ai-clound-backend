import re

from collections.abc import Iterator, Mapping
from contextlib import AbstractContextManager, contextmanager
from typing import Any

from aio_pika.abc import HeadersType
from fastapi import FastAPI
from opentelemetry import _logs, metrics, trace
from opentelemetry.context import Context
from opentelemetry.exporter.otlp.proto.grpc._log_exporter import OTLPLogExporter
from opentelemetry.exporter.otlp.proto.grpc.metric_exporter import OTLPMetricExporter
from opentelemetry.exporter.otlp.proto.grpc.trace_exporter import OTLPSpanExporter
from opentelemetry.instrumentation.asyncio import AsyncioInstrumentor
from opentelemetry.instrumentation.fastapi import FastAPIInstrumentor
from opentelemetry.instrumentation.httpx import HTTPXClientInstrumentor
from opentelemetry.instrumentation.logging import LoggingInstrumentor
from opentelemetry.instrumentation.redis import RedisInstrumentor
from opentelemetry.instrumentation.sqlalchemy import SQLAlchemyInstrumentor
from opentelemetry.sdk._logs import LoggerProvider, LoggingHandler
from opentelemetry.sdk._logs._internal.export import BatchLogRecordProcessor
from opentelemetry.sdk.metrics import MeterProvider
from opentelemetry.sdk.metrics.export import PeriodicExportingMetricReader
from opentelemetry.sdk.resources import Resource
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import BatchSpanProcessor
from opentelemetry.trace import Span, SpanKind, Status, StatusCode
from opentelemetry.trace.propagation.tracecontext import (
    TraceContextTextMapPropagator,
)
from redis.observability import OTelConfig, get_observability_instance

from backend.common.log import log, request_id_filter
from backend.common.observability.prometheus import PROMETHEUS_APP_NAME
from backend.core.conf import settings
from backend.database.db import async_engine
from backend.database.redis import redis_client

_RABBITMQ_TRACE_PROPAGATOR = TraceContextTextMapPropagator()
_SAFE_MESSAGING_DIMENSION = re.compile(r'^[A-Za-z0-9._:-]{1,128}$')


def _require_safe_messaging_dimension(value: str, *, field: str) -> str:
    normalized = value.strip()
    if _SAFE_MESSAGING_DIMENSION.fullmatch(normalized) is None:
        raise ValueError(f'{field} 必须是稳定低基数标识')
    return normalized


def build_rabbitmq_span_attributes(
    *,
    operation: str,
    destination: str,
    result: str | None = None,
    redelivered: bool | None = None,
) -> dict[str, str | bool]:
    """构造不含 DSN、实体 ID和正文的 RabbitMQ span 属性。"""
    attributes: dict[str, str | bool] = {
        'messaging.system': 'rabbitmq',
        'messaging.destination.name': _require_safe_messaging_dimension(
            destination,
            field='RabbitMQ destination',
        ),
        'messaging.operation.type': _require_safe_messaging_dimension(
            operation,
            field='RabbitMQ operation',
        ),
    }
    if result is not None:
        attributes['hasn.messaging.result'] = _require_safe_messaging_dimension(
            result,
            field='RabbitMQ result',
        )
    if redelivered is not None:
        attributes['messaging.message.redelivered'] = redelivered
    return attributes


def inject_rabbitmq_trace_headers() -> HeadersType:
    """只把 W3C trace context 注入 AMQP header，不传播 baggage。"""
    carrier: HeadersType = {}
    _RABBITMQ_TRACE_PROPAGATOR.inject(carrier)
    return carrier


def extract_rabbitmq_trace_context(headers: Mapping[str, Any] | None) -> Context:
    """从 AMQP header 提取 W3C trace context，忽略其它业务 header。"""
    carrier: dict[str, str] = {}
    for key, value in (headers or {}).items():
        normalized_key = str(key).lower()
        if normalized_key not in {'traceparent', 'tracestate'}:
            continue
        if isinstance(value, bytes):
            carrier[normalized_key] = value.decode('ascii', errors='ignore')
        elif isinstance(value, str):
            carrier[normalized_key] = value
    return _RABBITMQ_TRACE_PROPAGATOR.extract(carrier)


@contextmanager
def _messaging_span(
    *,
    name: str,
    kind: SpanKind,
    attributes: Mapping[str, str | bool],
    parent_context: Context | None = None,
) -> Iterator[Span]:
    tracer = trace.get_tracer('backend.messaging')
    with tracer.start_as_current_span(
        name,
        context=parent_context,
        kind=kind,
        attributes=dict(attributes),
        record_exception=False,
        set_status_on_exception=False,
    ) as span:
        yield span


def integration_event_consume_span(
    *,
    consumer: str,
    event_type: str,
    consumer_class: str,
) -> AbstractContextManager[Span]:
    """创建 IM integration event 消费根 span。"""
    attributes = {
        'messaging.system': 'postgresql',
        'messaging.destination.name': 'hasn_im.integration_events',
        'messaging.operation.type': 'process',
        'hasn.consumer.name': _require_safe_messaging_dimension(
            consumer,
            field='consumer',
        ),
        'hasn.consumer.class': _require_safe_messaging_dimension(
            consumer_class,
            field='consumer class',
        ),
        'hasn.event.type': _require_safe_messaging_dimension(
            event_type,
            field='event type',
        ),
    }
    return _messaging_span(
        name='hasn.im.integration_event.process',
        kind=SpanKind.CONSUMER,
        attributes=attributes,
    )


def rabbitmq_publish_span(destination: str) -> AbstractContextManager[Span]:
    """创建 RabbitMQ publisher confirm 边界 span。"""
    return _messaging_span(
        name='rabbitmq.publish',
        kind=SpanKind.PRODUCER,
        attributes=build_rabbitmq_span_attributes(
            operation='publish',
            destination=destination,
        ),
    )


def rabbitmq_consume_span(
    *,
    destination: str,
    headers: Mapping[str, Any] | None,
    redelivered: bool,
) -> AbstractContextManager[Span]:
    """按 publisher trace context 创建 RabbitMQ delivery/ack 边界 span。"""
    return _messaging_span(
        name='rabbitmq.process',
        kind=SpanKind.CONSUMER,
        attributes=build_rabbitmq_span_attributes(
            operation='process',
            destination=destination,
            redelivered=redelivered,
        ),
        parent_context=extract_rabbitmq_trace_context(headers),
    )


def websocket_send_span() -> AbstractContextManager[Span]:
    """创建不含 node/message ID 与正文的 WebSocket 发送 span。"""
    return _messaging_span(
        name='websocket.send',
        kind=SpanKind.PRODUCER,
        attributes={
            'network.protocol.name': 'websocket',
            'messaging.operation.type': 'deliver',
        },
    )


def mark_messaging_span(
    span: Span,
    *,
    result: str,
    error: BaseException | None = None,
    failed: bool = False,
) -> None:
    """只记录低基数结果与异常类型，不把异常文本或连接 DSN 写入 trace。"""
    span.set_attribute(
        'hasn.messaging.result',
        _require_safe_messaging_dimension(result, field='messaging result'),
    )
    if error is None and not failed:
        span.set_status(Status(StatusCode.OK))
        return
    if error is not None:
        error_type = type(error).__name__
        span.set_attribute('error.type', error_type)
        span.set_status(Status(StatusCode.ERROR, error_type))
        return
    span.set_status(Status(StatusCode.ERROR, result))


def init_resource(service_name: str) -> Resource:
    """
    初始化资源

    :param service_name: 服务名称
    :return:
    """
    from backend import __version__

    return Resource(
        attributes={
            'service.name': service_name,
            'service.version': __version__,
            'deployment.environment': settings.ENVIRONMENT,
        },
    )


def init_tracer(resource: Resource) -> None:
    """
    初始化追踪器

    :param resource: 遥测资源
    :return:
    """
    provider = TracerProvider(resource=resource)
    exporter = OTLPSpanExporter(endpoint=settings.GRAFANA_OTLP_GRPC_ENDPOINT, insecure=True)
    processor = BatchSpanProcessor(span_exporter=exporter)

    provider.add_span_processor(processor)
    trace.set_tracer_provider(provider)


def init_metrics(resource: Resource) -> None:
    """
    初始化指标

    :param resource: 遥测资源
    :return:
    """
    exporter = OTLPMetricExporter(endpoint=settings.GRAFANA_OTLP_GRPC_ENDPOINT, insecure=True)
    reader = PeriodicExportingMetricReader(exporter=exporter)
    provider = MeterProvider(resource=resource, metric_readers=[reader])

    metrics.set_meter_provider(provider)


def init_logging(resource: Resource) -> None:
    """
    初始化日志

    :param resource: 遥测资源
    :return:
    """
    provider = LoggerProvider(resource=resource)
    exporter = OTLPLogExporter(endpoint=settings.GRAFANA_OTLP_GRPC_ENDPOINT, insecure=True)
    processor = BatchLogRecordProcessor(exporter=exporter)

    provider.add_log_record_processor(processor)
    _logs.set_logger_provider(provider)

    otel_logging_handler = LoggingHandler(logger_provider=provider)
    log.add(
        otel_logging_handler,
        level=settings.LOG_STD_LEVEL,
        format=settings.LOG_FORMAT,
        filter=lambda record: request_id_filter(record),
    )


def init_otel(app: FastAPI) -> None:
    """
    初始化 OpenTelemetry

    :param app: FastAPI 应用实例
    :return:
    """
    resource = init_resource(PROMETHEUS_APP_NAME)

    init_tracer(resource)
    init_metrics(resource)
    init_logging(resource)

    AsyncioInstrumentor().instrument()
    LoggingInstrumentor().instrument(set_logging_format=True)
    SQLAlchemyInstrumentor().instrument(engine=async_engine.sync_engine)
    # redis-py 8 的异步连接池已支持原生连接指标；标准 instrumentation 继续提供链路追踪。
    redis_otel = get_observability_instance()
    redis_otel.init(OTelConfig())
    RedisInstrumentor.instrument_client(redis_client)  # type: ignore
    HTTPXClientInstrumentor().instrument()
    FastAPIInstrumentor.instrument_app(app)
