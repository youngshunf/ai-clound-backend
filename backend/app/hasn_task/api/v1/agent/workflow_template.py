"""工作流模板 - Agent API（hasn_task 应用，P3 模板层 doc11 §4 / §6）。

路径前缀: /api/v1/hasn-task/agent
认证方式: Agent JWT（身份取自 JWT claims）。owner 隔离键 = ``agent.owner_hasn_id``；
scope 闸：workflow:read（模板读 = 工作流读）。供 P5 的 hasn.workflow.template.list/get 工具承载。

可见性 = 内置 + 主人名下模板（与 app 面同规则，owner 取 agent.owner_hasn_id）。
"""

from typing import Annotated

from fastapi import APIRouter, Path, Query, Request
from sqlalchemy.ext.asyncio import AsyncSession

from backend.app.hasn_task.service.workflow_template_service import workflow_template_service
from backend.common.dataclasses import AgentTokenPayload
from backend.common.response.response_schema import ResponseModel, response_base
from backend.common.security.agent_capability import require_capability_not_denied
from backend.common.security.agent_jwt_auth import DependsAgentJwtAuth
from backend.database.db import CurrentSession

router = APIRouter()

_SCOPE_READ = 'workflow:read'


async def _agent(request: Request, db: AsyncSession, *scopes: str) -> AgentTokenPayload:
    agent: AgentTokenPayload = request.state.agent
    for scope in scopes:
        await require_capability_not_denied(db, agent.agent_hasn_id, scope)
    return agent


@router.get(
    '/workflow-templates', summary='Agent 列工作流模板', dependencies=[DependsAgentJwtAuth],
    name='hasn_workflow_template_agent_list',
)
async def agent_list_templates(
    request: Request,
    db: CurrentSession,
    domain_only: Annotated[bool, Query(description='只取场景模板（domain 非空）')] = False,
    domain: Annotated[str | None, Query(description='按领域 code 精确过滤')] = None,
    status: Annotated[str | None, Query(description='按状态过滤（缺省不过滤）')] = None,
) -> ResponseModel:
    agent = await _agent(request, db, _SCOPE_READ)
    data = await workflow_template_service.list_templates(
        db, owner_id=agent.owner_hasn_id, domain_only=domain_only, domain=domain, status=status
    )
    return response_base.success(data=data)


@router.get(
    '/workflow-templates/{template_key}', summary='Agent 取工作流模板详情',
    dependencies=[DependsAgentJwtAuth], name='hasn_workflow_template_agent_get',
)
async def agent_get_template(
    request: Request, db: CurrentSession, template_key: Annotated[str, Path()]
) -> ResponseModel:
    agent = await _agent(request, db, _SCOPE_READ)
    template = await workflow_template_service.get_template(
        db, owner_id=agent.owner_hasn_id, template_key=template_key
    )
    return response_base.success(data={'template': template})
