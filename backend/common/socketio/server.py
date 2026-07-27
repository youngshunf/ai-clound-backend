"""传统业务 Socket.IO 服务器实例及连接事件。"""
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


async def connect(sid, environ, auth) -> bool:
    """
    Socket 连接事件
    对应设计文档 §3.1: Human 用 JWT, Agent 用 API Key
    """
    if not auth:
        log.error('WebSocket 连接失败: 无授权')
        return False

    session_uuid = auth.get('session_uuid')
    token = auth.get('token')

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


async def disconnect(sid) -> None:
    """Socket 断开连接事件"""
    await redis_client.spop(settings.TOKEN_ONLINE_REDIS_PREFIX)


sio.on('connect', handler=connect)
sio.on('disconnect', handler=disconnect)
