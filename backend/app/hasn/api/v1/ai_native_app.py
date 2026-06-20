from __future__ import annotations

from typing import Any

import sqlalchemy as sa

from fastapi import APIRouter, Depends, Request

from backend.app.hasn.model import HasnHumans
from backend.app.hasn.schema.ai_native_audit import CreateAiNativeAppAuditParam, ReportAiNativeAppAuditParam
from backend.app.hasn.schema.ai_native_runtime import (
    AiNativeAuditQuery,
    AiNativeRuntimeCapabilitiesRequest,
    AiNativeToolCallRequest,
)
from backend.app.hasn.service.ai_native_app_registry import ai_native_app_registry
from backend.app.hasn.service.ai_native_audit_service import ai_native_audit_service
from backend.app.hasn.service.ai_native_runtime_gateway import ai_native_runtime_gateway
from backend.app.hasn.service.app_instance_service import app_instance_service
from backend.app.hasn.service.workbench_domain_service import workbench_domain_service
from backend.common.dataclasses import AgentTokenPayload
from backend.common.exception import errors
from backend.common.response.response_schema import ResponseModel, response_base
from backend.common.security.agent_jwt_auth import DependsAgentJwtAuth
from backend.common.security.jwt import DependsJwtAuth
from backend.database.db import CurrentSession, CurrentSessionTransaction

apps_router = APIRouter()
runtime_router = APIRouter()
audit_router = APIRouter()


async def _require_owner_hasn_id(db: CurrentSession, request: Request) -> str:
    """从已认证 Owner JWT 解析 developer_id（= 登录人 hasn_id）。"""
    user_id = getattr(getattr(request, 'user', None), 'id', None)
    if user_id is None:
        raise errors.TokenError(msg='owner_jwt_required')
    row = (await db.execute(sa.select(HasnHumans).where(HasnHumans.user_id == int(user_id)))).scalars().first()
    if row is None:
        raise errors.NotFoundError(msg='owner_identity_not_found')
    return row.hasn_id


@apps_router.get('', summary='AI-Native 应用清单')
async def list_ai_native_apps(db: CurrentSession) -> ResponseModel:
    return response_base.success(data=await ai_native_app_registry.list_published_manifests(db))


@apps_router.get('/{app_id}', summary='AI-Native 应用详情')
async def get_ai_native_app(db: CurrentSession, app_id: str) -> ResponseModel:
    return response_base.success(data=await ai_native_app_registry.get(db, app_id))


@apps_router.post('/{app_id}/validate', summary='AI-Native 应用校验')
async def validate_ai_native_app(db: CurrentSession, app_id: str, body: dict[str, Any]) -> ResponseModel:
    result = ai_native_app_registry.validate_manifest(body)
    return response_base.success(
        data={
            'app_id': app_id,
            'valid': result.valid,
            'errors': result.errors,
            'manifest_hash': result.manifest_hash,
        }
    )


@apps_router.post('/{app_id}/publish', summary='AI-Native 应用发布')
async def publish_ai_native_app(db: CurrentSessionTransaction, app_id: str) -> ResponseModel:
    return response_base.success(data=await ai_native_app_registry.publish_builtin(db, app_id))


@apps_router.post(
    '/{app_id}/register', summary='AI-Native 应用注册（第三方注册即接入）', dependencies=[DependsJwtAuth]
)
async def register_ai_native_app(
    request: Request, db: CurrentSessionTransaction, app_id: str, body: dict[str, Any]
) -> ResponseModel:
    """第三方注册：绑定 developer_id↔app_id 所有权 + 建公共实例 + 发布 manifest（设计 11 §5.1/§5.2）。"""
    developer_id = await _require_owner_hasn_id(db, request)
    data = await app_instance_service.register_app(
        db,
        developer_id=developer_id,
        app_id=app_id,
        manifest_json=dict(body.get('manifest') or {}),
        endpoint=str(body.get('endpoint') or ''),
        credential=str(body.get('credential') or ''),
        publisher_type=str(body.get('publisher_type') or 'third_party'),
    )
    return response_base.success(data=data)


