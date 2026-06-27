"""短视频项目（reel：一组创作的容器 + 默认创作参数） - 用户端 API

认证方式: DependsJwtAuth（仅当前登录用户）
数据隔离: 通过 request.user.id 限制为用户自己的数据
"""
from typing import Annotated

from fastapi import APIRouter, Path, Request

from backend.app.hasn_reel.schema.reel_project import (
    CreateReelProjectParam,
    GetReelProjectDetail,
    UpdateReelProjectParam,
)
from backend.app.hasn_reel.service.reel_project_service import reel_project_service
from backend.common.exception import errors
from backend.common.pagination import DependsPagination, PageData
from backend.common.response.response_schema import ResponseModel, ResponseSchemaModel, response_base
from backend.common.security.jwt import DependsJwtAuth
from backend.database.db import CurrentSession, CurrentSessionTransaction

router = APIRouter()


@router.get(
    '',
    summary='获取我的短视频项目（reel：一组创作的容器 + 默认创作参数）列表',
    dependencies=[DependsJwtAuth, DependsPagination],
    name='hasn_reel_app_get_my_reel_project',
)
async def get_my_reel_project(
    request: Request,
    db: CurrentSession,
) -> ResponseSchemaModel[PageData[GetReelProjectDetail]]:
    page_data = await reel_project_service.get_list(db=db)
    return response_base.success(data=page_data)


@router.post(
    '',
    summary='创建短视频项目（reel：一组创作的容器 + 默认创作参数）',
    dependencies=[DependsJwtAuth],
    name='hasn_reel_app_create_my_reel_project',
)
async def create_my_reel_project(
    request: Request,
    db: CurrentSessionTransaction,
    obj: CreateReelProjectParam,
) -> ResponseModel:
    result = await reel_project_service.create(db=db, obj=obj)
    return response_base.success(data=result)


@router.get(
    '/{pk}',
    summary='获取短视频项目（reel：一组创作的容器 + 默认创作参数）详情',
    dependencies=[DependsJwtAuth],
    name='hasn_reel_app_get_my_reel_project_detail',
)
async def get_my_reel_project_detail(
    request: Request,
    db: CurrentSession,
    pk: Annotated[int, Path(description='短视频项目（reel：一组创作的容器 + 默认创作参数） ID')],
) -> ResponseSchemaModel[GetReelProjectDetail]:
    reel_project = await reel_project_service.get(db=db, pk=pk)
    if reel_project.user_id != request.user.id:
        raise errors.ForbiddenError(msg='无权访问该短视频项目（reel：一组创作的容器 + 默认创作参数）')
    return response_base.success(data=reel_project)


@router.put(
    '/{pk}',
    summary='更新短视频项目（reel：一组创作的容器 + 默认创作参数）',
    dependencies=[DependsJwtAuth],
    name='hasn_reel_app_update_my_reel_project',
)
async def update_my_reel_project(
    request: Request,
    db: CurrentSessionTransaction,
    pk: Annotated[int, Path(description='短视频项目（reel：一组创作的容器 + 默认创作参数） ID')],
    obj: UpdateReelProjectParam,
) -> ResponseModel:
    reel_project = await reel_project_service.get(db=db, pk=pk)
    if getattr(reel_project, 'user_id', request.user.id) != request.user.id:
        raise errors.ForbiddenError(msg='无权修改该短视频项目（reel：一组创作的容器 + 默认创作参数）')
    count = await reel_project_service.update(db=db, pk=pk, obj=obj)
    if count > 0:
        return response_base.success()
    return response_base.fail()


@router.delete(
    '/{pk}',
    summary='删除短视频项目（reel：一组创作的容器 + 默认创作参数）',
    dependencies=[DependsJwtAuth],
    name='hasn_reel_app_delete_my_reel_project',
)
async def delete_my_reel_project(
    request: Request,
    db: CurrentSessionTransaction,
    pk: Annotated[int, Path(description='短视频项目（reel：一组创作的容器 + 默认创作参数） ID')],
) -> ResponseModel:
    user_id = request.user.id
    reel_project = await reel_project_service.get(db=db, pk=pk)
    if reel_project.user_id != user_id:
        raise errors.ForbiddenError(msg='无权删除该短视频项目（reel：一组创作的容器 + 默认创作参数）')
    from backend.app.hasn_reel.schema.reel_project import DeleteReelProjectParam
    count = await reel_project_service.delete(db=db, obj=DeleteReelProjectParam(pks=[pk]))
    if count > 0:
        return response_base.success()
    return response_base.fail()
