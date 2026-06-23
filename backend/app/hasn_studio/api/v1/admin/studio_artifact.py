from typing import Annotated

from fastapi import APIRouter, Depends, Path

from backend.app.hasn_studio.schema.studio_artifact import (
    CreateStudioArtifactParam,
    DeleteStudioArtifactParam,
    GetStudioArtifactDetail,
    UpdateStudioArtifactParam,
)
from backend.app.hasn_studio.service.studio_artifact_service import studio_artifact_service
from backend.common.pagination import DependsPagination, PageData
from backend.common.response.response_schema import ResponseModel, ResponseSchemaModel, response_base
from backend.common.security.jwt import DependsJwtAuth
from backend.common.security.permission import RequestPermission
from backend.common.security.rbac import DependsRBAC
from backend.database.db import CurrentSession, CurrentSessionTransaction

router = APIRouter()


@router.get('/{pk}', summary='获取视频成品（最终视频 + 元数据；与通用索引 public.hasn_artifacts 同引 hasn://asset/）详情', dependencies=[DependsJwtAuth], name='hasn_studio_admin_get_studio_artifact')
async def get_studio_artifact(
    db: CurrentSession, pk: Annotated[int, Path(description='视频成品（最终视频 + 元数据；与通用索引 public.hasn_artifacts 同引 hasn://asset/） ID')]
) -> ResponseSchemaModel[GetStudioArtifactDetail]:
    studio_artifact = await studio_artifact_service.get(db=db, pk=pk)
    return response_base.success(data=studio_artifact)


@router.get(
    '',
    summary='分页获取所有视频成品（最终视频 + 元数据；与通用索引 public.hasn_artifacts 同引 hasn://asset/）',
    dependencies=[
        DependsJwtAuth,
        DependsPagination,
    ],
    name='hasn_studio_admin_get_studio_artifact_paginated',
)
async def get_studio_artifact_paginated(db: CurrentSession) -> ResponseSchemaModel[PageData[GetStudioArtifactDetail]]:
    page_data = await studio_artifact_service.get_list(db=db)
    return response_base.success(data=page_data)


@router.post(
    '',
    summary='创建视频成品（最终视频 + 元数据；与通用索引 public.hasn_artifacts 同引 hasn://asset/）',
    dependencies=[
        Depends(RequestPermission('studio:artifact:add')),
        DependsRBAC,
    ],
    name='hasn_studio_admin_create_studio_artifact',
)
async def create_studio_artifact(db: CurrentSessionTransaction, obj: CreateStudioArtifactParam) -> ResponseModel:
    await studio_artifact_service.create(db=db, obj=obj)
    return response_base.success()


@router.put(
    '/{pk}',
    summary='更新视频成品（最终视频 + 元数据；与通用索引 public.hasn_artifacts 同引 hasn://asset/）',
    dependencies=[
        Depends(RequestPermission('studio:artifact:edit')),
        DependsRBAC,
    ],
    name='hasn_studio_admin_update_studio_artifact',
)
async def update_studio_artifact(
    db: CurrentSessionTransaction, pk: Annotated[int, Path(description='视频成品（最终视频 + 元数据；与通用索引 public.hasn_artifacts 同引 hasn://asset/） ID')], obj: UpdateStudioArtifactParam
) -> ResponseModel:
    count = await studio_artifact_service.update(db=db, pk=pk, obj=obj)
    if count > 0:
        return response_base.success()
    return response_base.fail()


@router.delete(
    '',
    summary='批量删除视频成品（最终视频 + 元数据；与通用索引 public.hasn_artifacts 同引 hasn://asset/）',
    dependencies=[
        Depends(RequestPermission('studio:artifact:del')),
        DependsRBAC,
    ],
    name='hasn_studio_admin_delete_studio_artifact',
)
async def delete_studio_artifact(db: CurrentSessionTransaction, obj: DeleteStudioArtifactParam) -> ResponseModel:
    count = await studio_artifact_service.delete(db=db, obj=obj)
    if count > 0:
        return response_base.success()
    return response_base.fail()
