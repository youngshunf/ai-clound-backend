"""HASN 统一节点 WebSocket 协议入口兼容层。"""

from contextlib import contextmanager
from typing import Any, Iterator

from backend.app.hasn_im.api import ws_node as _ws_node_v2


def __getattr__(name: str) -> Any:
    """兼容层透传旧引用，避免上层直接 import 的内部路径失效。"""
    if name == 'message_router':
        return _ws_node_v2.message_router
    if name == 'ws_router':
        return _ws_node_v2.ws_router
    raise AttributeError(name)


router = _ws_node_v2.router
message_router = _ws_node_v2.message_router
ws_router = _ws_node_v2.ws_router
hasn_node_websocket = _ws_node_v2.hasn_node_websocket
authenticate_ws_connection = _ws_node_v2.authenticate_ws_connection
async_db_session = _ws_node_v2.async_db_session


@contextmanager
def _bridge_legacy_deps(overrides: dict[str, Any]) -> Iterator[None]:
    """在兼容层打补丁时回灌到新版实现，保证旧测试可用猴子补丁。"""
    saved: dict[str, Any] = {}
    try:
        for key, value in overrides.items():
            if hasattr(_ws_node_v2, key):
                saved[key] = getattr(_ws_node_v2, key)
                setattr(_ws_node_v2, key, value)
        yield
    finally:
        for key, value in saved.items():
            setattr(_ws_node_v2, key, value)


async def hasn_node_websocket(websocket) -> None:
    """兼容函数，注入可替换依赖后复用新版实现。"""
    with _bridge_legacy_deps(
        {
            'authenticate_ws_connection': authenticate_ws_connection,
            'async_db_session': async_db_session,
        }
    ):
        return await _ws_node_v2.hasn_node_websocket(websocket)


async def _handle_add_agent(websocket, node_id: str, params: dict, active_entities: set[str]) -> None:
    """兼容函数，注入可替换 DB session 后复用新版实现。"""
    with _bridge_legacy_deps({'async_db_session': async_db_session}):
        return await _ws_node_v2._handle_add_agent(websocket, node_id, params, active_entities)


async def _handle_send(websocket, node_id: str, params: dict, active_entities: set[str]) -> None:
    """兼容函数，注入可替换 DB session 后复用新版实现。"""
    with _bridge_legacy_deps({'async_db_session': async_db_session}):
        return await _ws_node_v2._handle_send(websocket, node_id, params, active_entities)


__all__ = [
    'authenticate_ws_connection',
    'async_db_session',
    'hasn_node_websocket',
    'message_router',
    'router',
    'ws_router',
    '_handle_add_agent',
    '_handle_send',
]
