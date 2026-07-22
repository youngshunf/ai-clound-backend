"""Marketplace 测试夹具：隔离模块级事件循环与共享异步连接池。"""

from collections.abc import AsyncGenerator

import pytest_asyncio

from backend.database.db import async_engine


@pytest_asyncio.fixture(autouse=True, scope='module', loop_scope='module')
async def fresh_engine_pool() -> AsyncGenerator[None, None]:
    """每个市场测试模块开始前丢弃上一事件循环的连接池。"""
    await async_engine.dispose(close=False)
    yield
