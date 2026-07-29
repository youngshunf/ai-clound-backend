from pathlib import Path

from redis.asyncio import ConnectionPool

OTEL_MODULE = Path(__file__).resolve().parents[1] / 'common' / 'observability' / 'otel.py'


def test_async_connection_pool_supports_native_connection_metrics() -> None:
    """异步连接池必须实现 redis-py 原生连接数指标契约。"""
    connection_pool = ConnectionPool()

    connection_counts = connection_pool.get_connection_count()

    assert len(connection_counts) == 2
    assert sum(count for count, _attributes in connection_counts) == 0


def test_redis_enables_native_metrics_and_standard_tracing() -> None:
    """Redis 同时启用原生指标与标准链路追踪。"""
    source = OTEL_MODULE.read_text(encoding='utf-8')

    assert 'get_observability_instance()' in source
    assert 'OTelConfig()' in source
    assert 'RedisInstrumentor.instrument_client(redis_client)' in source
