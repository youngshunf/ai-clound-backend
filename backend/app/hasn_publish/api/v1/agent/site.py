"""通用网页发布与分享 - Agent API（publish 应用，设计 03 §1）。

路由前缀: /api/v1/publish/agent
认证方式: Agent JWT（身份取自 JWT claims，绝不读请求体身份）。
owner 隔离键 = ``agent.owner_hasn_id``；publisher_agent_id = ``agent.agent_hasn_id``。
scope 闸：publish:read / publish:write。
注：制品打包+上云在 daemon（execution_location=local），本端仅记录 site/revision 元数据。
"""

from datetime import datetime
from typing import Annotated

from fastapi import APIRouter, Query, Request
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from backend.app.hasn_publish.service.publish_service import publish_service
from backend.common.dataclasses import AgentTokenPayload
from backend.common.response.response_schema import ResponseModel, response_base
from backend.common.security.agent_capability import require_capability_not_denied
from backend.common.security.agent_jwt_auth import DependsAgentJwtAuth
from backend.database.db import CurrentSession, CurrentSessionTransaction

router = APIRouter()

_SCOPE_READ = 'publish:read'
_SCOPE_WRITE = 'publish:write'


async def _agent(request: Request, db: AsyncSession, *scopes: str) -> AgentTokenPayload:
    agent: AgentTokenPayload = request.state.agent
    for scope in scopes:
        await require_capability_not_denied(db, agent.agent_hasn_id, scope)
    return agent


class AgentCreateSiteRequest(BaseModel):
    kind: str = Field(default='page')
    # 分身必须给名字：无 default＝缺字段 422，min_length=1＝空串 422，纯空白由 service 的
    # normalize_title 拦。主人在「网页发布」里靠这个名字认出是哪个站，不能落成「未命名」。
    title: str = Field(min_length=1, max_length=200, description='展示标题（必填）')
    asset_id: str = Field(min_length=1, max_length=40)
    runtime: str = Field(default='single-html')
    content_hash: str = Field(default='', max_length=64)
    size_bytes: int = Field(default=0, ge=0)
    manifest_json: dict | None = Field(default=None)
    visibility: str = Field(default='private')
    password: str | None = Field(default=None)
    expires_at: datetime | None = Field(default=None)
    allow_present: bool = Field(default=True)
    allow_download: bool = Field(default=False)
    allow_indexing: bool = Field(default=False)
    source_app: str | None = Field(default=None, max_length=32)
    source_ref: str | None = Field(default=None, max_length=80)
    platform_project_id: str | None = Field(default=None, max_length=36)


class AgentUpdateSiteRequest(BaseModel):
    asset_id: str = Field(min_length=1, max_length=40)
    runtime: str = Field(default='single-html')
    content_hash: str = Field(default='', max_length=64)
    size_bytes: int = Field(default=0, ge=0)
    manifest_json: dict | None = Field(default=None)
    title: str | None = Field(default=None, max_length=200, description='可选：随内容更新一并改名')


class AgentRenameSiteRequest(BaseModel):
    title: str = Field(min_length=1, max_length=200, description='新展示标题（去空白后必须非空）')


class AgentSetVisibilityRequest(BaseModel):
    visibility: str | None = Field(default=None)
    password: str | None = Field(default=None)
    clear_password: bool = Field(default=False)
    expires_at: datetime | None = Field(default=None)
    clear_expires: bool = Field(default=False)
    allow_present: bool | None = Field(default=None)
    allow_download: bool | None = Field(default=None)
    allow_indexing: bool | None = Field(default=None)


@router.post('/sites', summary='Agent 发布', dependencies=[DependsAgentJwtAuth], name='publish_agent_create')
async def agent_create_site(
    request: Request, db: CurrentSessionTransaction, body: AgentCreateSiteRequest
) -> ResponseModel:
    agent = await _agent(request, db, _SCOPE_WRITE)
    data = await publish_service.create_site(
        db,
        owner_id=agent.owner_hasn_id,
        publisher_agent_id=agent.agent_hasn_id,
        kind=body.kind,
        # 纯空白标题 Pydantic 的 min_length 拦不住（长度够），这里过一遍归一：去空白后为空即 400。
        title=publish_service.normalize_title(body.title),
        asset_id=body.asset_id,
        runtime=body.runtime,
        content_hash=body.content_hash,
        size_bytes=body.size_bytes,
        manifest_json=body.manifest_json,
        visibility=body.visibility,
        password=body.password,
        expires_at=body.expires_at,
        allow_present=body.allow_present,
        allow_download=body.allow_download,
        allow_indexing=body.allow_indexing,
        source_app=body.source_app,
        source_ref=body.source_ref,
        platform_project_id=body.platform_project_id,
    )
    return response_base.success(data=data)


