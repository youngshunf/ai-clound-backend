"""hasn_diag 测试夹具（同 hasn_plan：每模块 dispose 全局引擎，隔断跨 loop 复用）。"""

from __future__ import annotations

from typing import TYPE_CHECKING

import pytest_asyncio

from backend.database.db import async_engine

if TYPE_CHECKING:
    from collections.abc import AsyncIterator


@pytest_asyncio.fixture(scope='module', loop_scope='module', autouse=True)
async def _dispose_global_engine_per_module() -> AsyncIterator[None]:
    yield
    await async_engine.dispose()
