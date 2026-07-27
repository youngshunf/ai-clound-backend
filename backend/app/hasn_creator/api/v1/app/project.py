"""运营单元根（一个「号」的定位与运营单元）；双模归属 + 负责人 + 打法 + 自主度 - 用户端 API

认证方式: DependsJwtAuth（仅当前登录用户）
数据隔离: 通过 request.user.id 限制为用户自己的数据
"""
from typing import Annotated

from fastapi import APIRouter, Path, Request

from backend.app.hasn_creator.schema.project import (
    CreateProjectParam,
    GetProjectDetail,
    UpdateProjectParam,
)
from backend.app.hasn_creator.service.project_service import project_service
from backend.common.exception import errors
from backend.common.pagination import DependsPagination, PageData
from backend.common.response.response_schema import ResponseModel, ResponseSchemaModel, response_base
from backend.common.security.jwt import DependsJwtAuth
from backend.database.db import CurrentSession, CurrentSessionTransaction

router = APIRouter()


@router.get(
    '',
    summary='获取我的运营单元根（一个「号」的定位与运营单元）；双模归属 + 负责人 + 打法 + 自主度列表',
    dependencies=[DependsJwtAuth, DependsPagination],
    name='hasn_creator_app_get_my_project',
)
async def get_my_project(
    request: Request,
    db: CurrentSession,
) -> ResponseSchemaModel[PageData[GetProjectDetail]]:
    page_data = await project_service.get_list(db=db)
    return response_base.success(data=page_data)


@router.post(
    '',
    summary='创建运营单元根（一个「号」的定位与运营单元）；双模归属 + 负责人 + 打法 + 自主度',
    dependencies=[DependsJwtAuth],
    name='hasn_creator_app_create_my_project',
)
async def create_my_project(
    request: Request,
    db: CurrentSessionTransaction,
    obj: CreateProjectParam,
) -> ResponseModel:
    await project_service.create(db=db, obj=obj)
    return response_base.success()


@router.get(
    '/{pk}',
    summary='获取运营单元根（一个「号」的定位与运营单元）；双模归属 + 负责人 + 打法 + 自主度详情',
    dependencies=[DependsJwtAuth],
    name='hasn_creator_app_get_my_project_detail',
)
async def get_my_project_detail(
    request: Request,
    db: CurrentSession,
    pk: Annotated[int, Path(description='运营单元根（一个「号」的定位与运营单元）；双模归属 + 负责人 + 打法 + 自主度 ID')],
) -> ResponseSchemaModel[GetProjectDetail]:
    project = await project_service.get(db=db, pk=pk)
    if project.user_id != request.user.id:
        raise errors.ForbiddenError(msg='无权访问该运营单元根（一个「号」的定位与运营单元）；双模归属 + 负责人 + 打法 + 自主度')
    return response_base.success(data=project)


@router.put(
    '/{pk}',
    summary='更新运营单元根（一个「号」的定位与运营单元）；双模归属 + 负责人 + 打法 + 自主度',
    dependencies=[DependsJwtAuth],
    name='hasn_creator_app_update_my_project',
)
async def update_my_project(
    request: Request,
    db: CurrentSessionTransaction,
    pk: Annotated[int, Path(description='运营单元根（一个「号」的定位与运营单元）；双模归属 + 负责人 + 打法 + 自主度 ID')],
    obj: UpdateProjectParam,
) -> ResponseModel:
    project = await project_service.get(db=db, pk=pk)
    if getattr(project, 'user_id', request.user.id) != request.user.id:
        raise errors.ForbiddenError(msg='无权修改该运营单元根（一个「号」的定位与运营单元）；双模归属 + 负责人 + 打法 + 自主度')
    count = await project_service.update(db=db, pk=pk, obj=obj)
    if count > 0:
        return response_base.success()
    return response_base.fail()


@router.delete(
    '/{pk}',
    summary='删除运营单元根（一个「号」的定位与运营单元）；双模归属 + 负责人 + 打法 + 自主度',
    dependencies=[DependsJwtAuth],
    name='hasn_creator_app_delete_my_project',
)
async def delete_my_project(
    request: Request,
    db: CurrentSessionTransaction,
    pk: Annotated[int, Path(description='运营单元根（一个「号」的定位与运营单元）；双模归属 + 负责人 + 打法 + 自主度 ID')],
) -> ResponseModel:
    user_id = request.user.id
    project = await project_service.get(db=db, pk=pk)
    if project.user_id != user_id:
        raise errors.ForbiddenError(msg='无权删除该运营单元根（一个「号」的定位与运营单元）；双模归属 + 负责人 + 打法 + 自主度')
    from backend.app.hasn_creator.schema.project import DeleteProjectParam
    count = await project_service.delete(db=db, obj=DeleteProjectParam(pks=[pk]))
    if count > 0:
        return response_base.success()
    return response_base.fail()
