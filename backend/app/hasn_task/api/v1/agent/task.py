"""任务系统 - Agent API（hasn_task 应用，设计 06 §5 云端补线）

路由前缀: /api/v1/hasn-task/agent
认证方式: Agent JWT（身份取自 JWT claims，绝不读请求体身份）。
owner 隔离键 = ``agent.owner_hasn_id``；scope 闸：task:read / task:manage / task:run。
任务标识 = ``task_uuid``（端云稳定 ID，与本地 hasn.task.* 工具一致）。
"""

from typing import Annotated, Any

from fastapi import APIRouter, Path, Query, Request
from pydantic import BaseModel, Field

from backend.app.hasn_task.service.agent_task_service import agent_task_service
from backend.common.dataclasses import AgentTokenPayload
from backend.common.response.response_schema import ResponseModel, response_base
from backend.common.security.agent_jwt_auth import DependsAgentJwtAuth, check_scopes
from backend.database.db import CurrentSession, CurrentSessionTransaction

router = APIRouter()

_SCOPE_READ = 'task:read'
_SCOPE_MANAGE = 'task:manage'
_SCOPE_RUN = 'task:run'


class AgentCreateTaskRequest(BaseModel):
    name: str = Field(min_length=1, max_length=200, description='任务名称')
    prompt: str = Field(min_length=1, description='任务执行提示')
    agent_id: str | None = Field(default=None, description='目标分身，缺省=当前分身')
    schedule_type: str = Field(description='调度类型 once/interval/cron')
    schedule_config: dict = Field(default_factory=dict, description='调度配置')
    risk_level: str | None = Field(default=None, description='风险等级 low/high（KNOWU §4.5；缺省由调度类型决定）')
    description: str | None = None
    system_prompt: str | None = None
    skill_bundle_refs: list[dict] = Field(default_factory=list)
    timezone: str | None = None
    continuation_enabled: bool = Field(default=False, description='任务接续（D2）')
    enable_subagents: bool = Field(default=False, description='允许子分身（D5）')


class AgentUpdateTaskRequest(BaseModel):
    name: str | None = Field(default=None, max_length=200)
    description: str | None = None
    prompt: str | None = None
    system_prompt: str | None = None
    schedule_type: str | None = None
    schedule_config: dict | None = None
    timezone: str | None = None
    continuation_enabled: bool | None = None
    enable_subagents: bool | None = None


def _agent(request: Request, *scopes: str) -> AgentTokenPayload:
    agent: AgentTokenPayload = request.state.agent
    check_scopes(agent, list(scopes))
    return agent


@router.post('/tasks', summary='Agent 建任务', dependencies=[DependsAgentJwtAuth], name='hasn_task_agent_create')
async def agent_create_task(
    request: Request,
    db: CurrentSessionTransaction,
    body: AgentCreateTaskRequest,
) -> ResponseModel:
    agent = _agent(request, _SCOPE_MANAGE)
    task, created = await agent_task_service.create_task(db, agent=agent, params=body.model_dump())
    return response_base.success(data={'task': task, 'created': created})


@router.get('/tasks', summary='Agent 列任务', dependencies=[DependsAgentJwtAuth], name='hasn_task_agent_list')
async def agent_list_tasks(
    request: Request,
    db: CurrentSession,
    state: Annotated[str | None, Query(description='按状态过滤')] = None,
    limit: Annotated[int, Query(ge=1, le=100)] = 20,
) -> ResponseModel:
    agent = _agent(request, _SCOPE_READ)
    tasks = await agent_task_service.list_tasks(db, owner_id=agent.owner_hasn_id, state=state, limit=limit)
    return response_base.success(data={'tasks': tasks})


@router.get(
    '/tasks/{task_uuid}', summary='Agent 取任务', dependencies=[DependsAgentJwtAuth], name='hasn_task_agent_get'
)
async def agent_get_task(
    request: Request,
    db: CurrentSession,
    task_uuid: Annotated[str, Path()],
) -> ResponseModel:
    agent = _agent(request, _SCOPE_READ)
    row = await agent_task_service.get_task_row(db, owner_id=agent.owner_hasn_id, task_uuid=task_uuid)
    from backend.app.hasn_task.service.agent_task_service import task_to_public

    return response_base.success(data={'task': task_to_public(row)})


@router.put(
    '/tasks/{task_uuid}', summary='Agent 改任务（R2 守卫）', dependencies=[DependsAgentJwtAuth],
    name='hasn_task_agent_update',
)
async def agent_update_task(
    request: Request,
    db: CurrentSessionTransaction,
    task_uuid: Annotated[str, Path()],
    body: AgentUpdateTaskRequest,
) -> ResponseModel:
    agent = _agent(request, _SCOPE_MANAGE)
    task = await agent_task_service.update_task(db, agent=agent, task_uuid=task_uuid, patch=body.model_dump())
    return response_base.success(data={'task': task})


