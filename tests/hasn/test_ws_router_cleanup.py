"""HASN WebSocket 断连清理韧性回归：unregister_node 不得因 redis 故障抛出。

复现并锁死的 bug：节点 WS 断开时 ws_node.py 的 finally 调 unregister_node → 一连串
redis 操作；当 redis 连接 transport 已关闭（断连与 event-loop 拆除并发 / 池中陈旧连接）
时整串抛出 RuntimeError，冒泡出 ASGI handler → "Application callable raised an exception"。

修复：unregister_node 把 redis 清理包进 try/except 只记 warning，进程内 WS 引用始终移除。
本测试用抛异常的 fake redis 验证：清理不抛 + _ws_connections 仍被清。纯 service 层，
不连真 redis/DB（符合"零 Mock 零 Fake"——这里 fake 的是"故障注入"而非业务数据造假）。
"""

from __future__ import annotations

import pytest

from backend.app.hasn.service import ws_router as ws_router_module
from backend.app.hasn.service.ws_router import ws_router


class _BoomRedis:
    """每个被用到的 async redis 方法都抛 RuntimeError（模拟 transport closed）。"""

    def __getattr__(self, _name: str):
        async def _boom(*_args, **_kwargs):
            raise RuntimeError('unable to perform operation on <TCPTransport closed=True>; the handler is closed')

        return _boom


@pytest.mark.asyncio
async def test_unregister_node_swallows_redis_failure_and_clears_ws_ref(monkeypatch) -> None:
    # Arrange：进程内挂一个该节点的 WS 引用；redis 全线故障
    node_id = 'node_test_boom'
    ws_router_module._ws_connections[node_id] = object()  # 占位 WS 引用
    monkeypatch.setattr(ws_router_module, 'redis_client', _BoomRedis())

    # Act：断连清理——绝不抛出（旧代码会冒泡 RuntimeError）
    await ws_router.unregister_node(node_id)

    # Assert：进程内 WS 引用已移除（redis 失败不影响本地清理）
    assert node_id not in ws_router_module._ws_connections


@pytest.mark.asyncio
async def test_unregister_node_unknown_node_is_noop(monkeypatch) -> None:
    # Arrange：未知节点 + redis 故障
    monkeypatch.setattr(ws_router_module, 'redis_client', _BoomRedis())

    # Act / Assert：不抛、不残留
    await ws_router.unregister_node('node_never_seen')
    assert 'node_never_seen' not in ws_router_module._ws_connections
