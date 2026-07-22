"""Celery 结果模型回归测试。"""

from celery import states

from backend.app.task.model.result import Task, TaskExtended, TaskSet


def test_task_result_models_preserve_serialized_fields() -> None:
    """基础、扩展和任务集结果均可保留并序列化运行时字段。"""
    task = Task('task-001')
    task.status = states.SUCCESS
    task.result = {'answer': 42}
    task.traceback = None

    extended = TaskExtended('task-002')
    extended.name = 'demo.task'
    extended.args = b'[]'
    extended.kwargs = b'{}'
    extended.worker = 'worker-1'
    extended.retries = 1
    extended.queue = 'default'

    task_set = TaskSet('set-001', ['task-001', 'task-002'])

    assert task.to_dict()['result'] == {'answer': 42}
    assert extended.to_dict()['name'] == 'demo.task'
    assert extended.to_dict()['queue'] == 'default'
    assert task_set.to_dict()['result'] == ['task-001', 'task-002']
