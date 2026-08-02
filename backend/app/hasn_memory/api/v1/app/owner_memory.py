"""Owner 记忆 - 用户端（App scope）合并态视图。

认证方式: DependsJwtAuth（当前登录用户）。owner 身份由 user_id -> hasn_humans.hasn_id 解析。

主人可查看跨自己所有 Agent 合并后的 owner_memory。本视图只读；原 contribution 流已退役。

ADR-15 收编：从 `app/hasn/api/v1/app/owner_memory.py` 迁入 `app/hasn_memory`；
URL 前缀 `/api/v1/hasn/app/owner/memory*` 保持不变（仍由 `app/hasn/api/router.py` 在
hasn `app` 路由内挂载，daemon 依赖），仅实现/目录归位。
"""

from fastapi import APIRouter, Request

from backend.app.hasn_memory.api.v1.app.owner_scope import resolve_owner_id as _resolve_owner_id
from backend.app.hasn_memory.schema.owner_memory import OwnerMemoryResponse
from backend.app.hasn_memory.service.owner_memory_service import owner_memory_service
from backend.common.response.response_schema import ResponseSchemaModel, response_base
from backend.common.security.jwt import DependsJwtAuth
from backend.database.db import CurrentSession

router = APIRouter()


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
        data=OwnerMemoryResponse(
            content=memory.get('content'),
            version=int(memory.get('version') or 0),
            owner_edited=bool(memory.get('owner_edited')),
        )
    )