@router.post(
    '/tasks/{task_uuid}/pause', summary='Agent 暂停任务', dependencies=[DependsAgentJwtAuth],
    name='hasn_task_agent_pause',
)
async def agent_pause_task(
    request: Request,
    db: CurrentSessionTransaction,
    task_uuid: Annotated[str, Path()],
) -> ResponseModel:
    agent = _agent(request, _SCOPE_MANAGE)
    task = await agent_task_service.pause_task(db, owner_id=agent.owner_hasn_id, task_uuid=task_uuid)
    return response_base.success(data={'task': task})


@router.post(
    '/tasks/{task_uuid}/resume', summary='Agent 恢复任务（仅 paused）', dependencies=[DependsAgentJwtAuth],
    name='hasn_task_agent_resume',
)
async def agent_resume_task(
    request: Request,
    db: CurrentSessionTransaction,
    task_uuid: Annotated[str, Path()],
) -> ResponseModel:
    agent = _agent(request, _SCOPE_MANAGE)
    task = await agent_task_service.resume_task(db, owner_id=agent.owner_hasn_id, task_uuid=task_uuid)
    return response_base.success(data={'task': task})


@router.delete(
    '/tasks/{task_uuid}', summary='Agent 删任务（软删）', dependencies=[DependsAgentJwtAuth],
    name='hasn_task_agent_delete',
)
async def agent_delete_task(
    request: Request,
    db: CurrentSessionTransaction,
    task_uuid: Annotated[str, Path()],
) -> ResponseModel:
    agent = _agent(request, _SCOPE_MANAGE)
    await agent_task_service.delete_task(db, owner_id=agent.owner_hasn_id, task_uuid=task_uuid)
    return response_base.success(data={'deleted': True})


@router.post(
    '/tasks/{task_uuid}/run-now', summary='Agent 立即触发（仅 scheduled/paused）',
    dependencies=[DependsAgentJwtAuth], name='hasn_task_agent_run_now',
)
async def agent_run_now(
    request: Request,
    db: CurrentSessionTransaction,
    task_uuid: Annotated[str, Path()],
) -> ResponseModel:
    agent = _agent(request, _SCOPE_RUN)
    task = await agent_task_service.run_now(db, owner_id=agent.owner_hasn_id, task_uuid=task_uuid)
    return response_base.success(data={'task': task})


@router.get(
    '/tasks/{task_uuid}/runs', summary='Agent 列执行记录（run 摘要倒序）',
    dependencies=[DependsAgentJwtAuth], name='hasn_task_agent_list_runs',
)
async def agent_list_runs(
    request: Request,
    db: CurrentSession,
    task_uuid: Annotated[str, Path()],
    limit: Annotated[int, Query(ge=1, le=50)] = 10,
) -> ResponseModel:
    agent = _agent(request, _SCOPE_READ)
    await agent_task_service.get_task_row(db, owner_id=agent.owner_hasn_id, task_uuid=task_uuid)
    runs = await agent_task_service.list_run_summaries(
        db, owner_id=agent.owner_hasn_id, task_uuid=task_uuid, limit=limit
    )
    return response_base.success(data={'runs': runs})


@router.get(
    '/runs/{run_uuid}', summary='Agent 取单次结果（output_summary + 产物）',
    dependencies=[DependsAgentJwtAuth], name='hasn_task_agent_get_run',
)
async def agent_get_run(
    request: Request,
    db: CurrentSession,
    run_uuid: Annotated[str, Path()],
) -> ResponseModel:
    agent = _agent(request, _SCOPE_READ)
    run = await agent_task_service.get_run_summary(db, owner_id=agent.owner_hasn_id, run_uuid=run_uuid)
    return response_base.success(data={'run': run})


@router.get(
    '/tasks/{task_uuid}/results', summary='Agent 查任务历史结果（接续深查，D2）',
    dependencies=[DependsAgentJwtAuth], name='hasn_task_agent_query_results',
)
async def agent_query_results(
    request: Request,
    db: CurrentSession,
    task_uuid: Annotated[str, Path()],
    limit: Annotated[int, Query(ge=1, le=50)] = 10,
    status: Annotated[str | None, Query(description='success/error')] = None,
    include_artifacts: Annotated[bool, Query()] = True,  # noqa: FBT002 FastAPI query 参数惯用形态
) -> ResponseModel:
    agent = _agent(request, _SCOPE_READ)
    await agent_task_service.get_task_row(db, owner_id=agent.owner_hasn_id, task_uuid=task_uuid)
    results: list[dict[str, Any]] = await agent_task_service.list_run_summaries(
        db,
        owner_id=agent.owner_hasn_id,
        task_uuid=task_uuid,
        limit=limit,
        status=status,
        include_artifacts=include_artifacts,
    )
    return response_base.success(data={'results': results})
