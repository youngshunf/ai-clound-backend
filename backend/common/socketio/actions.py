"""通用 Socket.IO 事件处理。"""

import urllib.parse

import socketio

from backend.common.socketio.server import sio
from backend.core.conf import settings

_task_notification_manager = socketio.RedisManager(
    (
        f'redis://:{urllib.parse.quote(settings.REDIS_PASSWORD)}'
        f'@{settings.REDIS_HOST}:{settings.REDIS_PORT}/{settings.REDIS_DATABASE}'
    ),
    write_only=True,
)


async def task_notification(msg: str) -> None:
    """发送任务通知。"""
    await sio.emit('task_notification', {'msg': msg})


def task_notification_sync(msg: str) -> None:
    """从 Celery 同步生命周期钩子发送任务通知。"""
    _task_notification_manager.emit('task_notification', {'msg': msg})
