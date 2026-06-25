from typing import Annotated

from fastapi import APIRouter, Depends, Path

from backend.app.hasn_creator.schema.content import (
    CreateContentParam,
    DeleteContentParam,
    GetContentDetail,
    UpdateContentParam,
)
from backend.app.hasn_creator.service.content_service import content_service
from backend.common.pagination import DependsPagination, PageData
from backend.common.response.response_schema import ResponseModel, ResponseSchemaModel, response_base
from backend.common.security.jwt import DependsJwtAuth
from backend.common.security.permission import RequestPermission
from backend.common.security.rbac import DependsRBAC
from backend.database.db import CurrentSession, CurrentSessionTransaction

router = APIRouter()


@router.get('/{pk}', summary='获取内容主体（按画像创作，归 project）；状态机 + 多形态轨道 + 自主度 + 审核详情', dependencies=[DependsJwtAuth], name='hasn_creator_admin_get_content')
async def get_content(
    db: CurrentSession, pk: Annotated[int, Path(description='内容主体（按画像创作，归 project）；状态机 + 多形态轨道 + 自主度 + 审核 ID')]
) -> ResponseSchemaModel[GetContentDetail]:
    content = await content_service.get(db=db, pk=pk)
    return response_base.success(data=content)


@router.get(
    '',
    summary='分页获取所有内容主体（按画像创作，归 project）；状态机 + 多形态轨道 + 自主度 + 审核',
    dependencies=[
        DependsJwtAuth,
        DependsPagination,
    ],
    name='hasn_creator_admin_get_content_paginated',
)
async def get_content_paginated(db: CurrentSession) -> ResponseSchemaModel[PageData[GetContentDetail]]:
    page_data = await content_service.get_list(db=db)
    return response_base.success(data=page_data)


@router.post(
    '',
    summary='创建内容主体（按画像创作，归 project）；状态机 + 多形态轨道 + 自主度 + 审核',
    dependencies=[
        Depends(RequestPermission('content:add')),
        DependsRBAC,
    ],
    name='hasn_creator_admin_create_content',
)
async def create_content(db: CurrentSessionTransaction, obj: CreateContentParam) -> ResponseModel:
    await content_service.create(db=db, obj=obj)
    return response_base.success()


@router.put(
    '/{pk}',
    summary='更新内容主体（按画像创作，归 project）；状态机 + 多形态轨道 + 自主度 + 审核',
    dependencies=[
        Depends(RequestPermission('content:edit')),
        DependsRBAC,
    ],
    name='hasn_creator_admin_update_content',
)
async def update_content(
    db: CurrentSessionTransaction, pk: Annotated[int, Path(description='内容主体（按画像创作，归 project）；状态机 + 多形态轨道 + 自主度 + 审核 ID')], obj: UpdateContentParam
) -> ResponseModel:
    count = await content_service.update(db=db, pk=pk, obj=obj)
    if count > 0:
        return response_base.success()
    return response_base.fail()


@router.delete(
    '',
    summary='批量删除内容主体（按画像创作，归 project）；状态机 + 多形态轨道 + 自主度 + 审核',
    dependencies=[
        Depends(RequestPermission('content:del')),
        DependsRBAC,
    ],
    name='hasn_creator_admin_delete_content',
)
async def delete_content(db: CurrentSessionTransaction, obj: DeleteContentParam) -> ResponseModel:
    count = await content_service.delete(db=db, obj=obj)
    if count > 0:
        return response_base.success()
    return response_base.fail()
