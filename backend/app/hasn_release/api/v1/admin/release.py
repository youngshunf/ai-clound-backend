"""桌面端发布 - 管理端 API（JWT + RBAC）。

管理端能力（设计 §7 管理页）：
  - 手动上传新包发布（预上传七牛后回传元数据 + .sig）
  - 从 GitHub 自动构建（触发 workflow_dispatch + 轮询构建状态）
  - 版本管理：列表/详情/编辑 changelog/状态、置为最新（回滚）、删除
"""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, Path, Query, Request

from backend.app.hasn_release.schema.release import (
    BuildDetail,
    GithubBuildRequest,
    PublishReleaseRequest,
    ReleaseDetail,
    SetLatestRequest,
    UpdateReleaseMetaRequest,
)
from backend.app.hasn_release.service.release_service import release_service
from backend.common.response.response_schema import ResponseModel, ResponseSchemaModel, response_base
from backend.common.security.jwt import DependsJwtAuth
from backend.common.security.permission import RequestPermission
from backend.common.security.rbac import DependsRBAC
from backend.database.db import CurrentSession, CurrentSessionTransaction

router = APIRouter()


def _actor(request: Request) -> str:
    user = getattr(request, 'user', None)
    return getattr(user, 'username', None) or getattr(user, 'hasn_id', None) or 'admin'


@router.post(
    '/publish',
    summary='手动上传发布新版本（预上传七牛后回传元数据）',
    dependencies=[Depends(RequestPermission('release:publish')), DependsRBAC],
)
async def publish_release(
    request: Request, db: CurrentSessionTransaction, obj: PublishReleaseRequest
) -> ResponseSchemaModel[ReleaseDetail]:
    data = await release_service.publish(db, obj, source='manual')
    return response_base.success(data=data)


@router.get(
    '/list',
    summary='版本列表',
    dependencies=[DependsJwtAuth],
)
async def list_releases(
    db: CurrentSession,
    channel: Annotated[str | None, Query(description='stable/beta，空=全部')] = None,
    limit: Annotated[int, Query(ge=1, le=100)] = 50,
) -> ResponseSchemaModel[list[ReleaseDetail]]:
    data = await release_service.list_releases(db, channel=channel, limit=limit)
    return response_base.success(data=data)


@router.get(
    '/{pk}',
    summary='版本详情（含资产）',
    dependencies=[DependsJwtAuth],
)
async def get_release(
    db: CurrentSession, pk: Annotated[int, Path(description='版本 id')]
) -> ResponseSchemaModel[ReleaseDetail]:
    data = await release_service.get_detail(db, pk)
    return response_base.success(data=data)


@router.put(
    '/{pk}',
    summary='编辑版本元数据（changelog / 状态）',
    dependencies=[Depends(RequestPermission('release:edit')), DependsRBAC],
)
async def update_release(
    db: CurrentSessionTransaction, pk: Annotated[int, Path(description='版本 id')], obj: UpdateReleaseMetaRequest
) -> ResponseSchemaModel[ReleaseDetail]:
    data = await release_service.update_meta(db, pk, obj)
    return response_base.success(data=data)


@router.post(
    '/{pk}/set-latest',
    summary='置为当前 channel 最新（回滚 / 手动切换）',
    dependencies=[Depends(RequestPermission('release:edit')), DependsRBAC],
)
async def set_latest(
    db: CurrentSessionTransaction,
    pk: Annotated[int, Path(description='版本 id')],
    obj: SetLatestRequest,
) -> ResponseSchemaModel[ReleaseDetail]:
    data = await release_service.set_latest(db, pk, channel=obj.channel)
    return response_base.success(data=data)


@router.delete(
    '/{pk}',
    summary='删除版本（级联删资产）',
    dependencies=[Depends(RequestPermission('release:del')), DependsRBAC],
)
async def delete_release(
    db: CurrentSessionTransaction, pk: Annotated[int, Path(description='版本 id')]
) -> ResponseModel:
    await release_service.delete(db, pk)
    return response_base.success()


# --------- CI 构建任务 ---------


@router.post(
    '/builds/github',
    summary='从 GitHub 自动构建（触发 workflow_dispatch）',
    dependencies=[Depends(RequestPermission('release:build')), DependsRBAC],
)
async def trigger_github_build(
    request: Request, db: CurrentSessionTransaction, obj: GithubBuildRequest
) -> ResponseSchemaModel[BuildDetail]:
    data = await release_service.trigger_github_build(db, obj, actor=_actor(request))
    return response_base.success(data=data)


@router.get(
    '/builds/list',
    summary='构建任务列表（GitHub Actions 进度）',
    dependencies=[DependsJwtAuth],
)
async def list_builds(
    db: CurrentSession, limit: Annotated[int, Query(ge=1, le=100)] = 50
) -> ResponseSchemaModel[list[BuildDetail]]:
    data = await release_service.list_builds(db, limit=limit)
    return response_base.success(data=data)
