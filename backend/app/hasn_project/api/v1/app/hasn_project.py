"""平台项目（doc38）用户端 API —— owner 隔离，由真正的 ``ProjectService`` + 挂靠点注册表支撑。

路由前缀: /api/v1/project/app/projects（router.py 把本 router include 到 /projects）。
认证: Owner JWT（``DependsJwtAuth``）；owner hasn_id 由 ``request.user.id`` 解析，绝不读请求体身份。
定位: hasn-node daemon 以 Owner JWT 回源这些端点做 local_first 镜像，WebUI 只调 daemon。
与 agent 平台工具 ``hasn.project.*``（backend/app/mcp/tools/project.py）共用同一 ``ProjectService``，
仅身份来源不同（此处 Owner JWT，工具面 Agent 凭证）。

注意（codegen 修正）：本文件原是 codegen 样板（int pk / user_id / 泛型 ``hasn_project_service``），
与本应用的 UUID 主键 + owner_hasn_id 身份模型不兼容，已整体改写为 ProjectService 支撑；
codegen 生成的 admin/agent/open 面继续用泛型 service，互不影响。
"""

from typing import Annotated, Any

from fastapi import APIRouter, Body, Path, Query, Request

from backend.app.hasn_project.api.v1.app._common import bump_project_sync, resolve_owner
from backend.app.hasn_project.service.project_app_service import project_service
from backend.app.hasn_project.service.project_linkage_registry import project_linkage_registry
from backend.common.response.response_schema import ResponseModel, response_base
from backend.common.security.jwt import DependsJwtAuth
from backend.database.db import CurrentSession, CurrentSessionTransaction

router = APIRouter()


@router.get('', summary='我的项目列表', dependencies=[DependsJwtAuth], name='project_app_list_projects')
async def app_list_projects(
    request: Request, db: CurrentSession, status: Annotated[str | None, Query()] = None
) -> ResponseModel:
    """列主人名下项目（active 在前、新建在前）。可选 status 过滤 active/archived。"""
    owner = await resolve_owner(db, request)
    rows = await project_service.list_projects(db, owner=owner, status=status)
    return response_base.success(data={'items': rows})


@router.post('', summary='创建项目', dependencies=[DependsJwtAuth], name='project_app_create_project')
async def app_create_project(
    request: Request, db: CurrentSessionTransaction, body: Annotated[dict[str, Any], Body()]
) -> ResponseModel:
    """建项目（name 必填；可选 goal/cover_asset_uri/bound_agent_id）。"""
    owner = await resolve_owner(db, request)
    data = await project_service.create_project(db, owner=owner, data=body)
    await bump_project_sync(db, owner)
    return response_base.success(data=data)


@router.get(
    '/{pk}',
    summary='项目详情（含里程碑轨 + 产物流并集读）',
    dependencies=[DependsJwtAuth],
    name='project_app_get_project',
)
async def app_get_project(request: Request, db: CurrentSession, pk: Annotated[str, Path()]) -> ResponseModel:
    """取项目详情：基本信息 + 里程碑轨(milestones) + 产物流并集读(artifact_flow)。owner 隔离由 service 兜。"""
    owner = await resolve_owner(db, request)
    detail = await project_service.get_project(db, owner=owner, pk=pk)
    detail['artifact_flow'] = await project_service.project_artifact_flow(db, owner=owner, project_id=pk)
    return response_base.success(data=detail)


@router.put('/{pk}', summary='更新项目', dependencies=[DependsJwtAuth], name='project_app_update_project')
async def app_update_project(
    request: Request,
    db: CurrentSessionTransaction,
    pk: Annotated[str, Path()],
    body: Annotated[dict[str, Any], Body()],
) -> ResponseModel:
    """改项目（name/goal/cover_asset_uri/bound_agent_id/status 局部更新；空 patch 返回原值）。"""
    owner = await resolve_owner(db, request)
    data = await project_service.update_project(db, owner=owner, pk=pk, data=body)
    await bump_project_sync(db, owner)
    return response_base.success(data=data)


