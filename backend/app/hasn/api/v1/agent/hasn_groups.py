"""HASN 群组 - Agent 端 API（只读：分身所在群 + 名册）。

认证: Agent JWT（DependsAgentJwtAuth），身份取自 request.state.agent。
用途: daemon 以 Agent 身份经 BackendGateway agent 通道拉取群名册（G4 派发取 candidate/
roster 用）。建/管群属主人权限，不在 Agent 面暴露。
统一信封: response_base.success。
"""
from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Path, Request

from backend.app.hasn.service.hasn_group_service import hasn_group_service
from backend.common.dataclasses import AgentTokenPayload
from backend.common.response.response_schema import ResponseSchemaModel, response_base
from backend.common.security.agent_jwt_auth import DependsAgentJwtAuth
from backend.database.db import CurrentImSession

router = APIRouter()


@router.get('', summary='分身所在群列表', dependencies=[DependsAgentJwtAuth])
async def agent_list_groups(request: Request, db: CurrentImSession) -> ResponseSchemaModel[dict]:
    agent: AgentTokenPayload = request.state.agent
    items = await hasn_group_service.list_my_groups(db=db, hasn_id=agent.agent_hasn_id)
    return response_base.success(data={'items': items})


@router.get('/{group_id}', summary='群名册（分身视角）', dependencies=[DependsAgentJwtAuth])
async def agent_group_detail(
    request: Request,
    db: CurrentImSession,
    group_id: Annotated[str, Path(description='群组公开 ID g:NNNNNN')],
) -> ResponseSchemaModel[dict]:
    agent: AgentTokenPayload = request.state.agent
    data = await hasn_group_service.get_group_detail(
        db=db, hasn_id=agent.agent_hasn_id, group_id=group_id
    )
    return response_base.success(data=data)
