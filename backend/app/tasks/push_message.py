"""已退役的移动推送 Celery 兼容入口。

消息移动推送已由 ``hasn_im.consumers.push_notifier.PushNotifier`` 消费持久集成事件，
并明确采用 best-effort 契约。保留同名无副作用任务只为安全消费切换前已入队的旧消息，
禁止重新接入生产写点。
"""

from __future__ import annotations

from backend.app.task.celery import celery_app
from backend.common.log import log


@celery_app.task(name='push_message')
def push_message(message_id: int) -> str:
    """消费旧队列中的兼容消息，不再直接调用友盟。"""
    log.warning(f'[PushMessage] 旧 Celery 推送入口已退役，忽略 message_id={message_id}')
    return 'retired'
