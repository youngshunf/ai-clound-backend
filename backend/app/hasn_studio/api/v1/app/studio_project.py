"""视频项目（统一视频引擎 studio：管线/素材/成品的容器） - 用户端 API

认证方式: DependsJwtAuth（仅当前登录用户）
数据隔离: 通过 request.user.id 限制为用户自己的数据
"""
from typing import Annotated

from fastapi import APIRouter, Path, Request

from backend.app.hasn_studio.schema.studio_project import (
    CreateStudioProjectParam,
    GetStudioProjectDetail,
    UpdateStudioProjectParam,
)
from backend.app.hasn_studio.service.studio_project_service import studio_project_service
from backend.common.exception import errors
from backend.common.pagination import DependsPagination, PageData
from backend.common.response.response_schema import ResponseModel, ResponseSchemaModel, response_base
from backend.common.security.jwt import DependsJwtAuth
from backend.database.db import CurrentSession, CurrentSessionTransaction

router = APIRouter()


@router.get(
    '',
    summary='获取我的视频项目（统一视频引擎 studio：管线/素材/成品的容器）列表',
    dependencies=[DependsJwtAuth, DependsPagination],
    name='hasn_studio_app_get_my_studio_project',
)
async def get_my_studio_project(
    request: Request,
    db: CurrentSession,
) -> ResponseSchemaModel[PageData[GetStudioProjectDetail]]:
    page_data = await studio_project_service.get_list(db=db)
    return response_base.success(data=page_data)


@router.post(
    '',
    summary='创建视频项目（统一视频引擎 studio：管线/素材/成品的容器）',
    dependencies=[DependsJwtAuth],
    name='hasn_studio_app_create_my_studio_project',
)
async def create_my_studio_project(
    request: Request,
    db: CurrentSessionTransaction,
    obj: CreateStudioProjectParam,
) -> ResponseModel:
    result = await studio_project_service.create(db=db, obj=obj)
    return response_base.success(data=result)


@router.get(
    '/{pk}',
    summary='获取视频项目（统一视频引擎 studio：管线/素材/成品的容器）详情',
    dependencies=[DependsJwtAuth],
    name='hasn_studio_app_get_my_studio_project_detail',
)
async def get_my_studio_project_detail(
    request: Request,
    db: CurrentSession,
    pk: Annotated[int, Path(description='视频项目（统一视频引擎 studio：管线/素材/成品的容器） ID')],
) -> ResponseSchemaModel[GetStudioProjectDetail]:
    studio_project = await studio_project_service.get(db=db, pk=pk)
    if studio_project.user_id != request.user.id:
        raise errors.ForbiddenError(msg='无权访问该视频项目（统一视频引擎 studio：管线/素材/成品的容器）')
    return response_base.success(data=studio_project)


@router.put(
    '/{pk}',
    summary='更新视频项目（统一视频引擎 studio：管线/素材/成品的容器）',
    dependencies=[DependsJwtAuth],
    name='hasn_studio_app_update_my_studio_project',
)
async def update_my_studio_project(
    request: Request,
    db: CurrentSessionTransaction,
    pk: Annotated[int, Path(description='视频项目（统一视频引擎 studio：管线/素材/成品的容器） ID')],
    obj: UpdateStudioProjectParam,
) -> ResponseModel:
    studio_project = await studio_project_service.get(db=db, pk=pk)
    if getattr(studio_project, 'user_id', request.user.id) != request.user.id:
        raise errors.ForbiddenError(msg='无权修改该视频项目（统一视频引擎 studio：管线/素材/成品的容器）')
    count = await studio_project_service.update(db=db, pk=pk, obj=obj)
    if count > 0:
        return response_base.success()
    return response_base.fail()


@router.delete(
    '/{pk}',
    summary='删除视频项目（统一视频引擎 studio：管线/素材/成品的容器）',
    dependencies=[DependsJwtAuth],
    name='hasn_studio_app_delete_my_studio_project',
)
async def delete_my_studio_project(
    request: Request,
    db: CurrentSessionTransaction,
    pk: Annotated[int, Path(description='视频项目（统一视频引擎 studio：管线/素材/成品的容器） ID')],
) -> ResponseModel:
    user_id = request.user.id
    studio_project = await studio_project_service.get(db=db, pk=pk)
    if studio_project.user_id != user_id:
        raise errors.ForbiddenError(msg='无权删除该视频项目（统一视频引擎 studio：管线/素材/成品的容器）')
    from backend.app.hasn_studio.schema.studio_project import DeleteStudioProjectParam
    count = await studio_project_service.delete(db=db, obj=DeleteStudioProjectParam(pks=[pk]))
    if count > 0:
        return response_base.success()
    return response_base.fail()
