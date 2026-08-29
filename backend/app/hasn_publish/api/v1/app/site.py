"""通用网页发布与分享 用户端 API。

路由前缀: /api/v1/publish/app
认证方式: Owner JWT（owner hasn_id 由登录用户解析），owner 隔离。
调用方: daemon PublishBroker（WebUI/owner 发布）——制品已由 daemon 打包并经 AssetGateway 上云，
        本端只记录 site/revision 元数据（asset_id 指向 public.hasn_assets）。
设计事实源：docs/hasn-node设计文档/18-通用网页发布与分享/03-工具与接口.md §2。
"""

from datetime import datetime
from typing import Annotated

from fastapi import APIRouter, Query, Request
from pydantic import BaseModel, Field

from backend.app.hasn_core import identity
from backend.app.hasn_publish.service.publish_service import publish_service
from backend.common.exception import errors
from backend.common.response.response_schema import ResponseModel, response_base
from backend.common.security.jwt import DependsJwtAuth
from backend.database.db import CurrentSession, CurrentSessionTransaction

router = APIRouter()


class CreateSiteRequest(BaseModel):
    kind: str = Field(default='page', description='制品类型 deck/report/page/dashboard/other')
    title: str = Field(default='', max_length=200, description='展示标题')
    asset_id: str = Field(min_length=1, max_length=40, description='制品在 hasn_assets 的 id（私有桶）')
    runtime: str = Field(default='single-html', description='single-html / bundle-zip')
    content_hash: str = Field(default='', max_length=64, description='制品内容哈希（去重/幂等）')
    size_bytes: int = Field(default=0, ge=0, description='制品大小')
    manifest_json: dict | None = Field(default=None, description='bundle-zip 子文件清单')
    visibility: str = Field(default='private', description='private/password/unlisted/public')
    password: str | None = Field(default=None, description='visibility=password 时的口令明文')
    expires_at: datetime | None = Field(default=None, description='过期时间')
    allow_present: bool = Field(default=True)
    allow_download: bool = Field(default=False)
    allow_indexing: bool = Field(default=False)
    source_app: str | None = Field(default=None, max_length=32)
    source_ref: str | None = Field(default=None, max_length=80)
    platform_project_id: str | None = Field(default=None, max_length=36)
    publisher_agent_id: str | None = Field(default=None, max_length=40, description='若由 agent 代发布')


class UpdateSiteRequest(BaseModel):
    asset_id: str = Field(min_length=1, max_length=40)
    runtime: str = Field(default='single-html')
    content_hash: str = Field(default='', max_length=64)
    size_bytes: int = Field(default=0, ge=0)
    manifest_json: dict | None = Field(default=None)
    title: str | None = Field(
        default=None, max_length=200, description='可选：随内容更新一并改名；不传＝保留原名'
    )


class RenameSiteRequest(BaseModel):
    title: str = Field(min_length=1, max_length=200, description='新展示标题（去空白后必须非空）')


class SetVisibilityRequest(BaseModel):
    visibility: str | None = Field(default=None, description='private/password/unlisted/public')
    password: str | None = Field(default=None)
    clear_password: bool = Field(default=False)
    expires_at: datetime | None = Field(default=None)
    clear_expires: bool = Field(default=False)
    allow_present: bool | None = Field(default=None)
    allow_download: bool | None = Field(default=None)
    allow_indexing: bool | None = Field(default=None)


async def _resolve_owner(db: CurrentSession, request: Request) -> str:
    owner_id = getattr(request.user, 'hasn_id', None)
    if owner_id:
        return owner_id
    human = await identity.get_human_by_user_id(db, user_id=request.user.id)
    if not human:
        raise errors.NotFoundError(msg='用户 HASN 身份不存在')
    return human.hasn_id


