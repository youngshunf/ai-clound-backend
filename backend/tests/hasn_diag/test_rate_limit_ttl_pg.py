"""diag 限频 TTL 自愈回归（真实 Redis，零 mock）。

锁死根因修复：`_rate_limit` 的 INCR-then-EXPIRE 竞态曾把桶变成「无 TTL 毒桶」——
count 只增不减、一旦越过 limit 对该维度**永久 429**（实测 owner 桶卡 6798、node 桶卡 1277，
把每分钟仅 1 次的上报也全打成 429）。修复后：任何 ttl<0 的桶（新建 or 毒桶）在下次
`_rate_limit` 调用时都会被重新武装固定窗口 TTL，永久 429 无法再形成。

需要：export DATABASE_PORT=15432（沿用套件约定；本文件只碰 Redis，不碰 PG）。
"""

from __future__ import annotations

from typing import Any
from uuid import uuid4

import pytest
import pytest_asyncio

from fastapi import HTTPException, status

from backend.app.hasn_diag.api.v1.app.errors import (
    _OWNER_LIMIT_PER_MIN,
    _RATE_WINDOW_SECONDS,
    _rate_limit,
)
from backend.database.redis import redis_client


@pytest_asyncio.fixture(autouse=True)
async def _reset_redis_pool() -> Any:
    """pytest-asyncio 每测一个事件循环；全局 redis_client 池化连接绑定首个循环，
    跨循环复用会 'Event loop is closed'。每测 disconnect 让其在本循环上重建（真 Redis）。"""
    try:
        await redis_client.connection_pool.disconnect()
    except Exception:
        pass
    yield


def _bucket(dimension: str, key: str) -> str:
    return f'diag:ratelimit:{dimension}:{key}'


@pytest.mark.asyncio
async def test_poisoned_bucket_without_ttl_self_heals_on_next_call() -> None:
    """无 TTL 的毒桶（值已超阈值）——下次限频调用必须给它重新武装 TTL，杜绝永久 429。"""
    key = f'test_poison_{uuid4().hex}'
    bucket = _bucket('owner', key)
    try:
        # 制造毒桶：值远超阈值 + 无 TTL（ttl=-1），复现历史竞态遗留态。
        await redis_client.set(bucket, _OWNER_LIMIT_PER_MIN + 500)
        assert await redis_client.ttl(bucket) == -1, '前置：毒桶应无 TTL'

        # 本次仍会 429（值 > limit 是真的超额），但关键是它必须被重新武装 TTL。
        with pytest.raises(HTTPException) as exc:
            await _rate_limit('owner', key, _OWNER_LIMIT_PER_MIN)
        assert exc.value.status_code == status.HTTP_429_TOO_MANY_REQUESTS

        ttl = await redis_client.ttl(bucket)
        assert 0 < ttl <= _RATE_WINDOW_SECONDS, f'毒桶必须自愈出 TTL，实测 ttl={ttl}'
    finally:
        await redis_client.delete(bucket)


@pytest.mark.asyncio
async def test_fresh_bucket_gets_ttl_and_passes_under_limit() -> None:
    """全新桶：首次调用不 429，且必须带上固定窗口 TTL（不会退化成无 TTL 毒桶）。"""
    key = f'test_fresh_{uuid4().hex}'
    bucket = _bucket('owner', key)
    try:
        await _rate_limit('owner', key, _OWNER_LIMIT_PER_MIN)  # 不应抛
        assert await redis_client.get(bucket) == '1'
        ttl = await redis_client.ttl(bucket)
        assert 0 < ttl <= _RATE_WINDOW_SECONDS, f'新桶必须带 TTL，实测 ttl={ttl}'
    finally:
        await redis_client.delete(bucket)


@pytest.mark.asyncio
async def test_counts_up_to_limit_then_429() -> None:
    """固定窗口语义：limit 次内放行，第 limit+1 次 429（且全程桶有 TTL）。"""
    key = f'test_count_{uuid4().hex}'
    bucket = _bucket('owner', key)
    limit = 3
    try:
        for _ in range(limit):
            await _rate_limit('owner', key, limit)  # 前 limit 次放行
        assert await redis_client.ttl(bucket) > 0, '窗口内桶应始终带 TTL'
        with pytest.raises(HTTPException) as exc:
            await _rate_limit('owner', key, limit)  # 第 limit+1 次超额
        assert exc.value.status_code == status.HTTP_429_TOO_MANY_REQUESTS
    finally:
        await redis_client.delete(bucket)
