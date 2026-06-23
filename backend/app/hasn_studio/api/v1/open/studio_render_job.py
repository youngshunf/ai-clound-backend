"""视频渲染任务（运行态镜像 + 进度/成本 + 审计；引擎权威态经云端轮询/webhook 同步落库） - 公开 API

认证方式: 无（公开接口，无需登录）
"""
from typing import Annotated

from fastapi import APIRouter, Path

from backend.app.hasn_studio.schema.studio_render_job import GetStudioRenderJobDetail
from backend.app.hasn_studio.service.studio_render_job_service import studio_render_job_service
from backend.common.pagination import DependsPagination, PageData
from backend.common.response.response_schema import ResponseSchemaModel, response_base
from backend.database.db import CurrentSession

router = APIRouter()


@router.get(
    '',
    summary='获取视频渲染任务（运行态镜像 + 进度/成本 + 审计；引擎权威态经云端轮询/webhook 同步落库）列表',
    dependencies=[DependsPagination],
    name='hasn_studio_open_get_studio_render_job',
)
async def get_studio_render_job(
    db: CurrentSession,
) -> ResponseSchemaModel[PageData[GetStudioRenderJobDetail]]:
    page_data = await studio_render_job_service.get_list(db=db)
    return response_base.success(data=page_data)


@router.get(
    '/{pk}',
    summary='获取视频渲染任务（运行态镜像 + 进度/成本 + 审计；引擎权威态经云端轮询/webhook 同步落库）详情',
    name='hasn_studio_open_get_studio_render_job_detail',
)
async def get_studio_render_job_detail(
    db: CurrentSession,
    pk: Annotated[int, Path(description='视频渲染任务（运行态镜像 + 进度/成本 + 审计；引擎权威态经云端轮询/webhook 同步落库） ID')],
) -> ResponseSchemaModel[GetStudioRenderJobDetail]:
    studio_render_job = await studio_render_job_service.get(db=db, pk=pk)
    return response_base.success(data=studio_render_job)
