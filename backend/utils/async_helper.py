import asyncio
import atexit
import threading
import weakref

from collections.abc import Awaitable, Callable
from concurrent.futures import Future
from functools import wraps
from typing import ParamSpec, TypeVar

T = TypeVar('T')
P = ParamSpec('P')


class _TaskRunner:
    """在后台线程上运行 asyncio 事件循环的任务运行器"""

    def __init__(self) -> None:
        self.__loop: asyncio.AbstractEventLoop | None = None
        self.__thread: threading.Thread | None = None
        self.__lock = threading.Lock()
        atexit.register(self.close)

    def close(self) -> None:
        """关闭事件循环并清理"""
        with self.__lock:
            if self.__loop:
                self.__loop.call_soon_threadsafe(self.__loop.stop)
            if self.__thread and self.__thread.is_alive():
                self.__thread.join()
            self.__loop = None
            self.__thread = None
            name = f'TaskRunner-{threading.get_ident()}'
            _runner_map.pop(name, None)

    def _target(self) -> None:
        """后台线程的目标函数"""
        loop = self.__loop
        if loop is None:
            return
        try:
            loop.run_forever()
        finally:
            loop.close()

    def run(self, awaitable: Awaitable[T]) -> T:
        """在后台事件循环上运行协程并返回其结果"""
        with self.__lock:
            name = f'TaskRunner-{threading.get_ident()}'
            if self.__loop is None:
                self.__loop = asyncio.new_event_loop()
                self.__thread = threading.Thread(target=self._target, daemon=True, name=name)
                self.__thread.start()
            loop = self.__loop
            if loop is None:
                raise RuntimeError('任务事件循环初始化失败')
            future: Future[T] = asyncio.run_coroutine_threadsafe(_await_result(awaitable), loop)
            return future.result()


_runner_map: weakref.WeakValueDictionary[str, _TaskRunner] = weakref.WeakValueDictionary()


async def _await_result(awaitable: Awaitable[T]) -> T:
    """将宽泛的可等待对象收敛为可跨线程提交的协程对象。"""
    return await awaitable


def run_await(coro: Callable[P, Awaitable[T]]) -> Callable[P, T]:
    """将协程包装在函数中，直到它执行完为止"""

    @wraps(coro)
    def wrapped(*args: P.args, **kwargs: P.kwargs) -> T:
        inner = coro(*args, **kwargs)

        try:
            # 如果事件循环正在运行，则使用任务调用
            asyncio.get_running_loop()
            name = f'TaskRunner-{threading.get_ident()}'
            if name not in _runner_map:
                _runner_map[name] = _TaskRunner()
            return _runner_map[name].run(inner)
        except RuntimeError:
            # 当前线程没有运行中的事件循环时，使用独立循环并在完成后释放。
            loop = asyncio.new_event_loop()
            try:
                return loop.run_until_complete(_await_result(inner))
            finally:
                loop.close()

    wrapped.__doc__ = coro.__doc__
    return wrapped
