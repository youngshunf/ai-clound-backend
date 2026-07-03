"""多任务编排（工作流）- Agent API（hasn_task 应用，设计 07 §9 云端补线）

路由前缀: /api/v1/hasn-task/agent
认证方式: Agent JWT（身份取自 JWT claims）。owner 隔离键 = ``agent.owner_hasn_id``；
scope 闸：workflow:read / workflow:manage / workflow:run。
工作流标识 = ``workflow_id``（= 端云稳定 workflow_uuid，与本地 hasn.workflow.* 工具一致）。
"""

from typing import Annotated

from fastapi import APIRouter, Path, Request
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from backend.app.hasn_task.schema.workflow import WorkflowEdgeSpec, WorkflowNodeSpec
from backend.app.hasn_task.service.agent_workflow_service import agent_workflow_service
from backend.common.dataclasses import AgentTokenPayload
from backend.common.response.response_schema import ResponseModel, response_base
from backend.common.security.agent_capability import require_capability_not_denied
from backend.common.security.agent_jwt_auth import DependsAgentJwtAuth
from backend.database.db import CurrentSession, CurrentSessionTransaction

router = APIRouter()

_SCOPE_READ = 'workflow:read'
_SCOPE_MANAGE = 'workflow:manage'
_SCOPE_RUN = 'workflow:run'


class AgentCreateWorkflowRequest(BaseModel):
    name: str = Field(min_length=1, max_length=200, description='工作流名称')
    goal: str | None = Field(default=None, description='总目标')
    nodes: list[WorkflowNodeSpec] = Field(min_length=1, description='节点列表')
    edges: list[WorkflowEdgeSpec] = Field(default_factory=list, description='依赖边列表')
    schedule_type: str = Field(default='once', description='整图定时 once/interval/cron')
    schedule_config: dict = Field(default_factory=dict, description='调度配置')
    timezone: str | None = None
    continuation_enabled: bool = Field(default=False, description='跨 fire 接续（二期）')


async def _agent(request: Request, db: AsyncSession, *scopes: str) -> AgentTokenPayload:
    agent: AgentTokenPayload = request.state.agent
    for scope in scopes:
        await require_capability_not_denied(db, agent.agent_hasn_id, scope)
    return agent


@router.post(
    '/workflows', summary='Agent 建工作流', dependencies=[DependsAgentJwtAuth], name='hasn_workflow_agent_create'
)
async def agent_create_workflow(
    request: Request,
    db: CurrentSessionTransaction,
    body: AgentCreateWorkflowRequest,
) -> ResponseModel:
    agent = await _agent(request, db, _SCOPE_MANAGE)
    workflow = await agent_workflow_service.create_workflow(db, agent=agent, params=body.model_dump())
    return response_base.success(data={'workflow': workflow})


@router.get('/workflows', summary='Agent 列工作流', dependencies=[DependsAgentJwtAuth], name='hasn_workflow_agent_list')
async def agent_list_workflows(request: Request, db: CurrentSession) -> ResponseModel:
    agent = await _agent(request, db, _SCOPE_READ)
    workflows = await agent_workflow_service.list_workflows(db, owner_id=agent.owner_hasn_id)
    return response_base.success(data={'workflows': workflows})


@router.get(
    '/agents', summary='Agent 发现可用分身', dependencies=[DependsAgentJwtAuth],
    name='hasn_workflow_agent_list_agents',
)
async def agent_list_agents(request: Request, db: CurrentSession) -> ResponseModel:
    agent = await _agent(request, db, _SCOPE_READ)
    agents = await agent_workflow_service.list_agents(db, owner_id=agent.owner_hasn_id)
    return response_base.success(data={'agents': agents})


@router.get(
    '/workflows/{workflow_uuid}', summary='Agent 查工作流图', dependencies=[DependsAgentJwtAuth],
    name='hasn_workflow_agent_get',
)
async def agent_get_workflow(
    request: Request,
    db: CurrentSession,
    workflow_uuid: Annotated[str, Path()],
) -> ResponseModel:
    agent = await _agent(request, db, _SCOPE_READ)
    detail = await agent_workflow_service.get_workflow(db, owner_id=agent.owner_hasn_id, workflow_uuid=workflow_uuid)
    return response_base.success(data=detail)


@router.get(
    '/workflows/{workflow_uuid}/nodes/{node_key}/result', summary='Agent 取节点产出',
    dependencies=[DependsAgentJwtAuth], name='hasn_workflow_agent_get_node_result',
)
async def agent_get_node_result(
    request: Request,
    db: CurrentSession,
    workflow_uuid: Annotated[str, Path()],
    node_key: Annotated[str, Path()],
) -> ResponseModel:
    agent = await _agent(request, db, _SCOPE_READ)
    result = await agent_workflow_service.get_node_result(
        db, owner_id=agent.owner_hasn_id, workflow_uuid=workflow_uuid, node_key=node_key
    )
    return response_base.success(data={'node': result})


@router.post(
    '/workflows/{workflow_uuid}/run', summary='Agent 立即触发整图', dependencies=[DependsAgentJwtAuth],
    name='hasn_workflow_agent_run',
)
async def agent_run_workflow(
    request: Request,
    db: CurrentSessionTransaction,
    workflow_uuid: Annotated[str, Path()],
) -> ResponseModel:
    agent = await _agent(request, db, _SCOPE_RUN)
    workflow = await agent_workflow_service.run(db, owner_id=agent.owner_hasn_id, workflow_uuid=workflow_uuid)
    return response_base.success(data={'workflow': workflow})


@router.post(
    '/workflows/{workflow_uuid}/pause', summary='Agent 暂停工作流', dependencies=[DependsAgentJwtAuth],
    name='hasn_workflow_agent_pause',
)
async def agent_pause_workflow(
    request: Request,
    db: CurrentSessionTransaction,
    workflow_uuid: Annotated[str, Path()],
) -> ResponseModel:
    agent = await _agent(request, db, _SCOPE_MANAGE)
    workflow = await agent_workflow_service.pause(db, owner_id=agent.owner_hasn_id, workflow_uuid=workflow_uuid)
    return response_base.success(data={'workflow': workflow})


@router.post(
    '/workflows/{workflow_uuid}/cancel', summary='Agent 取消整图执行', dependencies=[DependsAgentJwtAuth],
    name='hasn_workflow_agent_cancel',
)
async def agent_cancel_workflow(
    request: Request,
    db: CurrentSessionTransaction,
    workflow_uuid: Annotated[str, Path()],
) -> ResponseModel:
    agent = await _agent(request, db, _SCOPE_MANAGE)
    result = await agent_workflow_service.cancel(db, owner_id=agent.owner_hasn_id, workflow_uuid=workflow_uuid)
    return response_base.success(data=result)
