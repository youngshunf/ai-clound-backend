"""HASN Agent  - Agent API

认证方式: Agent JWT (DependsAgentJwtAuth)
Agent 信息: 通过 request.state.agent 获取 AgentTokenPayload
"""
from typing import Annotated

from fastapi import APIRouter, Path, Request

from backend.app.hasn.schema.hasn_agents import (
    AgentHeartbeatRequest,
    AgentHeartbeatResponse,
)
from backend.app.hasn.service.hasn_agents_service import (
    agent_profile_service,
)
from backend.common.dataclasses import AgentTokenPayload
from backend.common.exception import errors
from backend.common.response.response_schema import (
    ResponseSchemaModel,
    response_base,
)
from backend.common.security.agent_jwt_auth import DependsAgentJwtAuth
from backend.database.db import CurrentSessionTransaction

router = APIRouter()


@router.post(
    "/by-hasn-id/{hasn_id}/heartbeat",
    summary="Agent 上报自身心跳",
    dependencies=[DependsAgentJwtAuth],
)
async def agent_report_agent_heartbeat(
    request: Request,
    db: CurrentSessionTransaction,
    hasn_id: Annotated[str, Path(description="Agent HASN ID, 如 a_xxx")],
    body: AgentHeartbeatRequest,
) -> ResponseSchemaModel[AgentHeartbeatResponse]:
    """Agent Runtime 定期调用，上报自身在线状态和心跳时间。"""
    agent: AgentTokenPayload = request.state.agent
    if hasn_id != agent.agent_hasn_id:
        raise errors.ForbiddenError(msg="ERR_HASN_AGENT_SELF_HEARTBEAT_ONLY")

    result = await agent_profile_service.update_heartbeat(
        db,
        hasn_id=hasn_id,
        request=body,
        user_id=agent.owner_user_id,
    )
    return response_base.success(data=result)
