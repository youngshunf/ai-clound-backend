from typing import Annotated

from fastapi import APIRouter, Depends, Path, Query

from backend.app.hasn_creator.schema.publish import (
    CreatePublishParam,
    DeletePublishParam,
    GetPublishDetail,
    UpdatePublishParam,
)
from backend.app.hasn_creator.service.publish_service import publish_service
from backend.common.pagination import DependsPagination, PageData
from backend.common.response.response_schema import ResponseModel, ResponseSchemaModel, response_base
from backend.common.security.jwt import DependsJwtAuth
from backend.common.security.permission import RequestPermission
from backend.common.security.rbac import DependsRBAC
from backend.database.db import CurrentSession, CurrentSessionTransaction

router = APIRouter()


@router.get('/{pk}', summary='获取发布记录（= content × account：发到某平台账号 + 数据指标）详情', dependencies=[DependsJwtAuth], name='hasn_creator_admin_get_publish')
async def get_publish(
    db: CurrentSession, pk: Annotated[int, Path(description='发布记录（= content × account：发到某平台账号 + 数据指标） ID')]
) -> ResponseSchemaModel[GetPublishDetail]:
    publish = await publish_service.get(db=db, pk=pk)
    return response_base.success(data=publish)


@router.get(
    '',
    summary='分页获取所有发布记录（= content × account：发到某平台账号 + 数据指标）',
    dependencies=[
        DependsJwtAuth,
        DependsPagination,
    ],
    name='hasn_creator_admin_get_publish_paginated',
)
async def get_publish_paginated(db: CurrentSession) -> ResponseSchemaModel[PageData[GetPublishDetail]]:
    page_data = await publish_service.get_list(db=db)
    return response_base.success(data=page_data)


@router.post(
    '',
    summary='创建发布记录（= content × account：发到某平台账号 + 数据指标）',
    dependencies=[
        Depends(RequestPermission('publish:add')),
        DependsRBAC,
    ],
    name='hasn_creator_admin_create_publish',
)
async def create_publish(db: CurrentSessionTransaction, obj: CreatePublishParam) -> ResponseModel:
    await publish_service.create(db=db, obj=obj)
    return response_base.success()


@router.put(
    '/{pk}',
    summary='更新发布记录（= content × account：发到某平台账号 + 数据指标）',
    dependencies=[
        Depends(RequestPermission('publish:edit')),
        DependsRBAC,
    ],
    name='hasn_creator_admin_update_publish',
)
async def update_publish(
    db: CurrentSessionTransaction, pk: Annotated[int, Path(description='发布记录（= content × account：发到某平台账号 + 数据指标） ID')], obj: UpdatePublishParam
) -> ResponseModel:
    count = await publish_service.update(db=db, pk=pk, obj=obj)
    if count > 0:
        return response_base.success()
    return response_base.fail()


@router.delete(
    '',
    summary='批量删除发布记录（= content × account：发到某平台账号 + 数据指标）',
    dependencies=[
        Depends(RequestPermission('publish:del')),
        DependsRBAC,
    ],
    name='hasn_creator_admin_delete_publish',
)
async def delete_publish(db: CurrentSessionTransaction, obj: DeletePublishParam) -> ResponseModel:
    count = await publish_service.delete(db=db, obj=obj)
    if count > 0:
        return response_base.success()
    return response_base.fail()