@router.get('/sites', summary='Agent 列我发布的', dependencies=[DependsAgentJwtAuth], name='publish_agent_list')
async def agent_list_sites(
    request: Request,
    db: CurrentSession,
    kind: Annotated[str | None, Query()] = None,
    page: Annotated[int, Query(ge=1)] = 1,
    size: Annotated[int, Query(ge=1, le=200)] = 50,
) -> ResponseModel:
    agent = await _agent(request, db, _SCOPE_READ)
    data = await publish_service.list_owned(
        db, owner_id=agent.owner_hasn_id, kind=kind, limit=size, offset=(page - 1) * size
    )
    return response_base.success(data=data)


@router.get('/sites/{site_id}', summary='Agent 取发布详情', dependencies=[DependsAgentJwtAuth], name='publish_agent_get')
async def agent_get_site(request: Request, db: CurrentSession, site_id: int) -> ResponseModel:
    agent = await _agent(request, db, _SCOPE_READ)
    data = await publish_service.get_owned(db, owner_id=agent.owner_hasn_id, site_id=site_id)
    return response_base.success(data={'site': data})


@router.put('/sites/{site_id}', summary='Agent 更新发布', dependencies=[DependsAgentJwtAuth], name='publish_agent_update')
async def agent_update_site(
    request: Request, db: CurrentSessionTransaction, site_id: int, body: AgentUpdateSiteRequest
) -> ResponseModel:
    agent = await _agent(request, db, _SCOPE_WRITE)
    data = await publish_service.update_site(
        db,
        owner_id=agent.owner_hasn_id,
        site_id=site_id,
        asset_id=body.asset_id,
        runtime=body.runtime,
        content_hash=body.content_hash,
        size_bytes=body.size_bytes,
        manifest_json=body.manifest_json,
        title=body.title,
    )
    return response_base.success(data=data)


@router.patch(
    '/sites/{site_id}/title',
    summary='Agent 改展示标题（纯元数据，不发新 revision）',
    dependencies=[DependsAgentJwtAuth],
    name='publish_agent_rename',
)
async def agent_rename_site(
    request: Request, db: CurrentSessionTransaction, site_id: int, body: AgentRenameSiteRequest
) -> ResponseModel:
    agent = await _agent(request, db, _SCOPE_WRITE)
    data = await publish_service.rename_site(
        db, owner_id=agent.owner_hasn_id, site_id=site_id, title=body.title
    )
    return response_base.success(data={'site': data})


@router.patch(
    '/sites/{site_id}/visibility',
    summary='Agent 改可见性',
    dependencies=[DependsAgentJwtAuth],
    name='publish_agent_set_visibility',
)
async def agent_set_visibility(
    request: Request, db: CurrentSessionTransaction, site_id: int, body: AgentSetVisibilityRequest
) -> ResponseModel:
    agent = await _agent(request, db, _SCOPE_WRITE)
    data = await publish_service.set_visibility(
        db,
        owner_id=agent.owner_hasn_id,
        site_id=site_id,
        visibility=body.visibility,
        password=body.password,
        clear_password=body.clear_password,
        expires_at=body.expires_at,
        clear_expires=body.clear_expires,
        allow_present=body.allow_present,
        allow_download=body.allow_download,
        allow_indexing=body.allow_indexing,
    )
    return response_base.success(data={'site': data})


@router.post(
    '/sites/{site_id}/revoke', summary='Agent 撤销发布', dependencies=[DependsAgentJwtAuth], name='publish_agent_revoke'
)
async def agent_revoke_site(request: Request, db: CurrentSessionTransaction, site_id: int) -> ResponseModel:
    agent = await _agent(request, db, _SCOPE_WRITE)
    data = await publish_service.revoke(db, owner_id=agent.owner_hasn_id, site_id=site_id)
    return response_base.success(data={'site': data})


@router.delete(
    '/sites/{site_id}', summary='Agent 删除发布', dependencies=[DependsAgentJwtAuth], name='publish_agent_delete'
)
async def agent_delete_site(request: Request, db: CurrentSessionTransaction, site_id: int) -> ResponseModel:
    agent = await _agent(request, db, _SCOPE_WRITE)
    await publish_service.delete_site(db, owner_id=agent.owner_hasn_id, site_id=site_id)
    return response_base.success(data={'deleted': True})
