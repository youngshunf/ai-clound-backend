from typing import Annotated

from fastapi import APIRouter, Depends, Path, Query

from backend.app.hasn_creator.schema.profile import (
    CreateProfileParam,
    DeleteProfileParam,
    GetProfileDetail,
    UpdateProfileParam,
)
from backend.app.hasn_creator.service.profile_service import profile_service
from backend.common.pagination import DependsPagination, PageData
from backend.common.response.response_schema import ResponseModel, ResponseSchemaModel, response_base
from backend.common.security.jwt import DependsJwtAuth
from backend.common.security.permission import RequestPermission
from backend.common.security.rbac import DependsRBAC
from backend.database.db import CurrentSession, CurrentSessionTransaction

router = APIRouter()


@router.get('/{pk}', summary='获取项目画像（1:1 project）；定位参数 + 内容支柱权重（进化核心）详情', dependencies=[DependsJwtAuth], name='hasn_creator_admin_get_profile')
async def get_profile(
    db: CurrentSession, pk: Annotated[int, Path(description='项目画像（1:1 project）；定位参数 + 内容支柱权重（进化核心） ID')]
) -> ResponseSchemaModel[GetProfileDetail]:
    profile = await profile_service.get(db=db, pk=pk)
    return response_base.success(data=profile)


@router.get(
    '',
    summary='分页获取所有项目画像（1:1 project）；定位参数 + 内容支柱权重（进化核心）',
    dependencies=[
        DependsJwtAuth,
        DependsPagination,
    ],
    name='hasn_creator_admin_get_profile_paginated',
)
async def get_profile_paginated(db: CurrentSession) -> ResponseSchemaModel[PageData[GetProfileDetail]]:
    page_data = await profile_service.get_list(db=db)
    return response_base.success(data=page_data)


@router.post(
    '',
    summary='创建项目画像（1:1 project）；定位参数 + 内容支柱权重（进化核心）',
    dependencies=[
        Depends(RequestPermission('profile:add')),
        DependsRBAC,
    ],
    name='hasn_creator_admin_create_profile',
)
async def create_profile(db: CurrentSessionTransaction, obj: CreateProfileParam) -> ResponseModel:
    await profile_service.create(db=db, obj=obj)
    return response_base.success()


@router.put(
    '/{pk}',
    summary='更新项目画像（1:1 project）；定位参数 + 内容支柱权重（进化核心）',
    dependencies=[
        Depends(RequestPermission('profile:edit')),
        DependsRBAC,
    ],
    name='hasn_creator_admin_update_profile',
)
async def update_profile(
    db: CurrentSessionTransaction, pk: Annotated[int, Path(description='项目画像（1:1 project）；定位参数 + 内容支柱权重（进化核心） ID')], obj: UpdateProfileParam
) -> ResponseModel:
    count = await profile_service.update(db=db, pk=pk, obj=obj)
    if count > 0:
        return response_base.success()
    return response_base.fail()


@router.delete(
    '',
    summary='批量删除项目画像（1:1 project）；定位参数 + 内容支柱权重（进化核心）',
    dependencies=[
        Depends(RequestPermission('profile:del')),
        DependsRBAC,
    ],
    name='hasn_creator_admin_delete_profile',
)
async def delete_profile(db: CurrentSessionTransaction, obj: DeleteProfileParam) -> ResponseModel:
    count = await profile_service.delete(db=db, obj=obj)
    if count > 0:
        return response_base.success()
    return response_base.fail()
