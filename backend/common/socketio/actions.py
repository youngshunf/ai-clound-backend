"""通用 Socket.IO 事件处理。"""

from backend.common.socketio.manager import build_socketio_sync_publisher
from backend.common.socketio.server import sio

_task_notification_manager = build_socketio_sync_publisher()


async def task_notification(msg: str) -> None:
    """发送任务通知。"""
    await sio.emit('task_notification', {'msg': msg})


def task_notification_sync(msg: str) -> None:
    """从 Celery 同步生命周期钩子发送任务通知。"""
    _task_notification_manager.emit('task_notification', {'msg': msg})
