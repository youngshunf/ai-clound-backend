"""通用 Socket.IO 事件处理。"""
from typing import Any

from backend.common.log import log
from backend.common.socketio.server import sio


async def task_notification(msg: str) -> None:
    """任务通知"""
    await sio.emit('task_notification', {'msg': msg})


async def _reject_legacy_hasn_event(sid: str, event: str) -> None:
    """拒绝绕过 Node 通道的遗留 HASN Socket.IO 事件。"""
    log.warning(f'[HASN Socket.IO] 拒绝已废弃事件: {event}')
    await sio.emit(
        'hasn_error',
        {
            'code': 2004,
            'message': 'HASN 消息和已读回执必须通过 daemon 的 Node 通道处理。',
        },
        to=sid,
    )


@sio.on('hasn_message')
async def handle_hasn_message(sid: str, _data: dict[str, Any]) -> None:
    """拒绝遗留的 HASN Socket.IO 发信事件。"""
    await _reject_legacy_hasn_event(sid, 'hasn_message')


@sio.on('hasn_read')
async def handle_hasn_read(sid: str, _data: dict[str, Any]) -> None:
    """拒绝遗留的 HASN Socket.IO 已读事件。"""
    await _reject_legacy_hasn_event(sid, 'hasn_read')


@sio.on('hasn_ping')
async def handle_hasn_ping(sid: str, data: dict[str, Any]) -> None:
    """心跳 (对应设计文档 cmd=PING/PONG)"""
    await sio.emit('hasn_pong', {'ts': data.get('ts')}, to=sid)
