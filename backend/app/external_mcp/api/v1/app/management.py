"""第三方 MCP 网关 owner 管理面（P7-D，事实源 10 §7.1 / 实施 99 P7-D）。

主人在 webui「我的 MCP」配置远程第三方 MCP server、写/轮换/撤销凭据、把 server 工具
绑定给自己的 Agent。**webui 经 daemon 薄代理调用本面**（铁律：webui 不直连云端）。

身份恒取自 Owner JWT（request.user.id → owner_hasn_id 行级隔离）；明文凭据仅前端→后端
单向提交，后端加密落库，**出参绝无明文**。一律统一信封（ResponseModel + response_base.success）。
"""

from __future__ import annotations

from fastapi import APIRouter, Path, Request
from sqlalchemy import select

from backend.app.external_mcp.schema.management import (
    CreateBindingParam,
    RegisterOwnerServerParam,
    SetBindingEnabledParam,
    SetCredentialParam,
    SetServerStatusParam,
)
from backend.app.external_mcp.service.gateway_service import external_mcp_gateway
from backend.app.external_mcp.service.quota import quota_service
from backend.app.hasn.model.hasn_agents import HasnAgents
from backend.app.hasn.service.app_catalog_service import resolve_owner_hasn_id
from backend.common.exception import errors
from backend.common.response.response_schema import ResponseModel, response_base
from backend.common.security.jwt import DependsJwtAuth
from backend.database.db import CurrentSession

router = APIRouter()


async def _owner(db: CurrentSession, request: Request) -> str:
    """从 Owner JWT 解析 owner_hasn_id；无平台身份映射则拒（行级隔离前提）。"""
    owner_hasn_id = await resolve_owner_hasn_id(db, user_id=request.user.id)
    if not owner_hasn_id:
        raise errors.ForbiddenError(msg='当前账号未关联唤星身份，无法管理第三方 MCP')
    return owner_hasn_id


async def _require_owned_agent(db: CurrentSession, *, agent_hasn_id: str, owner_hasn_id: str) -> None:
    """绑定前校验该 Agent 确属本主人（杜绝把别人的 Agent 绑上自己的 server）。"""
    row = (
        await db.execute(
            select(HasnAgents.hasn_id).where(
                HasnAgents.hasn_id == agent_hasn_id,
                HasnAgents.owner_id == owner_hasn_id,
            )
        )
    ).scalar_one_or_none()
    if row is None:
        raise errors.ForbiddenError(msg='该分身不在你名下，无法绑定外部 MCP')


# ============================ server 配置 ============================


@router.get('/servers', summary='[Owner] 列我的第三方 MCP server（含可绑定的 system 共享）', dependencies=[DependsJwtAuth])
async def list_servers(request: Request, db: CurrentSession, include_system: bool = True) -> ResponseModel:
    owner_hasn_id = await _owner(db, request)
    data = await external_mcp_gateway.list_servers(owner_hasn_id=owner_hasn_id, include_system=include_system)
    return response_base.success(data=data)


@router.get('/servers/{mcp_id}', summary='[Owner] server 详情（含已自省工具）', dependencies=[DependsJwtAuth])
async def get_server(request: Request, db: CurrentSession, mcp_id: str = Path(...)) -> ResponseModel:
    owner_hasn_id = await _owner(db, request)
    data = await external_mcp_gateway.get_server_detail(mcp_id=mcp_id, owner_hasn_id=owner_hasn_id)
    if data is None:
        raise errors.NotFoundError(msg='server 不存在或无权访问')
    return response_base.success(data=data)


@router.post('/servers', summary='[Owner] 注册第三方 MCP（remote_service / local_process，origin=owner）', dependencies=[DependsJwtAuth])
async def register_server(request: Request, db: CurrentSession, obj: RegisterOwnerServerParam) -> ResponseModel:
    owner_hasn_id = await _owner(db, request)
    server = await external_mcp_gateway.register_server(
        name=obj.name,
        hosting=obj.hosting,
        transport=obj.transport,
        origin='owner',
        owner_hasn_id=owner_hasn_id,
        endpoint=obj.endpoint,
        command=obj.command,
        args=obj.args,
        env=obj.env,
        display_name=obj.display_name,
        scope='owner',
        risk_level=obj.risk_level,
    )
    # 提供了凭据则立即写入并接进 header 模板（明文不回显）。
    if obj.credential:
        await external_mcp_gateway.set_credential(
            mcp_id=server['mcp_id'],
            plaintext=obj.credential,
            owner_hasn_id=owner_hasn_id,
            auth_header=obj.auth_header,
            auth_scheme=obj.auth_scheme,
        )
    return response_base.success(data={'mcp_id': server['mcp_id'], 'name': server['name']})


@router.post('/servers/{mcp_id}/introspect', summary='[Owner] 自省 server（拉工具列表）', dependencies=[DependsJwtAuth])
async def introspect_server(request: Request, db: CurrentSession, mcp_id: str = Path(...)) -> ResponseModel:
    owner_hasn_id = await _owner(db, request)
    detail = await external_mcp_gateway.get_server_detail(mcp_id=mcp_id, owner_hasn_id=owner_hasn_id)
    if detail is None or detail.get('owner_hasn_id') != owner_hasn_id:
        raise errors.ForbiddenError(msg='无权自省该 server（仅限自配 server）')
    data = await external_mcp_gateway.introspect_server(mcp_id)
    return response_base.success(data=data)


