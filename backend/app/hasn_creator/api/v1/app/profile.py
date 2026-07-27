"""项目画像（1:1 project）；定位参数 + 内容支柱权重（进化核心） - 用户端 API

认证方式: DependsJwtAuth（仅当前登录用户）
数据隔离: 通过 request.user.id 限制为用户自己的数据
"""
from typing import Annotated

from fastapi import APIRouter, Path, Request

from backend.app.hasn_creator.schema.profile import (
    CreateProfileParam,
    GetProfileDetail,
    UpdateProfileParam,
)
from backend.app.hasn_creator.service.profile_service import profile_service
from backend.common.exception import errors
from backend.common.pagination import DependsPagination, PageData
from backend.common.response.response_schema import ResponseModel, ResponseSchemaModel, response_base
from backend.common.security.jwt import DependsJwtAuth
from backend.database.db import CurrentSession, CurrentSessionTransaction

router = APIRouter()


@router.get(
    '',
    summary='获取我的项目画像（1:1 project）；定位参数 + 内容支柱权重（进化核心）列表',
    dependencies=[DependsJwtAuth, DependsPagination],
    name='hasn_creator_app_get_my_profile',
)
async def get_my_profile(
    request: Request,
    db: CurrentSession,
) -> ResponseSchemaModel[PageData[GetProfileDetail]]:
    page_data = await profile_service.get_list(db=db)
    return response_base.success(data=page_data)


@router.post(
    '',
    summary='创建项目画像（1:1 project）；定位参数 + 内容支柱权重（进化核心）',
    dependencies=[DependsJwtAuth],
    name='hasn_creator_app_create_my_profile',
)
async def create_my_profile(
    request: Request,
    db: CurrentSessionTransaction,
    obj: CreateProfileParam,
) -> ResponseModel:
    await profile_service.create(db=db, obj=obj)
    return response_base.success()


@router.get(
    '/{pk}',
    summary='获取项目画像（1:1 project）；定位参数 + 内容支柱权重（进化核心）详情',
    dependencies=[DependsJwtAuth],
    name='hasn_creator_app_get_my_profile_detail',
)
async def get_my_profile_detail(
    request: Request,
    db: CurrentSession,
    pk: Annotated[int, Path(description='项目画像（1:1 project）；定位参数 + 内容支柱权重（进化核心） ID')],
) -> ResponseSchemaModel[GetProfileDetail]:
    profile = await profile_service.get(db=db, pk=pk)
    if profile.user_id != request.user.id:
        raise errors.ForbiddenError(msg='无权访问该项目画像（1:1 project）；定位参数 + 内容支柱权重（进化核心）')
    return response_base.success(data=profile)


@router.put(
    '/{pk}',
    summary='更新项目画像（1:1 project）；定位参数 + 内容支柱权重（进化核心）',
    dependencies=[DependsJwtAuth],
    name='hasn_creator_app_update_my_profile',
)
async def update_my_profile(
    request: Request,
    db: CurrentSessionTransaction,
    pk: Annotated[int, Path(description='项目画像（1:1 project）；定位参数 + 内容支柱权重（进化核心） ID')],
    obj: UpdateProfileParam,
) -> ResponseModel:
    profile = await profile_service.get(db=db, pk=pk)
    if getattr(profile, 'user_id', request.user.id) != request.user.id:
        raise errors.ForbiddenError(msg='无权修改该项目画像（1:1 project）；定位参数 + 内容支柱权重（进化核心）')
    count = await profile_service.update(db=db, pk=pk, obj=obj)
    if count > 0:
        return response_base.success()
    return response_base.fail()


@router.delete(
    '/{pk}',
    summary='删除项目画像（1:1 project）；定位参数 + 内容支柱权重（进化核心）',
    dependencies=[DependsJwtAuth],
    name='hasn_creator_app_delete_my_profile',
)
async def delete_my_profile(
    request: Request,
    db: CurrentSessionTransaction,
    pk: Annotated[int, Path(description='项目画像（1:1 project）；定位参数 + 内容支柱权重（进化核心） ID')],
) -> ResponseModel:
    user_id = request.user.id
    profile = await profile_service.get(db=db, pk=pk)
    if profile.user_id != user_id:
        raise errors.ForbiddenError(msg='无权删除该项目画像（1:1 project）；定位参数 + 内容支柱权重（进化核心）')
    from backend.app.hasn_creator.schema.profile import DeleteProfileParam
    count = await profile_service.delete(db=db, obj=DeleteProfileParam(pks=[pk]))
    if count > 0:
        return response_base.success()
    return response_base.fail()
