"""发布记录（= content × account：发到某平台账号 + 数据指标） - 用户端 API

认证方式: DependsJwtAuth（仅当前登录用户）
数据隔离: 通过 request.user.id 限制为用户自己的数据
"""
from typing import Annotated

from fastapi import APIRouter, Path, Request

from backend.app.hasn_creator.schema.publish import (
    CreatePublishParam,
    GetPublishDetail,
    UpdatePublishParam,
)
from backend.app.hasn_creator.service.publish_service import publish_service
from backend.common.exception import errors
from backend.common.pagination import DependsPagination, PageData
from backend.common.response.response_schema import ResponseModel, ResponseSchemaModel, response_base
from backend.common.security.jwt import DependsJwtAuth
from backend.database.db import CurrentSession, CurrentSessionTransaction

router = APIRouter()


@router.get(
    '',
    summary='获取我的发布记录（= content × account：发到某平台账号 + 数据指标）列表',
    dependencies=[DependsJwtAuth, DependsPagination],
    name='hasn_creator_app_get_my_publish',
)
async def get_my_publish(
    request: Request,
    db: CurrentSession,
) -> ResponseSchemaModel[PageData[GetPublishDetail]]:
    page_data = await publish_service.get_list(db=db)
    return response_base.success(data=page_data)


@router.post(
    '',
    summary='创建发布记录（= content × account：发到某平台账号 + 数据指标）',
    dependencies=[DependsJwtAuth],
    name='hasn_creator_app_create_my_publish',
)
async def create_my_publish(
    request: Request,
    db: CurrentSessionTransaction,
    obj: CreatePublishParam,
) -> ResponseModel:
    result = await publish_service.create(db=db, obj=obj)
    return response_base.success(data=result)


@router.get(
    '/{pk}',
    summary='获取发布记录（= content × account：发到某平台账号 + 数据指标）详情',
    dependencies=[DependsJwtAuth],
    name='hasn_creator_app_get_my_publish_detail',
)
async def get_my_publish_detail(
    request: Request,
    db: CurrentSession,
    pk: Annotated[int, Path(description='发布记录（= content × account：发到某平台账号 + 数据指标） ID')],
) -> ResponseSchemaModel[GetPublishDetail]:
    publish = await publish_service.get(db=db, pk=pk)
    if publish.user_id != request.user.id:
        raise errors.ForbiddenError(msg='无权访问该发布记录（= content × account：发到某平台账号 + 数据指标）')
    return response_base.success(data=publish)


@router.put(
    '/{pk}',
    summary='更新发布记录（= content × account：发到某平台账号 + 数据指标）',
    dependencies=[DependsJwtAuth],
    name='hasn_creator_app_update_my_publish',
)
async def update_my_publish(
    request: Request,
    db: CurrentSessionTransaction,
    pk: Annotated[int, Path(description='发布记录（= content × account：发到某平台账号 + 数据指标） ID')],
    obj: UpdatePublishParam,
) -> ResponseModel:
    publish = await publish_service.get(db=db, pk=pk)
    if getattr(publish, 'user_id', request.user.id) != request.user.id:
        raise errors.ForbiddenError(msg='无权修改该发布记录（= content × account：发到某平台账号 + 数据指标）')
    count = await publish_service.update(db=db, pk=pk, obj=obj)
    if count > 0:
        return response_base.success()
    return response_base.fail()


@router.delete(
    '/{pk}',
    summary='删除发布记录（= content × account：发到某平台账号 + 数据指标）',
    dependencies=[DependsJwtAuth],
    name='hasn_creator_app_delete_my_publish',
)
async def delete_my_publish(
    request: Request,
    db: CurrentSessionTransaction,
    pk: Annotated[int, Path(description='发布记录（= content × account：发到某平台账号 + 数据指标） ID')],
) -> ResponseModel:
    user_id = request.user.id
    publish = await publish_service.get(db=db, pk=pk)
    if publish.user_id != user_id:
        raise errors.ForbiddenError(msg='无权删除该发布记录（= content × account：发到某平台账号 + 数据指标）')
    from backend.app.hasn_creator.schema.publish import DeletePublishParam
    count = await publish_service.delete(db=db, obj=DeletePublishParam(pks=[pk]))
    if count > 0:
        return response_base.success()
    return response_base.fail()
