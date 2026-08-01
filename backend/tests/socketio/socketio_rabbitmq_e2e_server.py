"""真实 RabbitMQ Socket.IO 跨进程 E2E 的隔离 ASGI 服务。"""

from __future__ import annotations

import socketio

from backend.common.socketio.manager import (
    assert_socketio_server_manager_ready,
    build_socketio_server_manager,
)

manager = build_socketio_server_manager()
sio = socketio.AsyncServer(
    client_manager=manager,
    async_mode='asgi',
    cors_allowed_origins=[],
)

app = socketio.ASGIApp(
    sio,
    socketio_path='socket.io',
    on_startup=lambda: assert_socketio_server_manager_ready(manager),
)
