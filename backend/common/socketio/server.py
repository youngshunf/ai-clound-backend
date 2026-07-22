"""
SocketIO 服务器实例 + 连接/断开事件
对应设计文档: 02-通信协议.md §三 WebSocket 协议
"""
import urllib.parse

import socketio

from backend.common.log import log
from backend.common.security.jwt import jwt_authentication
from backend.core.conf import settings
from backend.database.redis import redis_client

# 创建 Socket.IO 服务器实例
sio = socketio.AsyncServer(
    client_manager=socketio.AsyncRedisManager(
        f'redis://:{urllib.parse.quote(settings.REDIS_PASSWORD)}@{settings.REDIS_HOST}:{settings.REDIS_PORT}/{settings.REDIS_DATABASE}',
    ),
    async_mode='asgi',
    cors_allowed_origins=[],
    namespaces=['/ws'],
)


@sio.event
async def connect(sid, environ, auth) -> bool:
    """
    Socket 连接事件
    对应设计文档 §3.1: Human 用 JWT, Agent 用 API Key
    """
    if not auth:
        log.error('WebSocket 连接失败: 无授权')
        return False

    session_uuid = auth.get('session_uuid')
    hasn_id = auth.get('hasn_id')  # HASN hasn_id (h_xxx 或 a_xxx)
    token = auth.get('token')

    # HASN 的节点连接只允许原生 Node WebSocket；Socket.IO 无法承载节点身份和同步语义。
    if hasn_id:
        log.warning(f'[HASN Socket.IO] 拒绝使用 hasn_id 的遗留连接: {hasn_id}')
        return False

    # ── 传统连接 (唤星既有系统) ──
    if not token or not session_uuid:
        log.error('WebSocket 连接失败: 授权失败')
        return False

    if token == settings.WS_NO_AUTH_MARKER:
        await redis_client.sadd(settings.TOKEN_ONLINE_REDIS_PREFIX, session_uuid)
        return True

    try:
        await jwt_authentication(token)
    except Exception as e:
        log.info(f'WebSocket 连接失败: {e!s}')
        return False

    await redis_client.sadd(settings.TOKEN_ONLINE_REDIS_PREFIX, session_uuid)
    return True


@sio.event
async def disconnect(sid) -> None:
    """Socket 断开连接事件"""
    # 清理传统会话
    await redis_client.spop(settings.TOKEN_ONLINE_REDIS_PREFIX)

    # 兼容清理早期版本可能遗留的 Socket.IO 映射；新连接不会创建该键。
    await redis_client.delete(f"hasn:ws:sid2id:{sid}")
