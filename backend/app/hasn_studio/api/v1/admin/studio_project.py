from typing import Annotated

from fastapi import APIRouter, Depends, Path

from backend.app.hasn_studio.schema.studio_project import (
    CreateStudioProjectParam,
    DeleteStudioProjectParam,
    GetStudioProjectDetail,
    UpdateStudioProjectParam,
)
from backend.app.hasn_studio.service.studio_project_service import studio_project_service
from backend.common.pagination import DependsPagination, PageData
from backend.common.response.response_schema import ResponseModel, ResponseSchemaModel, response_base
from backend.common.security.jwt import DependsJwtAuth
from backend.common.security.permission import RequestPermission
from backend.common.security.rbac import DependsRBAC
from backend.database.db import CurrentSession, CurrentSessionTransaction

router = APIRouter()


@router.get('/{pk}', summary='获取视频项目（统一视频引擎 studio：管线/素材/成品的容器）详情', dependencies=[DependsJwtAuth], name='hasn_studio_admin_get_studio_project')
async def get_studio_project(
    db: CurrentSession, pk: Annotated[int, Path(description='视频项目（统一视频引擎 studio：管线/素材/成品的容器） ID')]
) -> ResponseSchemaModel[GetStudioProjectDetail]:
    studio_project = await studio_project_service.get(db=db, pk=pk)
    return response_base.success(data=studio_project)


@router.get(
    '',
    summary='分页获取所有视频项目（统一视频引擎 studio：管线/素材/成品的容器）',
    dependencies=[
        DependsJwtAuth,
        DependsPagination,
    ],
    name='hasn_studio_admin_get_studio_project_paginated',
)
async def get_studio_project_paginated(db: CurrentSession) -> ResponseSchemaModel[PageData[GetStudioProjectDetail]]:
    page_data = await studio_project_service.get_list(db=db)
    return response_base.success(data=page_data)


@router.post(
    '',
    summary='创建视频项目（统一视频引擎 studio：管线/素材/成品的容器）',
    dependencies=[
        Depends(RequestPermission('studio:project:add')),
        DependsRBAC,
    ],
    name='hasn_studio_admin_create_studio_project',
)
async def create_studio_project(db: CurrentSessionTransaction, obj: CreateStudioProjectParam) -> ResponseModel:
    await studio_project_service.create(db=db, obj=obj)
    return response_base.success()


@router.put(
    '/{pk}',
    summary='更新视频项目（统一视频引擎 studio：管线/素材/成品的容器）',
    dependencies=[
        Depends(RequestPermission('studio:project:edit')),
        DependsRBAC,
    ],
    name='hasn_studio_admin_update_studio_project',
)
async def update_studio_project(
    db: CurrentSessionTransaction, pk: Annotated[int, Path(description='视频项目（统一视频引擎 studio：管线/素材/成品的容器） ID')], obj: UpdateStudioProjectParam
) -> ResponseModel:
    count = await studio_project_service.update(db=db, pk=pk, obj=obj)
    if count > 0:
        return response_base.success()
    return response_base.fail()


@router.delete(
    '',
    summary='批量删除视频项目（统一视频引擎 studio：管线/素材/成品的容器）',
    dependencies=[
        Depends(RequestPermission('studio:project:del')),
        DependsRBAC,
    ],
    name='hasn_studio_admin_delete_studio_project',
)
async def delete_studio_project(db: CurrentSessionTransaction, obj: DeleteStudioProjectParam) -> ResponseModel:
    count = await studio_project_service.delete(db=db, obj=obj)
    if count > 0:
        return response_base.success()
    return response_base.fail()
