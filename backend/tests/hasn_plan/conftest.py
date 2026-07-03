"""hasn_plan 测试夹具。

全局 ``async_engine`` 是 QueuePool（pool_size=10，非 NullPool）：连接跨模块复用。
本目录多个测试文件用 ``loop_scope='module'``（pytest-asyncio 每模块新事件循环），
一条连接在模块 A 的 loop 里入池、到模块 B 的 loop 里被复用 → asyncpg
"attached to a different loop" 崩溃。这是**既有环境脆弱**（priority_coercion ↔
output_spec 两个旧文件同跑即复现），非某个测试引入。

模块级 autouse 夹具在每个模块自己的 loop 里 dispose 全局引擎（连接是在同一 loop
建的，dispose 干净），令下个模块从空池起、在自己的 loop 里新建连接，隔断跨 loop 复用。
"""

from __future__ import annotations

from typing import TYPE_CHECKING

import pytest_asyncio

from backend.database.db import async_engine

if TYPE_CHECKING:
    from collections.abc import AsyncIterator


@pytest_asyncio.fixture(scope='module', loop_scope='module', autouse=True)
async def _dispose_global_engine_per_module() -> AsyncIterator[None]:
    """每个模块结束时清空全局连接池（详见模块文档字符串）。"""
    yield
    await async_engine.dispose()
