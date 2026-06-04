"""Agent 权限管理路由（三态授权 + 工具目录，Owner JWT）

Q2 路由迁移：canonical 落到 Agent 资源域 `/agents/{id}/scopes`，
旧 `/community/settings/agents/{id}` 保留为兼容别名（转发同一 service）。

响应一律走 fba 标准信封 `ResponseModel`（`{code, msg, data}`）：daemon 的
envelope 解析依赖此约定，裸返回 Pydantic 模型会让 daemon 解析失败。
"""

from typing import Annotated

from fastapi import APIRouter, Depends, Request
from sqlalchemy.ext.asyncio import AsyncSession

from backend.app.hasn.crud.crud_hasn_humans import hasn_humans_dao
from backend.app.hasn.schema.agent_scopes import (
    AskDecisionRequest,
    AskRequestItem,
    AskRequestsResponse,
    GrantApprovalRequest,
    UpdateAgentScopesRequest,
)
from backend.app.hasn.service.agent_scopes_service import agent_scopes_service
from backend.common.exception import errors
from backend.common.response.response_schema import ResponseModel, response_base
from backend.common.security.jwt import DependsJwtAuth
from backend.database.db import get_db

router = APIRouter()


async def _owner_hasn_id(request: Request, db: AsyncSession) -> str:
    """从 Owner JWT 解析 HASN Human 身份。"""
    human = await hasn_humans_dao.get_by_user_id(db, user_id=request.user.id)
    if not human:
        raise errors.NotFoundError(msg='未找到对应的 HASN Human 记录')
    return human.hasn_id


# ---------- canonical：Agent 资源域 ----------


@router.get(
    '/agents/{agent_hasn_id}/scopes',
    summary='查询 Agent 权限配置（三态）',
    description='查询指定 Agent 的三态授权配置 default_mode/capability_modes（需要 Owner JWT）',
    dependencies=[DependsJwtAuth],
)
async def get_agent_scopes(
    request: Request,
    agent_hasn_id: str,
    db: Annotated[AsyncSession, Depends(get_db)],
) -> ResponseModel:
    owner_hasn_id = await _owner_hasn_id(request, db)
    config = await agent_scopes_service.get_agent_scopes(
        db=db,
        agent_hasn_id=agent_hasn_id,
        owner_hasn_id=owner_hasn_id,
    )
    return response_base.success(data=config)


@router.put(
    '/agents/{agent_hasn_id}/scopes',
    summary='更新 Agent 三态授权',
    description='更新 default_mode/capability_modes（D3 写表即时生效，不重签 JWT；需要 Owner JWT）',
    dependencies=[DependsJwtAuth],
)
async def update_agent_scopes(
    request: Request,
    agent_hasn_id: str,
    request_body: UpdateAgentScopesRequest,
    db: Annotated[AsyncSession, Depends(get_db)],
) -> ResponseModel:
    owner_hasn_id = await _owner_hasn_id(request, db)
    result = await agent_scopes_service.update_agent_scopes(
        db=db,
        agent_hasn_id=agent_hasn_id,
        owner_hasn_id=owner_hasn_id,
        request=request_body,
    )
    return response_base.success(data=result)


@router.get(
    '/agents/{agent_hasn_id}/scopes/catalog',
    summary='Agent 工具/scope 目录（三态）',
    description='按来源分组列出全部可见能力，每条带当前三态 mode 与展示元数据（需要 Owner JWT）',
    dependencies=[DependsJwtAuth],
)
async def get_scope_catalog(
    request: Request,
    agent_hasn_id: str,
    db: Annotated[AsyncSession, Depends(get_db)],
) -> ResponseModel:
    owner_hasn_id = await _owner_hasn_id(request, db)
    catalog = await agent_scopes_service.get_scope_catalog(
        db=db,
        agent_hasn_id=agent_hasn_id,
        owner_hasn_id=owner_hasn_id,
    )
    return response_base.success(data=catalog)


# ---------- ask 态批准请求（P6，主人侧列出/决定） ----------


