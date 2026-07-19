"""hasn_finance 测试夹具：隔离按模块创建的事件循环与共享异步连接池。

本目录的真实 PostgreSQL 合同测试使用 ``loop_scope='module'``，而生产
``async_engine`` 使用可跨用例复用连接的 QueuePool。前一测试模块关闭事件循环后，
池中的 asyncpg 连接仍绑定旧循环；后一模块复用连接会触发
``attached to a different loop``。

每个模块起跑前丢弃旧池且不在当前循环关闭旧连接，使本模块首次访问数据库时在当前
事件循环内创建新连接。该处理只隔离测试生命周期，不改变生产连接池配置。
"""

from __future__ import annotations

from typing import TYPE_CHECKING

import pytest_asyncio

from backend.database.db import async_engine

if TYPE_CHECKING:
    from collections.abc import AsyncGenerator


@pytest_asyncio.fixture(autouse=True, scope='module', loop_scope='module')
async def _fresh_engine_pool() -> AsyncGenerator[None, None]:
    """在每个 finance 测试模块开始前丢弃上一事件循环的连接池。"""
    await async_engine.dispose(close=False)
    yield
