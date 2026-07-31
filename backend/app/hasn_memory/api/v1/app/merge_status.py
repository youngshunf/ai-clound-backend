"""合并状态 - 用户端（App scope）可见性视图（doc19 §5.5）。

认证方式：`DependsJwtAuth`（当前登录用户）。owner 身份由 user_id → hasn_humans.hasn_id 解析。

§5.5「主脑单点：必须可见，不许静默停摆」：主脑设备关机 → 合并停摆 → 跨设备记忆不收敛。这是
本方案接受的代价，但**不接受静默**。本端点是主人记忆页「上次整理于 X，主脑在 <设备> 上，
当前离线」那一行的唯一数据源，并在超过阈值未成功合并时置位 `stale_over_threshold`——
**如实告知，不是错误**。

URL：`/api/v1/hasn/app/memory/merge/status`（daemon 代理给 WebUI，WebUI 不直连云端）。
"""

from fastapi import APIRouter, Request

from backend.app.hasn_memory.api.v1.app.owner_scope import resolve_owner_id as _resolve_owner_id
from backend.app.hasn_memory.schema.merge_gate import MergeStatusResponse
from backend.app.hasn_memory.service.merge_gate_service import merge_gate_service
from backend.common.response.response_schema import ResponseSchemaModel, response_base
from backend.common.security.jwt import DependsJwtAuth
from backend.database.db import CurrentSession

router = APIRouter()


@router.get(
    '/merge/status',
    summary='查看本人记忆整理状态（上次整理时间 / 主脑设备 / 待办 / 是否超阈值）',
    dependencies=[DependsJwtAuth],
)
async def get_my_merge_status(
    request: Request,
    db: CurrentSession,
) -> ResponseSchemaModel[MergeStatusResponse]:
    owner_id = await _resolve_owner_id(request, db)
    return response_base.success(data=await merge_gate_service.merge_status(db, owner_id=owner_id))
