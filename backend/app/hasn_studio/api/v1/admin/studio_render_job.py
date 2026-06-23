from typing import Annotated

from fastapi import APIRouter, Depends, Path

from backend.app.hasn_studio.schema.studio_render_job import (
    CreateStudioRenderJobParam,
    DeleteStudioRenderJobParam,
    GetStudioRenderJobDetail,
    UpdateStudioRenderJobParam,
)
from backend.app.hasn_studio.service.studio_render_job_service import studio_render_job_service
from backend.common.pagination import DependsPagination, PageData
from backend.common.response.response_schema import ResponseModel, ResponseSchemaModel, response_base
from backend.common.security.jwt import DependsJwtAuth
from backend.common.security.permission import RequestPermission
from backend.common.security.rbac import DependsRBAC
from backend.database.db import CurrentSession, CurrentSessionTransaction

router = APIRouter()


@router.get('/{pk}', summary='获取视频渲染任务（运行态镜像 + 进度/成本 + 审计；引擎权威态经云端轮询/webhook 同步落库）详情', dependencies=[DependsJwtAuth], name='hasn_studio_admin_get_studio_render_job')
async def get_studio_render_job(
    db: CurrentSession, pk: Annotated[int, Path(description='视频渲染任务（运行态镜像 + 进度/成本 + 审计；引擎权威态经云端轮询/webhook 同步落库） ID')]
) -> ResponseSchemaModel[GetStudioRenderJobDetail]:
    studio_render_job = await studio_render_job_service.get(db=db, pk=pk)
    return response_base.success(data=studio_render_job)


@router.get(
    '',
    summary='分页获取所有视频渲染任务（运行态镜像 + 进度/成本 + 审计；引擎权威态经云端轮询/webhook 同步落库）',
    dependencies=[
        DependsJwtAuth,
        DependsPagination,
    ],
    name='hasn_studio_admin_get_studio_render_job_paginated',
)
async def get_studio_render_job_paginated(db: CurrentSession) -> ResponseSchemaModel[PageData[GetStudioRenderJobDetail]]:
    page_data = await studio_render_job_service.get_list(db=db)
    return response_base.success(data=page_data)


@router.post(
    '',
    summary='创建视频渲染任务（运行态镜像 + 进度/成本 + 审计；引擎权威态经云端轮询/webhook 同步落库）',
    dependencies=[
        Depends(RequestPermission('studio:render:job:add')),
        DependsRBAC,
    ],
    name='hasn_studio_admin_create_studio_render_job',
)
async def create_studio_render_job(db: CurrentSessionTransaction, obj: CreateStudioRenderJobParam) -> ResponseModel:
    await studio_render_job_service.create(db=db, obj=obj)
    return response_base.success()


@router.put(
    '/{pk}',
    summary='更新视频渲染任务（运行态镜像 + 进度/成本 + 审计；引擎权威态经云端轮询/webhook 同步落库）',
    dependencies=[
        Depends(RequestPermission('studio:render:job:edit')),
        DependsRBAC,
    ],
    name='hasn_studio_admin_update_studio_render_job',
)
async def update_studio_render_job(
    db: CurrentSessionTransaction, pk: Annotated[int, Path(description='视频渲染任务（运行态镜像 + 进度/成本 + 审计；引擎权威态经云端轮询/webhook 同步落库） ID')], obj: UpdateStudioRenderJobParam
) -> ResponseModel:
    count = await studio_render_job_service.update(db=db, pk=pk, obj=obj)
    if count > 0:
        return response_base.success()
    return response_base.fail()


@router.delete(
    '',
    summary='批量删除视频渲染任务（运行态镜像 + 进度/成本 + 审计；引擎权威态经云端轮询/webhook 同步落库）',
    dependencies=[
        Depends(RequestPermission('studio:render:job:del')),
        DependsRBAC,
    ],
    name='hasn_studio_admin_delete_studio_render_job',
)
async def delete_studio_render_job(db: CurrentSessionTransaction, obj: DeleteStudioRenderJobParam) -> ResponseModel:
    count = await studio_render_job_service.delete(db=db, obj=obj)
    if count > 0:
        return response_base.success()
    return response_base.fail()