@apps_router.put(
    '/{app_id}/enterprise-instance', summary='企业自建实例（override 公共）', dependencies=[DependsJwtAuth]
)
async def configure_enterprise_instance(
    request: Request, db: CurrentSessionTransaction, app_id: str, body: dict[str, Any]
) -> ResponseModel:
    """企业自建实例：仅企业 owner/admin 可配置；该企业 workspace 下 resolve() 自动解析到企业实例（设计 11 §5.3）。"""
    owner_user_id = getattr(getattr(request, 'user', None), 'id', None)
    enterprise_id = body.get('enterprise_id')
    if owner_user_id is None:
        raise errors.TokenError(msg='owner_jwt_required')
    if enterprise_id is None:
        raise errors.RequestError(msg='enterprise_id_required')
    membership = await workbench_domain_service._approved_membership(
        db, enterprise_id=int(enterprise_id), user_id=int(owner_user_id)
    )
    role = getattr(membership, 'role', None) if membership is not None else None
    if role not in ('owner', 'admin'):
        raise errors.ForbiddenError(msg='enterprise_admin_required')
    data = await app_instance_service.configure_enterprise_instance(
        db,
        app_id=app_id,
        enterprise_id=int(enterprise_id),
        endpoint=str(body.get('endpoint') or ''),
        credential=str(body.get('credential') or ''),
        config=dict(body.get('config') or {}),
    )
    return response_base.success(data=data)


@runtime_router.post('/capabilities', summary='AI-Native 能力发现', dependencies=[DependsAgentJwtAuth])
async def runtime_capabilities(
    request: Request, db: CurrentSession, body: AiNativeRuntimeCapabilitiesRequest
) -> ResponseModel:
    data = await ai_native_runtime_gateway.get_capabilities(db=db, request=request, body=body)
    return response_base.success(data=data)


@runtime_router.post(
    '/tools/{app_id}/{tool_id}/call',
    summary='AI-Native Tool 调用',
    dependencies=[DependsAgentJwtAuth],
)
async def runtime_tool_call(
    request: Request,
    db: CurrentSessionTransaction,
    app_id: str,
    tool_id: str,
    body: AiNativeToolCallRequest,
) -> ResponseModel:
    data = await ai_native_runtime_gateway.call_tool(db=db, request=request, app_id=app_id, tool_id=tool_id, body=body)
    return response_base.success(data=data)


@audit_router.get('', summary='AI-Native 审计', dependencies=[DependsJwtAuth])
async def list_ai_native_audit(
    request: Request, db: CurrentSession, query: AiNativeAuditQuery = Depends()
) -> ResponseModel:
    """Owner 视角的 AI-Native 工具调用审计列表。

    owner 鉴权（`DependsJwtAuth`）+ 服务端按 `owner_hasn_id` 硬过滤：只返回当前登录人名下分身的
    工具调用审计；`agent_hasn_id` 仅作进一步收窄（传他人分身 ID 也被 owner 过滤兜底，读不到别人的）。
    """
    owner_hasn_id = await _require_owner_hasn_id(db, request)
    return response_base.success(
        data=await ai_native_runtime_gateway.list_audit(db=db, query=query, owner_hasn_id=owner_hasn_id)
    )


@audit_router.post('/report', summary='AI-Native 工具调用审计上报（Agent 自报本地工具，D5）')
async def report_ai_native_audit(
    db: CurrentSessionTransaction,
    body: ReportAiNativeAppAuditParam,
    agent: AgentTokenPayload = DependsAgentJwtAuth,
) -> ResponseModel:
    """分身经 `for_agent` 上报「本地执行工具」（如 hasn.deck.*）的调用审计。

    身份权威：`agent_hasn_id`/`owner_hasn_id` 取自 **Agent JWT**（不取 body，identity by auth），
    `actor_type` 强制 `agent`。本地审计先在分身侧产生，上报为 best-effort（失败可补传）；
    此端点只负责落库到 `hasn_ai_native_app_audit`（D5 新建链路，与云端 Runtime 路由审计共表）。
    """
    obj = CreateAiNativeAppAuditParam(
        **body.model_dump(),
        actor_type='agent',
        agent_hasn_id=agent.agent_hasn_id,
        owner_hasn_id=agent.owner_hasn_id,
    )
    await ai_native_audit_service.create(db=db, obj=obj)
    return response_base.success(data={'trace_id': body.trace_id, 'recorded': True})
