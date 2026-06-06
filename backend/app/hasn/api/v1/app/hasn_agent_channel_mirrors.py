"""HASN Agent 渠道脱敏摘要跨设备镜像 - 用户端 API（仅可见性，非操作路径）

认证方式: DependsJwtAuth（Owner JWT）。
数据隔离: owner_id 由 JWT 的 request.user.id 经 hasn_humans 解析，不取自 query/body；SQL 强制
          WHERE owner_id=<resolved>（三道隔离防线，设计 §6.3）。

设计事实源：
- docs/hasn-node设计文档/桌面端第三方IM渠道接入/01-桌面端第三方IM渠道接入总体设计.md §6.1/§6.3/§8.5/§3.3。

仅暴露两个端点（best-effort 跨设备可见性镜像，不提供任何渠道操作代理）：
- GET  /api/v1/hasn/app/agent-channel-mirrors          当前 owner 跨设备摘要列表
- PUT  /api/v1/hasn/app/agent-channel-mirrors/upsert   daemon 上报脱敏摘要（时间择新 upsert）
"""
from fastapi import APIRouter, Request

from backend.app.hasn.schema.hasn_agent_channel_mirrors import UpsertChannelMirrorRequest
from backend.app.hasn.service.hasn_agent_channel_mirrors_service import hasn_agent_channel_mirrors_service
from backend.common.response.response_schema import ResponseModel, response_base
from backend.common.security.jwt import DependsJwtAuth
from backend.database.db import CurrentSession, CurrentSessionTransaction

router = APIRouter()


@router.get(
    '',
    summary='获取当前 owner 跨设备渠道脱敏摘要列表（仅可见性）',
    dependencies=[DependsJwtAuth],
)
async def list_my_channel_mirrors(request: Request, db: CurrentSession) -> ResponseModel:
    """owner_id 由 JWT 解析（hasn_humans.user_id=request.user.id），强制 WHERE owner_id。"""
    data = await hasn_agent_channel_mirrors_service.list_for_owner(db=db, user_id=request.user.id)
    return response_base.success(data=data)


@router.put(
    '/upsert',
    summary='daemon 上报渠道脱敏摘要（owner_id 由 JWT 覆盖，时间择新）',
    dependencies=[DependsJwtAuth],
)
async def upsert_my_channel_mirror(
    request: Request,
    db: CurrentSessionTransaction,
    obj: UpsertChannelMirrorRequest,
) -> ResponseModel:
    """owner_id 不信任 body，由 JWT 覆盖；写库前过 safe_json 第④层兜底脱敏（§8.5-④）。"""
    data = await hasn_agent_channel_mirrors_service.upsert_for_owner(db=db, user_id=request.user.id, request=obj)
    return response_base.success(data=data)
