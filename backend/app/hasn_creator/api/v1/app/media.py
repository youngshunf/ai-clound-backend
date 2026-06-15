"""素材库；配图/封面/视频/模板（私有桶引用） - 用户端 API

认证方式: DependsJwtAuth（仅当前登录用户）
数据隔离: 通过 request.user.id 限制为用户自己的数据
"""
from typing import Annotated

from fastapi import APIRouter, Path, Request

from backend.app.hasn_creator.schema.media import (
    CreateMediaParam,
    GetMediaDetail,
    UpdateMediaParam,
)
from backend.app.hasn_creator.service.media_service import media_service
from backend.common.exception import errors
from backend.common.pagination import DependsPagination, PageData
from backend.common.response.response_schema import ResponseModel, ResponseSchemaModel, response_base
from backend.common.security.jwt import DependsJwtAuth
from backend.database.db import CurrentSession, CurrentSessionTransaction

router = APIRouter()


@router.get(
    '',
    summary='获取我的素材库；配图/封面/视频/模板（私有桶引用）列表',
    dependencies=[DependsJwtAuth, DependsPagination],
    name='hasn_creator_app_get_my_media',
)
async def get_my_media(
    request: Request,
    db: CurrentSession,
) -> ResponseSchemaModel[PageData[GetMediaDetail]]:
    page_data = await media_service.get_list(db=db)
    return response_base.success(data=page_data)


@router.post(
    '',
    summary='创建素材库；配图/封面/视频/模板（私有桶引用）',
    dependencies=[DependsJwtAuth],
    name='hasn_creator_app_create_my_media',
)
async def create_my_media(
    request: Request,
    db: CurrentSessionTransaction,
    obj: CreateMediaParam,
) -> ResponseModel:
    result = await media_service.create(db=db, obj=obj)
    return response_base.success(data=result)


@router.get(
    '/{pk}',
    summary='获取素材库；配图/封面/视频/模板（私有桶引用）详情',
    dependencies=[DependsJwtAuth],
    name='hasn_creator_app_get_my_media_detail',
)
async def get_my_media_detail(
    request: Request,
    db: CurrentSession,
    pk: Annotated[int, Path(description='素材库；配图/封面/视频/模板（私有桶引用） ID')],
) -> ResponseSchemaModel[GetMediaDetail]:
    media = await media_service.get(db=db, pk=pk)
    if media.user_id != request.user.id:
        raise errors.ForbiddenError(msg='无权访问该素材库；配图/封面/视频/模板（私有桶引用）')
    return response_base.success(data=media)


@router.put(
    '/{pk}',
    summary='更新素材库；配图/封面/视频/模板（私有桶引用）',
    dependencies=[DependsJwtAuth],
    name='hasn_creator_app_update_my_media',
)
async def update_my_media(
    request: Request,
    db: CurrentSessionTransaction,
    pk: Annotated[int, Path(description='素材库；配图/封面/视频/模板（私有桶引用） ID')],
    obj: UpdateMediaParam,
) -> ResponseModel:
    media = await media_service.get(db=db, pk=pk)
    if getattr(media, 'user_id', request.user.id) != request.user.id:
        raise errors.ForbiddenError(msg='无权修改该素材库；配图/封面/视频/模板（私有桶引用）')
    count = await media_service.update(db=db, pk=pk, obj=obj)
    if count > 0:
        return response_base.success()
    return response_base.fail()


@router.delete(
    '/{pk}',
    summary='删除素材库；配图/封面/视频/模板（私有桶引用）',
    dependencies=[DependsJwtAuth],
    name='hasn_creator_app_delete_my_media',
)
async def delete_my_media(
    request: Request,
    db: CurrentSessionTransaction,
    pk: Annotated[int, Path(description='素材库；配图/封面/视频/模板（私有桶引用） ID')],
) -> ResponseModel:
    user_id = request.user.id
    media = await media_service.get(db=db, pk=pk)
    if media.user_id != user_id:
        raise errors.ForbiddenError(msg='无权删除该素材库；配图/封面/视频/模板（私有桶引用）')
    from backend.app.hasn_creator.schema.media import DeleteMediaParam
    count = await media_service.delete(db=db, obj=DeleteMediaParam(pks=[pk]))
    if count > 0:
        return response_base.success()
    return response_base.fail()
