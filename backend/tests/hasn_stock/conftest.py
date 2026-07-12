"""hasn_stock 测试 fixtures：跨 module-scoped loop 隔离 async engine 连接池。

对齐 external_mcp/conftest.py 的做法：本目录真实-DB 测试文件与其它 module-scoped loop 测试同跑时，
前一模块关闭的 loop 仍把连接滞留在**共享** engine 池里 → 后一模块首个 DB 调用命中已关闭 loop 的连接
→ asyncpg「attached to a different loop」报错。每个模块起跑前 dispose(close=False) 丢弃旧池，池在
本模块 loop 内惰性重建。
"""

from __future__ import annotations

from typing import TYPE_CHECKING

import pytest_asyncio

from backend.database.db import async_engine

if TYPE_CHECKING:
    from collections.abc import AsyncGenerator


@pytest_asyncio.fixture(autouse=True, scope='module', loop_scope='module')
async def _fresh_engine_pool() -> AsyncGenerator[None, None]:
    await async_engine.dispose(close=False)
    yield