@router.post(
    '/servers/{mcp_id}/resolve-env',
    summary='[Owner] 建连时解析 local_process server 的 env 凭据（实时下发本机 daemon）',
    dependencies=[DependsJwtAuth],
)
async def resolve_server_env(request: Request, db: CurrentSession, mcp_id: str = Path(...)) -> ResponseModel:
    """local_process 建连凭据实时解析（P7-G G3，doc101 §2.1.2）。

    daemon 在 spawn 本机子进程前调本端点，取该 server 的 env 把 `secret://` 引用解析为明文，注入子进程
    env 后即用即弃。**仅此一处把明文下发给 owner 自己的 daemon**（owner 解析 owner 自己的密钥是合法使用，
    非「下发」）；明文不落审计/日志。仅 local_process + 非 system-origin + 属本 owner 的 server 可解析。
    任一引用未配置/已撤销 → CREDENTIAL_MISSING（撤销后软挡）。
    """
    owner_hasn_id = await _owner(db, request)
    env = await external_mcp_gateway.resolve_env_for_owner(mcp_id=mcp_id, owner_hasn_id=owner_hasn_id)
    return response_base.success(data={'mcp_id': mcp_id, 'env': env})


@router.put('/servers/{mcp_id}/credential', summary='[Owner] 写入/轮换 server 凭据（明文不回显）', dependencies=[DependsJwtAuth])
async def set_credential(request: Request, db: CurrentSession, obj: SetCredentialParam, mcp_id: str = Path(...)) -> ResponseModel:
    owner_hasn_id = await _owner(db, request)
    data = await external_mcp_gateway.set_credential(
        mcp_id=mcp_id,
        plaintext=obj.credential,
        owner_hasn_id=owner_hasn_id,
        auth_header=obj.auth_header,
        auth_scheme=obj.auth_scheme,
    )
    return response_base.success(data=data)


@router.delete(
    '/servers/{mcp_id}/credential',
    summary='[Owner] 撤销 server 凭据（撤销后调用软挡）',
    name='external_mcp_revoke_credential',
    dependencies=[DependsJwtAuth],
)
async def revoke_credential(request: Request, db: CurrentSession, mcp_id: str = Path(...)) -> ResponseModel:
    owner_hasn_id = await _owner(db, request)
    data = await external_mcp_gateway.revoke_credential(mcp_id=mcp_id, owner_hasn_id=owner_hasn_id)
    return response_base.success(data=data)


@router.put('/servers/{mcp_id}/status', summary='[Owner] 启用/停用 server', dependencies=[DependsJwtAuth])
async def set_server_status(request: Request, db: CurrentSession, obj: SetServerStatusParam, mcp_id: str = Path(...)) -> ResponseModel:
    owner_hasn_id = await _owner(db, request)
    data = await external_mcp_gateway.set_server_status(mcp_id=mcp_id, status=obj.status, owner_hasn_id=owner_hasn_id)
    return response_base.success(data=data)


@router.delete('/servers/{mcp_id}', summary='[Owner] 删除 server（连带凭据/绑定）', dependencies=[DependsJwtAuth])
async def delete_server(request: Request, db: CurrentSession, mcp_id: str = Path(...)) -> ResponseModel:
    owner_hasn_id = await _owner(db, request)
    ok = await external_mcp_gateway.delete_server(mcp_id=mcp_id, owner_hasn_id=owner_hasn_id)
    return response_base.success(data={'deleted': ok})


# ============================ Agent 绑定 ============================


@router.get('/bindings', summary='[Owner] 列我名下全部 Agent↔server 绑定', dependencies=[DependsJwtAuth])
async def list_bindings(request: Request, db: CurrentSession) -> ResponseModel:
    owner_hasn_id = await _owner(db, request)
    data = await external_mcp_gateway.list_bindings(owner_hasn_id=owner_hasn_id)
    return response_base.success(data=data)


@router.post('/bindings', summary='[Owner] 授权 Agent 可用某 server 的若干工具', dependencies=[DependsJwtAuth])
async def create_binding(request: Request, db: CurrentSession, obj: CreateBindingParam) -> ResponseModel:
    owner_hasn_id = await _owner(db, request)
    await _require_owned_agent(db, agent_hasn_id=obj.agent_hasn_id, owner_hasn_id=owner_hasn_id)
    # 行级隔离：只能绑自己的 server 或 system 共享 server。
    server = await external_mcp_gateway.get_server_detail(mcp_id=obj.mcp_id, owner_hasn_id=owner_hasn_id)
    if server is None:
        raise errors.NotFoundError(msg='server 不存在或无权绑定')
    data = await external_mcp_gateway.create_binding(
        agent_hasn_id=obj.agent_hasn_id,
        owner_hasn_id=owner_hasn_id,
        mcp_id=obj.mcp_id,
        allowed_raw_names=obj.allowed_raw_names,
    )
    return response_base.success(data=data)


@router.put('/bindings/enabled', summary='[Owner] 启用/停用某 Agent↔server 绑定', dependencies=[DependsJwtAuth])
async def set_binding_enabled(request: Request, db: CurrentSession, obj: SetBindingEnabledParam) -> ResponseModel:
    owner_hasn_id = await _owner(db, request)
    await _require_owned_agent(db, agent_hasn_id=obj.agent_hasn_id, owner_hasn_id=owner_hasn_id)
    ok = await external_mcp_gateway.set_binding_enabled(
        agent_hasn_id=obj.agent_hasn_id, mcp_id=obj.mcp_id, enabled=obj.enabled
    )
    return response_base.success(data={'updated': ok})


# ============================ 用量 ============================


@router.get('/usage', summary='[Owner] 我的第三方 MCP 调用用量', dependencies=[DependsJwtAuth])
async def usage_summary(request: Request, db: CurrentSession, mcp_id: str | None = None) -> ResponseModel:
    owner_hasn_id = await _owner(db, request)
    data = await quota_service.usage_summary(owner_hasn_id=owner_hasn_id, mcp_id=mcp_id)
    return response_base.success(data=data)
