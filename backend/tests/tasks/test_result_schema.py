"""任务结果响应模型回归测试。"""

from datetime import UTC, datetime

from celery import states

from backend.app.task.model.result import TaskExtended
from backend.app.task.schema.result import GetTaskResultDetail


def test_task_result_detail_reads_and_serializes_kwargs_field() -> None:
    """任务结果的 ORM 属性与 API 字段名均保持 ``kwargs``。"""
    task = TaskExtended('task-001')
    task.id = 1
    task.status = states.SUCCESS
    task.result = {'answer': 42}
    task.date_done = datetime(2026, 7, 23, tzinfo=UTC)
    task.traceback = None
    task.name = 'demo.task'
    task.args = b'[]'
    task.kwargs = b'{}'
    task.worker = 'worker-1'
    task.retries = 1
    task.queue = 'default'

    detail = GetTaskResultDetail.model_validate(task)

    serialized = detail.model_dump(by_alias=True)
    assert serialized['kwargs'] == {}
    assert 'task_kwargs' not in serialized
