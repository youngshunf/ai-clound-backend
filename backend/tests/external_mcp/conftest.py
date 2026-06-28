"""external_mcp 测试 fixtures：跨 module-scoped loop 隔离 async engine 连接池。

本目录有两个 `loop_scope='module'` 的真实-DB 测试文件（gateway + local_process）。两者同跑时，
前一模块的 loop 关闭后其连接仍滞留在**共享** engine 池里，后一模块首个 DB 调用命中绑定到已关闭
loop 的连接 → asyncpg "attached to a different loop" 报错。

每个模块起跑前 `dispose(close=False)`：丢弃旧池（旧连接交 GC，不在错的 loop 里强行 close），池在
本模块 loop 内惰性重建 → 后续 DB 调用拿到本 loop 的新连接。
"""

from __future__ import annotations

import pytest_asyncio

from backend.database.db import async_engine


@pytest_asyncio.fixture(autouse=True, scope='module', loop_scope='module')
async def _fresh_engine_pool():
    await async_engine.dispose(close=False)
    yield
