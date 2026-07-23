"""路由层连接内存域（process-local 连接引用）与就绪状态工具。"""

from fastapi import WebSocket


_ws_connections: dict[str, WebSocket] = {}
_ws_connection_ids: dict[str, str] = {}
_ws_ready_connection_ids: dict[str, str] = {}


def register_connection(node_id: str, ws: WebSocket, connection_id: str) -> None:
    """注册节点连接和代际 ID。"""
    _ws_connections[node_id] = ws
    _ws_connection_ids[node_id] = connection_id


def mark_connection_ready(node_id: str, connection_id: str) -> bool:
    """仅当前代际连接可标记 ready。"""
    if _ws_connection_ids.get(node_id) != connection_id:
        return False
    _ws_ready_connection_ids[node_id] = connection_id
    return True


def unregister_connection(node_id: str, connection_id: str) -> None:
    """按代际清理连接引用。"""
    if _ws_connection_ids.get(node_id) != connection_id:
        return
    _ws_connections.pop(node_id, None)
    _ws_connection_ids.pop(node_id, None)
    _ws_ready_connection_ids.pop(node_id, None)


def get_connection(node_id: str) -> WebSocket | None:
    return _ws_connections.get(node_id)


def get_connection_id(node_id: str) -> str | None:
    return _ws_connection_ids.get(node_id)


def get_ready_connection_id(node_id: str) -> str | None:
    return _ws_ready_connection_ids.get(node_id)


def is_connection_ready(node_id: str, connection_id: str) -> bool:
    return _ws_ready_connection_ids.get(node_id) == connection_id


def local_nodes() -> set[str]:
    """本进程当前持有连接的节点 ID 集。"""
    return set(_ws_connections.keys())


def iter_connections() -> tuple[tuple[str, WebSocket], ...]:
    """返回当前连接快照（用于广播/巡检）。"""
    return tuple(_ws_connections.items())


def iter_ready_connection_ids() -> tuple[tuple[str, str], ...]:
    """返回当前 ready 快照（用于重放窗口校验）。"""
    return tuple(_ws_ready_connection_ids.items())


__all__ = [
    '_ws_connections',
    '_ws_connection_ids',
    '_ws_ready_connection_ids',
    'get_connection',
    'get_connection_id',
    'get_ready_connection_id',
    'is_connection_ready',
    'iter_connections',
    'iter_ready_connection_ids',
    'local_nodes',
    'mark_connection_ready',
    'register_connection',
    'unregister_connection',
]
