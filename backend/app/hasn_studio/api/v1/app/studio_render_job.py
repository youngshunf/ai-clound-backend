"""视频渲染任务（运行态镜像 + 进度/成本 + 审计；引擎权威态经云端轮询/webhook 同步落库） - 用户端 API

认证方式: DependsJwtAuth（仅当前登录用户）
数据隔离: 通过 request.user.id 限制为用户自己的数据
"""
from typing import Annotated

from fastapi import APIRouter, Path, Request

from backend.app.hasn_studio.schema.studio_render_job import (
    CreateStudioRenderJobParam,
    GetStudioRenderJobDetail,
    UpdateStudioRenderJobParam,
)
from backend.app.hasn_studio.service.studio_render_job_service import studio_render_job_service
from backend.common.exception import errors
from backend.common.pagination import DependsPagination, PageData
from backend.common.response.response_schema import ResponseModel, ResponseSchemaModel, response_base
from backend.common.security.jwt import DependsJwtAuth
from backend.database.db import CurrentSession, CurrentSessionTransaction

router = APIRouter()


@router.get(
    '',
    summary='获取我的视频渲染任务（运行态镜像 + 进度/成本 + 审计；引擎权威态经云端轮询/webhook 同步落库）列表',
    dependencies=[DependsJwtAuth, DependsPagination],
    name='hasn_studio_app_get_my_studio_render_job',
)
async def get_my_studio_render_job(
    request: Request,
    db: CurrentSession,
) -> ResponseSchemaModel[PageData[GetStudioRenderJobDetail]]:
    page_data = await studio_render_job_service.get_list(db=db)
    return response_base.success(data=page_data)


@router.post(
    '',
    summary='创建视频渲染任务（运行态镜像 + 进度/成本 + 审计；引擎权威态经云端轮询/webhook 同步落库）',
    dependencies=[DependsJwtAuth],
    name='hasn_studio_app_create_my_studio_render_job',
)
async def create_my_studio_render_job(
    request: Request,
    db: CurrentSessionTransaction,
    obj: CreateStudioRenderJobParam,
) -> ResponseModel:
    result = await studio_render_job_service.create(db=db, obj=obj)
    return response_base.success(data=result)


@router.get(
    '/{pk}',
    summary='获取视频渲染任务（运行态镜像 + 进度/成本 + 审计；引擎权威态经云端轮询/webhook 同步落库）详情',
    dependencies=[DependsJwtAuth],
    name='hasn_studio_app_get_my_studio_render_job_detail',
)
async def get_my_studio_render_job_detail(
    request: Request,
    db: CurrentSession,
    pk: Annotated[int, Path(description='视频渲染任务（运行态镜像 + 进度/成本 + 审计；引擎权威态经云端轮询/webhook 同步落库） ID')],
) -> ResponseSchemaModel[GetStudioRenderJobDetail]:
    studio_render_job = await studio_render_job_service.get(db=db, pk=pk)
    if studio_render_job.user_id != request.user.id:
        raise errors.ForbiddenError(msg='无权访问该视频渲染任务（运行态镜像 + 进度/成本 + 审计；引擎权威态经云端轮询/webhook 同步落库）')
    return response_base.success(data=studio_render_job)


@router.put(
    '/{pk}',
    summary='更新视频渲染任务（运行态镜像 + 进度/成本 + 审计；引擎权威态经云端轮询/webhook 同步落库）',
    dependencies=[DependsJwtAuth],
    name='hasn_studio_app_update_my_studio_render_job',
)
async def update_my_studio_render_job(
    request: Request,
    db: CurrentSessionTransaction,
    pk: Annotated[int, Path(description='视频渲染任务（运行态镜像 + 进度/成本 + 审计；引擎权威态经云端轮询/webhook 同步落库） ID')],
    obj: UpdateStudioRenderJobParam,
) -> ResponseModel:
    studio_render_job = await studio_render_job_service.get(db=db, pk=pk)
    if getattr(studio_render_job, 'user_id', request.user.id) != request.user.id:
        raise errors.ForbiddenError(msg='无权修改该视频渲染任务（运行态镜像 + 进度/成本 + 审计；引擎权威态经云端轮询/webhook 同步落库）')
    count = await studio_render_job_service.update(db=db, pk=pk, obj=obj)
    if count > 0:
        return response_base.success()
    return response_base.fail()


@router.delete(
    '/{pk}',
    summary='删除视频渲染任务（运行态镜像 + 进度/成本 + 审计；引擎权威态经云端轮询/webhook 同步落库）',
    dependencies=[DependsJwtAuth],
    name='hasn_studio_app_delete_my_studio_render_job',
)
async def delete_my_studio_render_job(
    request: Request,
    db: CurrentSessionTransaction,
    pk: Annotated[int, Path(description='视频渲染任务（运行态镜像 + 进度/成本 + 审计；引擎权威态经云端轮询/webhook 同步落库） ID')],
) -> ResponseModel:
    user_id = request.user.id
    studio_render_job = await studio_render_job_service.get(db=db, pk=pk)
    if studio_render_job.user_id != user_id:
        raise errors.ForbiddenError(msg='无权删除该视频渲染任务（运行态镜像 + 进度/成本 + 审计；引擎权威态经云端轮询/webhook 同步落库）')
    from backend.app.hasn_studio.schema.studio_render_job import DeleteStudioRenderJobParam
    count = await studio_render_job_service.delete(db=db, obj=DeleteStudioRenderJobParam(pks=[pk]))
    if count > 0:
        return response_base.success()
    return response_base.fail()
