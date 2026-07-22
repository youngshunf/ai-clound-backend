"""Celery 任务生命周期钩子回归测试。"""

import inspect

from backend.app.task.tasks.base import TaskBase


def test_lifecycle_hooks_are_synchronous_celery_callbacks() -> None:
    """Celery 同步回调不得返回未执行的协程对象。"""
    task = TaskBase()

    assert not inspect.iscoroutinefunction(TaskBase.before_start)
    assert not inspect.iscoroutinefunction(TaskBase.on_success)
    task.before_start('task-001', (), {})
    task.on_success({'ok': True}, 'task-001', (), {})
    task.on_failure(RuntimeError('失败'), 'task-001', (), {}, None)
