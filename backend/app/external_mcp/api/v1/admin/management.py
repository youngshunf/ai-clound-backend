"""第三方 MCP 网关平台管理面（P7-D admin，事实源 10 §7.2 / 实施 99 P7-D）。

平台运营在 Vben Admin 配置 **system-origin** 平台 server（如 qcc）：注册、写平台 key、
配 per-owner 配额/限流。平台 key 是全体 owner 共享的付费凭据 → 必须配额防单 owner 刷爆
（10 §7.2 硬需求）。明文平台 key 仅前端→后端单向提交，加密落库，**永不回显**。

鉴权：Admin JWT + RBAC（RequestPermission）。一律统一信封。
"""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, Path

from backend.app.external_mcp.schema.management import (
    RegisterSystemServerParam,
    SetCredentialParam,
    SetServerQuotaParam,
    SetServerStatusParam,
)
from backend.app.external_mcp.service.gateway_service import external_mcp_gateway
from backend.common.exception import errors
from backend.common.response.response_schema import ResponseModel, response_base
from backend.common.security.jwt import DependsJwtAuth
from backend.common.security.permission import RequestPermission
from backend.common.security.rbac import DependsRBAC

router = APIRouter()


@router.get('/servers', summary='[Admin] 列 system-origin 平台 MCP server', dependencies=[DependsJwtAuth])
async def list_system_servers() -> ResponseModel:
    data = await external_mcp_gateway.list_servers_admin(origin='system')
    return response_base.success(data=data)


@router.get('/servers/{mcp_id}', summary='[Admin] 平台 server 详情', dependencies=[DependsJwtAuth])
async def get_system_server(mcp_id: Annotated[str, Path()]) -> ResponseModel:
    data = await external_mcp_gateway.get_server_detail(mcp_id=mcp_id, is_admin=True)
    if data is None:
        raise errors.NotFoundError(msg='server 不存在')
    return response_base.success(data=data)


@router.post(
    '/servers',
    summary='[Admin] 注册 system-origin 平台 MCP server',
    dependencies=[Depends(RequestPermission('external_mcp:server:add')), DependsRBAC],
)
async def register_system_server(obj: RegisterSystemServerParam) -> ResponseModel:
    server = await external_mcp_gateway.register_server(
        name=obj.name,
        hosting='remote_service',
        transport=obj.transport,
        origin='system',
        owner_hasn_id=None,
        endpoint=obj.endpoint,
        display_name=obj.display_name,
        scope='system',
        risk_level=obj.risk_level,
        per_owner_daily_quota=obj.per_owner_daily_quota,
        rate_limit_per_min=obj.rate_limit_per_min,
    )
    if obj.credential:
        await external_mcp_gateway.set_credential(
            mcp_id=server['mcp_id'],
            plaintext=obj.credential,
            is_admin=True,
            auth_header=obj.auth_header,
            auth_scheme=obj.auth_scheme,
        )
    return response_base.success(data={'mcp_id': server['mcp_id'], 'name': server['name']})


@router.post(
    '/servers/{mcp_id}/introspect',
    summary='[Admin] 自省平台 server',
    dependencies=[Depends(RequestPermission('external_mcp:server:edit')), DependsRBAC],
)
async def introspect_system_server(mcp_id: Annotated[str, Path()]) -> ResponseModel:
    data = await external_mcp_gateway.introspect_server(mcp_id)
    return response_base.success(data=data)


@router.put(
    '/servers/{mcp_id}/credential',
    summary='[Admin] 写入/轮换平台 key（明文不回显）',
    dependencies=[Depends(RequestPermission('external_mcp:server:edit')), DependsRBAC],
)
async def set_platform_credential(obj: SetCredentialParam, mcp_id: Annotated[str, Path()]) -> ResponseModel:
    data = await external_mcp_gateway.set_credential(
        mcp_id=mcp_id,
        plaintext=obj.credential,
        is_admin=True,
        auth_header=obj.auth_header,
        auth_scheme=obj.auth_scheme,
    )
    return response_base.success(data=data)


@router.delete(
    '/servers/{mcp_id}/credential',
    summary='[Admin] 撤销平台 key',
    dependencies=[Depends(RequestPermission('external_mcp:server:edit')), DependsRBAC],
)
async def revoke_platform_credential(mcp_id: Annotated[str, Path()]) -> ResponseModel:
    data = await external_mcp_gateway.revoke_credential(mcp_id=mcp_id, is_admin=True)
    return response_base.success(data=data)


@router.put(
    '/servers/{mcp_id}/quota',
    summary='[Admin] 配 per-owner 配额/限流（防刷爆平台 key）',
    dependencies=[Depends(RequestPermission('external_mcp:server:edit')), DependsRBAC],
)
async def set_server_quota(obj: SetServerQuotaParam, mcp_id: Annotated[str, Path()]) -> ResponseModel:
    data = await external_mcp_gateway.set_server_quota(
        mcp_id=mcp_id,
        per_owner_daily_quota=obj.per_owner_daily_quota,
        rate_limit_per_min=obj.rate_limit_per_min,
    )
    return response_base.success(data=data)


@router.put(
    '/servers/{mcp_id}/status',
    summary='[Admin] 启用/停用平台 server',
    dependencies=[Depends(RequestPermission('external_mcp:server:edit')), DependsRBAC],
)
async def set_system_server_status(obj: SetServerStatusParam, mcp_id: Annotated[str, Path()]) -> ResponseModel:
    data = await external_mcp_gateway.set_server_status(mcp_id=mcp_id, status=obj.status, is_admin=True)
    return response_base.success(data=data)


@router.delete(
    '/servers/{mcp_id}',
    summary='[Admin] 删除平台 server（连带凭据/绑定）',
    dependencies=[Depends(RequestPermission('external_mcp:server:del')), DependsRBAC],
)
async def delete_system_server(mcp_id: Annotated[str, Path()]) -> ResponseModel:
    ok = await external_mcp_gateway.delete_server(mcp_id=mcp_id, is_admin=True)
    return response_base.success(data={'deleted': ok})
