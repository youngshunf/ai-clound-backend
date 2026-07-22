import asyncio

from typing import Any

from celery import Task
from sqlalchemy.exc import SQLAlchemyError

from backend.common.log import log
from backend.common.socketio.actions import task_notification
from backend.core.conf import settings


def _log_task_notification_failure(notification: asyncio.Future[None]) -> None:
    """记录后台任务通知失败，避免未观察的协程异常。"""
    try:
        notification.result()
    except Exception:
        log.warning('任务生命周期通知发送失败', exc_info=True)


def _notify_task_event(message: str) -> None:
    """在同步 Celery 回调中可靠调度异步 Socket.IO 通知。"""
    notification = task_notification(msg=message)
    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        try:
            asyncio.run(notification)
        except Exception:
            log.warning('任务生命周期通知发送失败', exc_info=True)
    else:
        loop.create_task(notification).add_done_callback(_log_task_notification_failure)


class TaskBase(Task):
    """Celery 任务基类"""

    autoretry_for = (SQLAlchemyError,)
    max_retries = settings.CELERY_TASK_MAX_RETRIES

    def before_start(self, task_id: str, args: tuple[Any, ...], kwargs: dict[str, Any]) -> None:
        """
        任务开始前执行钩子

        :param task_id: 任务 ID
        :return:
        """
        _notify_task_event(f'任务 {task_id} 开始执行')

    def on_success(self, retval: Any, task_id: str, args: tuple[Any, ...], kwargs: dict[str, Any]) -> None:
        """
        任务成功后执行钩子

        :param retval: 任务返回值
        :param task_id: 任务 ID
        :return:
        """
        _notify_task_event(f'任务 {task_id} 执行成功')

    def on_failure(
        self,
        exc: Exception,
        task_id: str,
        args: tuple[Any, ...],
        kwargs: dict[str, Any],
        einfo: Any,
    ) -> None:
        """
        任务失败后执行钩子

        :param exc: 异常对象
        :param task_id: 任务 ID
        :param einfo: 异常信息
        :return:
        """
        _notify_task_event(f'任务 {task_id} 执行失败')
