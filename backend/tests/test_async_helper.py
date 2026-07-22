import asyncio
import threading

import pytest

from backend.utils.async_helper import run_await


async def _thread_id_after_yield() -> int:
    await asyncio.sleep(0)
    return threading.get_ident()


def test_run_await_runs_coroutine_without_active_loop() -> None:
    """同步调用方没有事件循环时，协程应在本地事件循环完成。"""
    assert run_await(_thread_id_after_yield)() == threading.get_ident()


@pytest.mark.asyncio
async def test_run_await_uses_background_loop_when_active_loop_exists() -> None:
    """同步桥接不能重入当前事件循环，应改由后台循环执行。"""
    assert run_await(_thread_id_after_yield)() != threading.get_ident()
