"""任务示例参数边界回归测试。"""

import pytest

from backend.app.task.tasks.tasks import task_demo_params

pytestmark = pytest.mark.asyncio(loop_scope='module')


async def test_demo_task_omits_optional_suffix_when_absent() -> None:
    """可选后缀缺失时，任务仍必须返回确定的基础文本。"""
    assert await task_demo_params.run('hello', None) == 'hello'
