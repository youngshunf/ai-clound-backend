from typing import Annotated

from fastapi import APIRouter, Depends, Path

from backend.app.hasn_creator.schema.media import (
    CreateMediaParam,
    DeleteMediaParam,
    GetMediaDetail,
    UpdateMediaParam,
)
from backend.app.hasn_creator.service.media_service import media_service
from backend.common.pagination import DependsPagination, PageData
from backend.common.response.response_schema import ResponseModel, ResponseSchemaModel, response_base
from backend.common.security.jwt import DependsJwtAuth
from backend.common.security.permission import RequestPermission
from backend.common.security.rbac import DependsRBAC
from backend.database.db import CurrentSession, CurrentSessionTransaction

router = APIRouter()


@router.get('/{pk}', summary='获取素材库；配图/封面/视频/模板（私有桶引用）详情', dependencies=[DependsJwtAuth], name='hasn_creator_admin_get_media')
async def get_media(
    db: CurrentSession, pk: Annotated[int, Path(description='素材库；配图/封面/视频/模板（私有桶引用） ID')]
) -> ResponseSchemaModel[GetMediaDetail]:
    media = await media_service.get(db=db, pk=pk)
    return response_base.success(data=media)


@router.get(
    '',
    summary='分页获取所有素材库；配图/封面/视频/模板（私有桶引用）',
    dependencies=[
        DependsJwtAuth,
        DependsPagination,
    ],
    name='hasn_creator_admin_get_media_paginated',
)
async def get_media_paginated(db: CurrentSession) -> ResponseSchemaModel[PageData[GetMediaDetail]]:
    page_data = await media_service.get_list(db=db)
    return response_base.success(data=page_data)


@router.post(
    '',
    summary='创建素材库；配图/封面/视频/模板（私有桶引用）',
    dependencies=[
        Depends(RequestPermission('media:add')),
        DependsRBAC,
    ],
    name='hasn_creator_admin_create_media',
)
async def create_media(db: CurrentSessionTransaction, obj: CreateMediaParam) -> ResponseModel:
    await media_service.create(db=db, obj=obj)
    return response_base.success()


@router.put(
    '/{pk}',
    summary='更新素材库；配图/封面/视频/模板（私有桶引用）',
    dependencies=[
        Depends(RequestPermission('media:edit')),
        DependsRBAC,
    ],
    name='hasn_creator_admin_update_media',
)
async def update_media(
    db: CurrentSessionTransaction, pk: Annotated[int, Path(description='素材库；配图/封面/视频/模板（私有桶引用） ID')], obj: UpdateMediaParam
) -> ResponseModel:
    count = await media_service.update(db=db, pk=pk, obj=obj)
    if count > 0:
        return response_base.success()
    return response_base.fail()


@router.delete(
    '',
    summary='批量删除素材库；配图/封面/视频/模板（私有桶引用）',
    dependencies=[
        Depends(RequestPermission('media:del')),
        DependsRBAC,
    ],
    name='hasn_creator_admin_delete_media',
)
async def delete_media(db: CurrentSessionTransaction, obj: DeleteMediaParam) -> ResponseModel:
    count = await media_service.delete(db=db, obj=obj)
    if count > 0:
        return response_base.success()
    return response_base.fail()
