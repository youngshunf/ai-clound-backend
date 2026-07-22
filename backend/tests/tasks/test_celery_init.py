"""Celery 初始化回归测试。"""

from backend.app.task.celery import init_celery


def test_celery_initialization_uses_custom_task_backend() -> None:
    """初始化必须保留任务基类、结果后端和已启动状态跟踪配置。"""
    app = init_celery()

    assert app.main == 'fba_celery'
    assert app.conf.task_track_started is True
    assert app.loader.override_backends['db'] == 'backend.app.task.database:DatabaseBackend'
