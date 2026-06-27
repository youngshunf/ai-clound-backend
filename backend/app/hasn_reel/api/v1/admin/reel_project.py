from typing import Annotated

from fastapi import APIRouter, Depends, Path, Query

from backend.app.hasn_reel.schema.reel_project import (
    CreateReelProjectParam,
    DeleteReelProjectParam,
    GetReelProjectDetail,
    UpdateReelProjectParam,
)
from backend.app.hasn_reel.service.reel_project_service import reel_project_service
from backend.common.pagination import DependsPagination, PageData
from backend.common.response.response_schema import ResponseModel, ResponseSchemaModel, response_base
from backend.common.security.jwt import DependsJwtAuth
from backend.common.security.permission import RequestPermission
from backend.common.security.rbac import DependsRBAC
from backend.database.db import CurrentSession, CurrentSessionTransaction

router = APIRouter()


@router.get('/{pk}', summary='获取短视频项目（reel：一组创作的容器 + 默认创作参数）详情', dependencies=[DependsJwtAuth], name='hasn_reel_admin_get_reel_project')
async def get_reel_project(
    db: CurrentSession, pk: Annotated[int, Path(description='短视频项目（reel：一组创作的容器 + 默认创作参数） ID')]
) -> ResponseSchemaModel[GetReelProjectDetail]:
    reel_project = await reel_project_service.get(db=db, pk=pk)
    return response_base.success(data=reel_project)


@router.get(
    '',
    summary='分页获取所有短视频项目（reel：一组创作的容器 + 默认创作参数）',
    dependencies=[
        DependsJwtAuth,
        DependsPagination,
    ],
    name='hasn_reel_admin_get_reel_project_paginated',
)
async def get_reel_project_paginated(db: CurrentSession) -> ResponseSchemaModel[PageData[GetReelProjectDetail]]:
    page_data = await reel_project_service.get_list(db=db)
    return response_base.success(data=page_data)


@router.post(
    '',
    summary='创建短视频项目（reel：一组创作的容器 + 默认创作参数）',
    dependencies=[
        Depends(RequestPermission('reel:project:add')),
        DependsRBAC,
    ],
    name='hasn_reel_admin_create_reel_project',
)
async def create_reel_project(db: CurrentSessionTransaction, obj: CreateReelProjectParam) -> ResponseModel:
    await reel_project_service.create(db=db, obj=obj)
    return response_base.success()


@router.put(
    '/{pk}',
    summary='更新短视频项目（reel：一组创作的容器 + 默认创作参数）',
    dependencies=[
        Depends(RequestPermission('reel:project:edit')),
        DependsRBAC,
    ],
    name='hasn_reel_admin_update_reel_project',
)
async def update_reel_project(
    db: CurrentSessionTransaction, pk: Annotated[int, Path(description='短视频项目（reel：一组创作的容器 + 默认创作参数） ID')], obj: UpdateReelProjectParam
) -> ResponseModel:
    count = await reel_project_service.update(db=db, pk=pk, obj=obj)
    if count > 0:
        return response_base.success()
    return response_base.fail()


@router.delete(
    '',
    summary='批量删除短视频项目（reel：一组创作的容器 + 默认创作参数）',
    dependencies=[
        Depends(RequestPermission('reel:project:del')),
        DependsRBAC,
    ],
    name='hasn_reel_admin_delete_reel_project',
)
async def delete_reel_project(db: CurrentSessionTransaction, obj: DeleteReelProjectParam) -> ResponseModel:
    count = await reel_project_service.delete(db=db, obj=obj)
    if count > 0:
        return response_base.success()
    return response_base.fail()
