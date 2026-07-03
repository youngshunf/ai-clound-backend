"""Agent MCP 接入凭证 - 用户端 API（Owner JWT）

owner 为自己名下的 Agent 签发/查看/吊销 MCP 接入凭证。
owner 身份由 JWT -> hasn_humans.hasn_id 解析；签发明文仅返回一次。
"""

from typing import Annotated

import sqlalchemy as sa

from fastapi import APIRouter, Path, Request

from backend.app.hasn.model import HasnHumans
from backend.app.hasn.schema.hasn_agent_mcp_keys import (
    AgentMcpKeyInfo,
    IssueAgentMcpKeyParam,
    IssuedAgentMcpKey,
)
from backend.app.hasn.service.hasn_agent_mcp_keys_service import hasn_agent_mcp_keys_service
from backend.common.exception import errors
from backend.common.response.response_schema import ResponseModel, ResponseSchemaModel, response_base
from backend.common.security.jwt import DependsJwtAuth
from backend.database.db import CurrentSession, CurrentSessionTransaction

router = APIRouter()


async def _current_owner_hasn_id(db: CurrentSession, user_id: int) -> str:
    """由当前登录用户解析 owner 的 HASN ID"""
    owner_hasn_id = (
        await db.execute(sa.select(HasnHumans.hasn_id).where(HasnHumans.user_id == user_id).limit(1))
    ).scalar_one_or_none()
    if not owner_hasn_id:
        raise errors.NotFoundError(msg='当前用户尚未绑定 HASN 身份')
    return owner_hasn_id


@router.post(
    '',
    summary='签发 Agent MCP 接入凭证（明文仅返回一次）',
    dependencies=[DependsJwtAuth],
    name='app_issue_agent_mcp_key',
)
async def issue_agent_mcp_key(
    request: Request,
    db: CurrentSessionTransaction,
    obj: IssueAgentMcpKeyParam,
) -> ResponseSchemaModel[IssuedAgentMcpKey]:
    user_id = request.user.id
    owner_hasn_id = await _current_owner_hasn_id(db, user_id)
    issued = await hasn_agent_mcp_keys_service.issue(
        db, obj=obj, owner_hasn_id=owner_hasn_id, owner_user_id=user_id
    )
    return response_base.success(data=issued)


@router.get(
    '',
    summary='获取我名下的 Agent MCP 接入凭证列表（不含明文）',
    dependencies=[DependsJwtAuth],
    name='app_list_agent_mcp_keys',
)
async def list_agent_mcp_keys(
    request: Request,
    db: CurrentSession,
) -> ResponseSchemaModel[list[AgentMcpKeyInfo]]:
    owner_hasn_id = await _current_owner_hasn_id(db, request.user.id)
    data = await hasn_agent_mcp_keys_service.list_for_owner(db, owner_hasn_id)
    return response_base.success(data=data)


@router.delete(
    '/{pk}',
    summary='吊销 Agent MCP 接入凭证（即时失效）',
    dependencies=[DependsJwtAuth],
    name='app_revoke_agent_mcp_key',
)
async def revoke_agent_mcp_key(
    request: Request,
    db: CurrentSessionTransaction,
    pk: Annotated[int, Path(description='凭证 ID')],
) -> ResponseModel:
    owner_hasn_id = await _current_owner_hasn_id(db, request.user.id)
    await hasn_agent_mcp_keys_service.revoke(db, pk=pk, owner_hasn_id=owner_hasn_id)
    return response_base.success()