@router.get(
    '/agents/{agent_hasn_id}/ask-requests',
    summary='列出 Agent 挂起的 ask 批准请求',
    description='列出该 Agent 当前因 ask 模式挂起、等待主人批准的工具调用（需要 Owner JWT）',
    dependencies=[DependsJwtAuth],
)
async def list_ask_requests(
    request: Request,
    agent_hasn_id: str,
    db: Annotated[AsyncSession, Depends(get_db)],
) -> ResponseModel:
    owner_hasn_id = await _owner_hasn_id(request, db)
    pending = await agent_scopes_service.list_ask_requests(
        db=db, agent_hasn_id=agent_hasn_id, owner_hasn_id=owner_hasn_id
    )
    data = AskRequestsResponse(requests=[AskRequestItem(**item) for item in pending])
    return response_base.success(data=data)


@router.post(
    '/agents/{agent_hasn_id}/ask-requests/{request_id}',
    summary='对挂起的 ask 请求做批准/拒绝',
    description='主人批准或拒绝某条挂起的工具调用（需要 Owner JWT）',
    dependencies=[DependsJwtAuth],
)
async def decide_ask_request(
    request: Request,
    agent_hasn_id: str,
    request_id: str,
    request_body: AskDecisionRequest,
    db: Annotated[AsyncSession, Depends(get_db)],
) -> ResponseModel:
    owner_hasn_id = await _owner_hasn_id(request, db)
    await agent_scopes_service.decide_ask_request(
        db=db,
        agent_hasn_id=agent_hasn_id,
        owner_hasn_id=owner_hasn_id,
        request_id=request_id,
        decision=request_body.decision,
    )
    return response_base.success(data={'request_id': request_id, 'decision': request_body.decision})


@router.post(
    '/agents/{agent_hasn_id}/approvals/{request_id}/grant',
    summary='批准一条 ask 审批请求（签一次性能力票据）',
    description=(
        '主人批准某挂起工具调用：签发一次性能力票据返回给 daemon（带票重试放行）；'
        'scope=always 额外写回 capability_modes=allow（以后不再 ask）。需要 Owner JWT。'
    ),
    dependencies=[DependsJwtAuth],
)
async def grant_approval(
    request: Request,
    agent_hasn_id: str,
    request_id: str,
    request_body: GrantApprovalRequest,
    db: Annotated[AsyncSession, Depends(get_db)],
) -> ResponseModel:
    owner_hasn_id = await _owner_hasn_id(request, db)
    result = await agent_scopes_service.grant_approval(
        db=db,
        agent_hasn_id=agent_hasn_id,
        owner_hasn_id=owner_hasn_id,
        request_id=request_id,
        scope=request_body.scope,
    )
    return response_base.success(data=result)


# ---------- 兼容别名：旧 /community/settings/agents/{id}（转发同一 service） ----------


@router.get(
    '/community/settings/agents/{agent_hasn_id}',
    summary='[兼容别名] 查询 Agent 权限配置',
    description='已迁移至 GET /agents/{agent_hasn_id}/scopes，保留兼容（需要 Owner JWT）',
    dependencies=[DependsJwtAuth],
    deprecated=True,
)
async def get_agent_scopes_compat(
    request: Request,
    agent_hasn_id: str,
    db: Annotated[AsyncSession, Depends(get_db)],
) -> ResponseModel:
    return await get_agent_scopes(request, agent_hasn_id, db)


@router.put(
    '/community/settings/agents/{agent_hasn_id}',
    summary='[兼容别名] 更新 Agent 权限配置',
    description='已迁移至 PUT /agents/{agent_hasn_id}/scopes，保留兼容（需要 Owner JWT）',
    dependencies=[DependsJwtAuth],
    deprecated=True,
)
async def update_agent_scopes_compat(
    request: Request,
    agent_hasn_id: str,
    request_body: UpdateAgentScopesRequest,
    db: Annotated[AsyncSession, Depends(get_db)],
) -> ResponseModel:
    return await update_agent_scopes(request, agent_hasn_id, request_body, db)
