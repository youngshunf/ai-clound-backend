"""通用 Socket.IO 事件处理。"""

from backend.common.socketio.server import sio


async def task_notification(msg: str) -> None:
    """发送任务通知。"""
    await sio.emit('task_notification', {'msg': msg})
