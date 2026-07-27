"""HASN WebSocket 断连清理结构契约。

Redis 故障下的真实恢复由 IM cutover 全栈 E2E 验收；本文件只钉死两个不依赖外部服务的
不变量：Redis 清理必须被 ``try/except/finally`` 包围，且本地连接引用必须在
``finally`` 中按连接代际移除。
"""

from __future__ import annotations

import ast
import inspect
import textwrap

from backend.app.hasn_im.adapters.routing import ws_connection_registry
from backend.app.hasn_im.adapters.routing.node_session_service import NodeSessionService


def test_unregister_node_has_best_effort_redis_boundary_and_finally_cleanup() -> None:
    """Redis 异常边界不能绕过进程内连接引用清理。"""
    tree = ast.parse(textwrap.dedent(inspect.getsource(NodeSessionService.unregister_node)))
    try_nodes = [node for node in ast.walk(tree) if isinstance(node, ast.Try)]
    assert len(try_nodes) == 1

    cleanup_calls = [
        node
        for node in ast.walk(ast.Module(body=try_nodes[0].finalbody, type_ignores=[]))
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Name)
        and node.func.id == 'unregister_connection'
    ]
    assert cleanup_calls, 'unregister_connection 必须位于 finally，确保 Redis 故障时仍清理本地引用'
    assert try_nodes[0].handlers, 'Redis 清理异常必须显式捕获并进入 warning 日志'


def test_unregister_unknown_local_connection_is_noop() -> None:
    """未知连接代际的本地清理必须幂等。"""
    node_id = 'node_never_seen'
    ws_connection_registry.unregister_connection(node_id, 'conn_never_seen')
    assert node_id not in ws_connection_registry._ws_connections
    assert node_id not in ws_connection_registry._ws_connection_ids
