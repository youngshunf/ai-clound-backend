from typing import Annotated

from fastapi import APIRouter, Depends, Path

from backend.app.hasn.schema.hasn_channel_bindings import (
    CreateHasnChannelBindingsParam,
    DeleteHasnChannelBindingsParam,
    GetHasnChannelBindingsDetail,
    UpdateHasnChannelBindingsParam,
)
from backend.app.hasn.service.hasn_channel_bindings_service import hasn_channel_bindings_service
from backend.common.pagination import DependsPagination, PageData
from backend.common.response.response_schema import ResponseModel, ResponseSchemaModel, response_base
from backend.common.security.jwt import DependsJwtAuth
from backend.common.security.permission import RequestPermission
from backend.common.security.rbac import DependsRBAC
from backend.database.db import CurrentSession, CurrentSessionTransaction

router = APIRouter()


@router.get('/{pk}', summary='获取HASN Channel Binding 详情', dependencies=[DependsJwtAuth], name='admin_get_hasn_channel_bindings')
async def get_hasn_channel_bindings(
    db: CurrentSession, pk: Annotated[int, Path(description='HASN Channel Binding  ID')]
) -> ResponseSchemaModel[GetHasnChannelBindingsDetail]:
    hasn_channel_bindings = await hasn_channel_bindings_service.get(db=db, pk=pk)
    return response_base.success(data=hasn_channel_bindings)


@router.get(
    '',
    summary='分页获取所有HASN Channel Binding ',
    dependencies=[
        DependsJwtAuth,
        DependsPagination,
    ],
 name='admin_get_hasn_channel_bindingss_paginated')
async def get_hasn_channel_bindingss_paginated(db: CurrentSession) -> ResponseSchemaModel[PageData[GetHasnChannelBindingsDetail]]:
    page_data = await hasn_channel_bindings_service.get_list(db=db)
    return response_base.success(data=page_data)


@router.post(
    '',
    summary='创建HASN Channel Binding ',
    dependencies=[
        Depends(RequestPermission('hasn:channel:bindings:add')),
        DependsRBAC,
    ],
)
async def create_hasn_channel_bindings(db: CurrentSessionTransaction, obj: CreateHasnChannelBindingsParam) -> ResponseModel:
    await hasn_channel_bindings_service.create(db=db, obj=obj)
    return response_base.success()


@router.put(
    '/{pk}',
    summary='更新HASN Channel Binding ',
    dependencies=[
        Depends(RequestPermission('hasn:channel:bindings:edit')),
        DependsRBAC,
    ],
)
async def update_hasn_channel_bindings(
    db: CurrentSessionTransaction, pk: Annotated[int, Path(description='HASN Channel Binding  ID')], obj: UpdateHasnChannelBindingsParam
) -> ResponseModel:
    count = await hasn_channel_bindings_service.update(db=db, pk=pk, obj=obj)
    if count > 0:
        return response_base.success()
    return response_base.fail()


@router.delete(
    '',
    summary='批量删除HASN Channel Binding ',
    dependencies=[
        Depends(RequestPermission('hasn:channel:bindings:del')),
        DependsRBAC,
    ],
)
async def delete_hasn_channel_bindingss(db: CurrentSessionTransaction, obj: DeleteHasnChannelBindingsParam) -> ResponseModel:
    count = await hasn_channel_bindings_service.delete(db=db, obj=obj)
    if count > 0:
        return response_base.success()
    return response_base.fail()
