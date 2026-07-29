from pathlib import Path


OTEL_MODULE = (
    Path(__file__).resolve().parents[1]
    / 'common'
    / 'observability'
    / 'otel.py'
)


def test_redis_uses_supported_instrumentation_without_native_async_metrics() -> None:
    """Redis 保留标准链路追踪，但不得启用 7.2 不支持的原生异步指标。"""
    source = OTEL_MODULE.read_text(encoding='utf-8')

    assert 'RedisInstrumentor.instrument_client(redis_client)' in source
    assert 'redis.observability' not in source