@router.post('/{pk}/archive', summary='归档项目', dependencies=[DependsJwtAuth], name='project_app_archive_project')
async def app_archive_project(
    request: Request, db: CurrentSessionTransaction, pk: Annotated[str, Path()]
) -> ResponseModel:
    """归档项目（status→archived）。只改状态、不删数据、不断挂靠（项目非权限边界）。"""
    owner = await resolve_owner(db, request)
    data = await project_service.archive_project(db, owner=owner, pk=pk)
    await bump_project_sync(db, owner)
    return response_base.success(data=data)


@router.post(
    '/{pk}/restore',
    summary='恢复项目（archived→active）',
    dependencies=[DependsJwtAuth],
    name='project_app_restore_project',
)
async def app_restore_project(
    request: Request, db: CurrentSessionTransaction, pk: Annotated[str, Path()]
) -> ResponseModel:
    """恢复归档项目（status→active）。"""
    owner = await resolve_owner(db, request)
    data = await project_service.update_project(db, owner=owner, pk=pk, data={'status': 'active'})
    await bump_project_sync(db, owner)
    return response_base.success(data=data)


@router.get(
    '/{pk}/artifact-flow',
    summary='项目产物流（并集读：直接命中 ∪ 挂靠容器名下产物）',
    dependencies=[DependsJwtAuth],
    name='project_app_artifact_flow',
)
async def app_project_artifact_flow(
    request: Request, db: CurrentSession, pk: Annotated[str, Path()]
) -> ResponseModel:
    """产物流并集读：``project_id`` 直接命中 ∪ 挂靠容器名下产物（读时派生不回填）。"""
    owner = await resolve_owner(db, request)
    rows = await project_service.project_artifact_flow(db, owner=owner, project_id=pk)
    return response_base.success(data={'items': rows})


@router.post(
    '/{pk}/milestones', summary='在项目下建里程碑', dependencies=[DependsJwtAuth], name='project_app_create_milestone'
)
async def app_create_milestone(
    request: Request,
    db: CurrentSessionTransaction,
    pk: Annotated[str, Path()],
    body: Annotated[dict[str, Any], Body()],
) -> ResponseModel:
    """在项目下建里程碑（name 必填；纯业务状态标记，无依赖无门控）。先校验项目归属。"""
    owner = await resolve_owner(db, request)
    data = await project_service.create_milestone(db, owner=owner, project_id=pk, data=body)
    await bump_project_sync(db, owner)
    return response_base.success(data=data)


@router.post('/{pk}/link', summary='挂靠资源进项目', dependencies=[DependsJwtAuth], name='project_app_link_resource')
async def app_link_resource(
    request: Request,
    db: CurrentSessionTransaction,
    pk: Annotated[str, Path()],
    body: Annotated[dict[str, Any], Body()],
) -> ResponseModel:
    """把 ``resource_uri`` 指向的资源挂靠进项目。先校验目标项目归属（不写他人项目），
    再由挂靠点注册表落挂靠列（唯一收口，绝不散写跨 schema UPDATE）。"""
    owner = await resolve_owner(db, request)
    await project_service.assert_owned(db, owner=owner, pk=pk)
    result = await project_linkage_registry.link(
        db, owner=owner, resource_uri=str(body.get('resource_uri') or ''), project_id=pk
    )
    await bump_project_sync(db, owner)
    return response_base.success(data=result)


@router.post(
    '/{pk}/unlink',
    summary='从项目摘出资源',
    dependencies=[DependsJwtAuth],
    name='project_app_unlink_resource',
)
async def app_unlink_resource(
    request: Request,
    db: CurrentSessionTransaction,
    pk: Annotated[str, Path()],
    body: Annotated[dict[str, Any], Body()],
) -> ResponseModel:
    """把 ``resource_uri`` 指向的资源从项目摘出（挂靠列置 NULL）。资源 owner 隔离由注册表 adapter 兜。"""
    owner = await resolve_owner(db, request)
    result = await project_linkage_registry.unlink(db, owner=owner, resource_uri=str(body.get('resource_uri') or ''))
    await bump_project_sync(db, owner)
    return response_base.success(data=result)
