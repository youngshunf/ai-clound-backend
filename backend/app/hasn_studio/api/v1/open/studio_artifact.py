"""视频成品（最终视频 + 元数据；与通用索引 public.hasn_artifacts 同引 hasn://asset/） - 公开 API

认证方式: 无（公开接口，无需登录）
"""
from typing import Annotated

from fastapi import APIRouter, Path

from backend.app.hasn_studio.schema.studio_artifact import GetStudioArtifactDetail
from backend.app.hasn_studio.service.studio_artifact_service import studio_artifact_service
from backend.common.pagination import DependsPagination, PageData
from backend.common.response.response_schema import ResponseSchemaModel, response_base
from backend.database.db import CurrentSession

router = APIRouter()


@router.get(
    '',
    summary='获取视频成品（最终视频 + 元数据；与通用索引 public.hasn_artifacts 同引 hasn://asset/）列表',
    dependencies=[DependsPagination],
    name='hasn_studio_open_get_studio_artifact',
)
async def get_studio_artifact(
    db: CurrentSession,
) -> ResponseSchemaModel[PageData[GetStudioArtifactDetail]]:
    page_data = await studio_artifact_service.get_list(db=db)
    return response_base.success(data=page_data)


@router.get(
    '/{pk}',
    summary='获取视频成品（最终视频 + 元数据；与通用索引 public.hasn_artifacts 同引 hasn://asset/）详情',
    name='hasn_studio_open_get_studio_artifact_detail',
)
async def get_studio_artifact_detail(
    db: CurrentSession,
    pk: Annotated[int, Path(description='视频成品（最终视频 + 元数据；与通用索引 public.hasn_artifacts 同引 hasn://asset/） ID')],
) -> ResponseSchemaModel[GetStudioArtifactDetail]:
    studio_artifact = await studio_artifact_service.get(db=db, pk=pk)
    return response_base.success(data=studio_artifact)