@router.post('/sites', summary='创建发布（记录 site/revision 元数据）', dependencies=[DependsJwtAuth])
async def create_site(request: Request, db: CurrentSessionTransaction, body: CreateSiteRequest) -> ResponseModel:
    owner_id = await _resolve_owner(db, request)
    data = await publish_service.create_site(
        db,
        owner_id=owner_id,
        publisher_agent_id=body.publisher_agent_id,
        kind=body.kind,
        title=body.title,
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


@router.get('/sites', summary='列出我发布的', dependencies=[DependsJwtAuth])
async def list_sites(
    request: Request,
    db: CurrentSession,
    kind: Annotated[str | None, Query()] = None,
    page: Annotated[int, Query(ge=1)] = 1,
    size: Annotated[int, Query(ge=1, le=200)] = 50,
) -> ResponseModel:
    owner_id = await _resolve_owner(db, request)
    data = await publish_service.list_owned(db, owner_id=owner_id, kind=kind, limit=size, offset=(page - 1) * size)
    return response_base.success(data=data)


@router.get('/sites/by-source', summary='按来源反查 site（deck 更新用）', dependencies=[DependsJwtAuth])
async def get_by_source(
    request: Request,
    db: CurrentSession,
    source_app: Annotated[str, Query(min_length=1)],
    source_ref: Annotated[str, Query(min_length=1)],
) -> ResponseModel:
    owner_id = await _resolve_owner(db, request)
    data = await publish_service.get_by_source(db, owner_id=owner_id, source_app=source_app, source_ref=source_ref)
    return response_base.success(data={'site': data})


@router.get('/sites/{site_id}', summary='发布详情', dependencies=[DependsJwtAuth])
async def get_site(request: Request, db: CurrentSession, site_id: int) -> ResponseModel:
    owner_id = await _resolve_owner(db, request)
    # data = {site, latest_revision}：latest_revision.materialize_status 是异步物化的轮询面
    data = await publish_service.get_owned(db, owner_id=owner_id, site_id=site_id)
    return response_base.success(data=data)


@router.put('/sites/{site_id}', summary='更新发布（新 revision，URL 不变）', dependencies=[DependsJwtAuth])
async def update_site(
    request: Request, db: CurrentSessionTransaction, site_id: int, body: UpdateSiteRequest
) -> ResponseModel:
    owner_id = await _resolve_owner(db, request)
    data = await publish_service.update_site(
        db,
        owner_id=owner_id,
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
    summary='改展示标题（纯元数据，不发新 revision）',
    dependencies=[DependsJwtAuth],
    name='publish_app_rename',
)
async def rename_site(
    request: Request, db: CurrentSessionTransaction, site_id: int, body: RenameSiteRequest
) -> ResponseModel:
    owner_id = await _resolve_owner(db, request)
    data = await publish_service.rename_site(db, owner_id=owner_id, site_id=site_id, title=body.title)
    return response_base.success(data={'site': data})


@router.patch(
    '/sites/{site_id}/visibility',
    summary='改可见性/口令/过期/能力',
    dependencies=[DependsJwtAuth],
    name='publish_app_set_visibility',
)
async def set_visibility(
    request: Request, db: CurrentSessionTransaction, site_id: int, body: SetVisibilityRequest
) -> ResponseModel:
    owner_id = await _resolve_owner(db, request)
    data = await publish_service.set_visibility(
        db,
        owner_id=owner_id,
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


@router.post('/sites/{site_id}/revoke', summary='撤销（URL 返回 410）', dependencies=[DependsJwtAuth])
async def revoke_site(request: Request, db: CurrentSessionTransaction, site_id: int) -> ResponseModel:
    owner_id = await _resolve_owner(db, request)
    data = await publish_service.revoke(db, owner_id=owner_id, site_id=site_id)
    return response_base.success(data={'site': data})


@router.delete('/sites/{site_id}', summary='删除（软删）', dependencies=[DependsJwtAuth])
async def delete_site(request: Request, db: CurrentSessionTransaction, site_id: int) -> ResponseModel:
    owner_id = await _resolve_owner(db, request)
    await publish_service.delete_site(db, owner_id=owner_id, site_id=site_id)
    return response_base.success(data={'deleted': True})


@router.post('/sites/{site_id}/view-ticket', summary='签发 private 浏览器短时访问票', dependencies=[DependsJwtAuth])
async def issue_view_ticket(request: Request, db: CurrentSession, site_id: int) -> ResponseModel:
    owner_id = await _resolve_owner(db, request)
    # owner 闸 + 存在性
    await publish_service.get_owned(db, owner_id=owner_id, site_id=site_id)
    data = publish_service.issue_view_ticket(site_id=site_id, owner_id=owner_id)
    return response_base.success(data=data)


@router.post(
    '/sites/by-slug/{slug}/view-ticket',
    summary='按 slug 签发 private 访问票（website /s/{slug} 查看器登录后换票）',
    dependencies=[DependsJwtAuth],
)
async def issue_view_ticket_by_slug(request: Request, db: CurrentSession, slug: str) -> ResponseModel:
    """website 查看器：owner 本人登录后按 slug 换 private 访问票（浏览器无 site_id，只有 slug）。

    owner 隔离——仅发布者本人可换自己 site 的票；非 owner / 已撤销一律按「不存在」拒绝，
    不泄露他人 slug 的归属（防 owner 枚举）。private=仅发布者本人（要给别人看请改可见性）。
    """
    owner_id = await _resolve_owner(db, request)
    site = await publish_service.get_site_by_slug(db, slug=slug)
    if site is None or site.owner_id != owner_id or site.status == 'revoked':
        raise errors.NotFoundError(msg='发布不存在')
    data = publish_service.issue_view_ticket(site_id=site.id, owner_id=owner_id)
    return response_base.success(data=data)
