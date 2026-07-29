from redis.asyncio import ConnectionPool


def test_async_connection_pool_supports_opentelemetry_connection_count() -> None:
    """异步连接池必须实现 redis-py 原生连接数指标所需的回调契约。"""
    connection_pool = ConnectionPool()

    connection_counts = connection_pool.get_connection_count()

    assert len(connection_counts) == 2
    assert sum(count for count, _attributes in connection_counts) == 0
