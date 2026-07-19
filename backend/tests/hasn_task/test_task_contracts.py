"""任务中心四轴 API 契约静态测试。"""

from backend.app.hasn_task.api.v1.agent.task import AgentCreateTaskRequest, AgentUpdateTaskRequest
from backend.app.hasn_task.schema.task import CreateHasnTaskParam, GetHasnTaskDetail, UpdateHasnTaskParam


def test_owner_schemas_expose_task_axes() -> None:
    """owner 创建、更新、详情模型都必须投影任务中心四轴。"""
    expected = {'project_id', 'app_id', 'execution_kind', 'execution_spec'}
    for model in (CreateHasnTaskParam, UpdateHasnTaskParam, GetHasnTaskDetail):
        assert expected <= set(model.model_fields), f'{model.__name__} 缺四轴字段'


def test_agent_request_models_expose_task_axes() -> None:
    """Agent REST 创建和更新请求不能吞掉四轴字段。"""
    expected = {'project_id', 'app_id', 'execution_kind', 'execution_spec'}
    assert expected <= set(AgentCreateTaskRequest.model_fields)
    assert expected <= set(AgentUpdateTaskRequest.model_fields)
