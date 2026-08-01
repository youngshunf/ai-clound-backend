"""Celery 生命周期通知的真实 Redis 回归测试。"""

from __future__ import annotations

from uuid import uuid4

from backend.app.task.tasks.base import TaskBase


def test_task_base_can_publish_consecutive_notifications() -> None:
    """连续任务必须共用安全的同步发布边界，不能复用已关闭的 asyncio loop。"""
    trace_id = uuid4().hex

    TaskBase._notify(f'真实回归通知一：{trace_id}')
    TaskBase._notify(f'真实回归通知二：{trace_id}')
