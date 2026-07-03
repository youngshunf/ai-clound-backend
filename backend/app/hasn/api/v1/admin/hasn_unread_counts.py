from typing import Annotated

from fastapi import APIRouter, Depends, Path

from backend.app.hasn.schema.hasn_unread_counts import (
    CreateHasnUnreadCountsParam,
    DeleteHasnUnreadCountsParam,
    GetHasnUnreadCountsDetail,
    UpdateHasnUnreadCountsParam,
)
from backend.app.hasn.service.hasn_unread_counts_service import hasn_unread_counts_service
from backend.common.pagination import DependsPagination, PageData
from backend.common.response.response_schema import ResponseModel, ResponseSchemaModel, response_base
from backend.common.security.jwt import DependsJwtAuth
from backend.common.security.permission import RequestPermission
from backend.common.security.rbac import DependsRBAC
from backend.database.db import CurrentSession, CurrentSessionTransaction

router = APIRouter()


@router.get('/{pk}', summary='获取HASN 未读计数详情', dependencies=[DependsJwtAuth], name='admin_get_hasn_unread_counts')
async def get_hasn_unread_counts(
    db: CurrentSession, pk: Annotated[int, Path(description='HASN 未读计数 ID')]
) -> ResponseSchemaModel[GetHasnUnreadCountsDetail]:
    hasn_unread_counts = await hasn_unread_counts_service.get(db=db, pk=pk)
    return response_base.success(data=hasn_unread_counts)


@router.get(
    '',
    summary='分页获取所有HASN 未读计数',
    dependencies=[
        DependsJwtAuth,
        DependsPagination,
    ],
 name='admin_get_hasn_unread_countss_paginated')
async def get_hasn_unread_countss_paginated(db: CurrentSession) -> ResponseSchemaModel[PageData[GetHasnUnreadCountsDetail]]:
    page_data = await hasn_unread_counts_service.get_list(db=db)
    return response_base.success(data=page_data)


@router.post(
    '',
    summary='创建HASN 未读计数',
    dependencies=[
        Depends(RequestPermission('hasn:unread:counts:add')),
        DependsRBAC,
    ],
)
async def create_hasn_unread_counts(db: CurrentSessionTransaction, obj: CreateHasnUnreadCountsParam) -> ResponseModel:
    await hasn_unread_counts_service.create(db=db, obj=obj)
    return response_base.success()


@router.put(
    '/{pk}',
    summary='更新HASN 未读计数',
    dependencies=[
        Depends(RequestPermission('hasn:unread:counts:edit')),
        DependsRBAC,
    ],
)
async def update_hasn_unread_counts(
    db: CurrentSessionTransaction, pk: Annotated[int, Path(description='HASN 未读计数 ID')], obj: UpdateHasnUnreadCountsParam
) -> ResponseModel:
    count = await hasn_unread_counts_service.update(db=db, pk=pk, obj=obj)
    if count > 0:
        return response_base.success()
    return response_base.fail()


@router.delete(
    '',
    summary='批量删除HASN 未读计数',
    dependencies=[
        Depends(RequestPermission('hasn:unread:counts:del')),
        DependsRBAC,
    ],
)
async def delete_hasn_unread_countss(db: CurrentSessionTransaction, obj: DeleteHasnUnreadCountsParam) -> ResponseModel:
    count = await hasn_unread_counts_service.delete(db=db, obj=obj)
    if count > 0:
        return response_base.success()
    return response_base.fail()
