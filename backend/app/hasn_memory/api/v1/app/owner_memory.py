"""Owner 记忆 - 用户端（App scope）透明视图。

认证方式: DependsJwtAuth（当前登录用户）。owner 身份由 user_id -> hasn_humans.hasn_id 解析。

ADR 2026-05-30 §5.4「owner 透明」：主人可查看跨自己所有 Agent 合并后的 owner_memory
与各 Agent 上传的 contribution 流（符合"通信对主人透明"原则）。本视图只读。

ADR-15 收编：从 `app/hasn/api/v1/app/owner_memory.py` 迁入 `app/hasn_memory`；
URL 前缀 `/api/v1/hasn/app/owner/memory*` 保持不变（仍由 `app/hasn/api/router.py` 在
hasn `app` 路由内挂载，daemon 依赖），仅实现/目录归位。
"""

from typing import Annotated

import sqlalchemy as sa

from fastapi import APIRouter, Query, Request

from backend.app.hasn.model import HasnHumans
from backend.app.hasn_memory.schema.owner_memory import (
    OwnerMemoryContributionItem,
    OwnerMemoryContributionsResponse,
    OwnerMemoryResponse,
)
from backend.app.hasn_memory.service.owner_memory_service import owner_memory_service
from backend.common.exception import errors
from backend.common.response.response_schema import ResponseSchemaModel, response_base
from backend.common.security.jwt import DependsJwtAuth
from backend.database.db import CurrentSession

router = APIRouter()


async def _resolve_owner_id(request: Request, db: CurrentSession) -> str:
    user_id = request.user.id
    owner = (
        await db.execute(sa.select(HasnHumans.hasn_id).where(HasnHumans.user_id == user_id).limit(1))
    ).scalar_one_or_none()
    if not owner:
        raise errors.ForbiddenError(msg='当前用户未注册 HASN 身份')
    return owner


@router.get(
    '/memory',
    summary='查看本人 owner 记忆（合并后的 USER.md）',
    dependencies=[DependsJwtAuth],
)
async def get_my_owner_memory(
    request: Request,
    db: CurrentSession,
) -> ResponseSchemaModel[OwnerMemoryResponse]:
    owner_id = await _resolve_owner_id(request, db)
    memory = await owner_memory_service.get_owner_memory(db, owner_id=owner_id)
    return response_base.success(
        data=OwnerMemoryResponse(content=memory.get('content'), version=int(memory.get('version') or 0))
    )


@router.get(
    '/memory/contributions',
    summary='查看本人记忆贡献流（各 Agent 上传，主人透明）',
    dependencies=[DependsJwtAuth],
)
async def list_my_owner_memory_contributions(
    request: Request,
    db: CurrentSession,
    limit: Annotated[int, Query(ge=1, le=200, description='返回条数上限')] = 50,
) -> ResponseSchemaModel[OwnerMemoryContributionsResponse]:
    owner_id = await _resolve_owner_id(request, db)
    result = await owner_memory_service.list_contributions(db, owner_id=owner_id, limit=limit)
    return response_base.success(
        data=OwnerMemoryContributionsResponse(
            items=[OwnerMemoryContributionItem(**item) for item in result['items']],
            pending_count=int(result['pending_count']),
        )
    )
