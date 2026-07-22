from collections.abc import Callable
from typing import assert_type

from backend.utils.async_helper import run_await


async def _answer() -> int:
    return 42


def check_run_await_contract() -> None:
    """异步桥接器接收协程函数，并保留其同步调用签名。"""
    assert_type(run_await(_answer), Callable[[], int])
