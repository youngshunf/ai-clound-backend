"""视频成品（最终视频 + 元数据；与通用索引 public.hasn_artifacts 同引 hasn://asset/） - 用户端 API

认证方式: DependsJwtAuth（仅当前登录用户）
数据隔离: 通过 request.user.id 限制为用户自己的数据
"""
from typing import Annotated

from fastapi import APIRouter, Path, Request

from backend.app.hasn_studio.schema.studio_artifact import (
    CreateStudioArtifactParam,
    GetStudioArtifactDetail,
    UpdateStudioArtifactParam,
)
from backend.app.hasn_studio.service.studio_artifact_service import studio_artifact_service
from backend.common.exception import errors
from backend.common.pagination import DependsPagination, PageData
from backend.common.response.response_schema import ResponseModel, ResponseSchemaModel, response_base
from backend.common.security.jwt import DependsJwtAuth
from backend.database.db import CurrentSession, CurrentSessionTransaction

router = APIRouter()


@router.get(
    '',
    summary='获取我的视频成品（最终视频 + 元数据；与通用索引 public.hasn_artifacts 同引 hasn://asset/）列表',
    dependencies=[DependsJwtAuth, DependsPagination],
    name='hasn_studio_app_get_my_studio_artifact',
)
async def get_my_studio_artifact(
    request: Request,
    db: CurrentSession,
) -> ResponseSchemaModel[PageData[GetStudioArtifactDetail]]:
    page_data = await studio_artifact_service.get_list(db=db)
    return response_base.success(data=page_data)


@router.post(
    '',
    summary='创建视频成品（最终视频 + 元数据；与通用索引 public.hasn_artifacts 同引 hasn://asset/）',
    dependencies=[DependsJwtAuth],
    name='hasn_studio_app_create_my_studio_artifact',
)
async def create_my_studio_artifact(
    request: Request,
    db: CurrentSessionTransaction,
    obj: CreateStudioArtifactParam,
) -> ResponseModel:
    result = await studio_artifact_service.create(db=db, obj=obj)
    return response_base.success(data=result)


@router.get(
    '/{pk}',
    summary='获取视频成品（最终视频 + 元数据；与通用索引 public.hasn_artifacts 同引 hasn://asset/）详情',
    dependencies=[DependsJwtAuth],
    name='hasn_studio_app_get_my_studio_artifact_detail',
)
async def get_my_studio_artifact_detail(
    request: Request,
    db: CurrentSession,
    pk: Annotated[int, Path(description='视频成品（最终视频 + 元数据；与通用索引 public.hasn_artifacts 同引 hasn://asset/） ID')],
) -> ResponseSchemaModel[GetStudioArtifactDetail]:
    studio_artifact = await studio_artifact_service.get(db=db, pk=pk)
    if studio_artifact.user_id != request.user.id:
        raise errors.ForbiddenError(msg='无权访问该视频成品（最终视频 + 元数据；与通用索引 public.hasn_artifacts 同引 hasn://asset/）')
    return response_base.success(data=studio_artifact)


@router.put(
    '/{pk}',
    summary='更新视频成品（最终视频 + 元数据；与通用索引 public.hasn_artifacts 同引 hasn://asset/）',
    dependencies=[DependsJwtAuth],
    name='hasn_studio_app_update_my_studio_artifact',
)
async def update_my_studio_artifact(
    request: Request,
    db: CurrentSessionTransaction,
    pk: Annotated[int, Path(description='视频成品（最终视频 + 元数据；与通用索引 public.hasn_artifacts 同引 hasn://asset/） ID')],
    obj: UpdateStudioArtifactParam,
) -> ResponseModel:
    studio_artifact = await studio_artifact_service.get(db=db, pk=pk)
    if getattr(studio_artifact, 'user_id', request.user.id) != request.user.id:
        raise errors.ForbiddenError(msg='无权修改该视频成品（最终视频 + 元数据；与通用索引 public.hasn_artifacts 同引 hasn://asset/）')
    count = await studio_artifact_service.update(db=db, pk=pk, obj=obj)
    if count > 0:
        return response_base.success()
    return response_base.fail()


@router.delete(
    '/{pk}',
    summary='删除视频成品（最终视频 + 元数据；与通用索引 public.hasn_artifacts 同引 hasn://asset/）',
    dependencies=[DependsJwtAuth],
    name='hasn_studio_app_delete_my_studio_artifact',
)
async def delete_my_studio_artifact(
    request: Request,
    db: CurrentSessionTransaction,
    pk: Annotated[int, Path(description='视频成品（最终视频 + 元数据；与通用索引 public.hasn_artifacts 同引 hasn://asset/） ID')],
) -> ResponseModel:
    user_id = request.user.id
    studio_artifact = await studio_artifact_service.get(db=db, pk=pk)
    if studio_artifact.user_id != user_id:
        raise errors.ForbiddenError(msg='无权删除该视频成品（最终视频 + 元数据；与通用索引 public.hasn_artifacts 同引 hasn://asset/）')
    from backend.app.hasn_studio.schema.studio_artifact import DeleteStudioArtifactParam
    count = await studio_artifact_service.delete(db=db, obj=DeleteStudioArtifactParam(pks=[pk]))
    if count > 0:
        return response_base.success()
    return response_base.fail()
