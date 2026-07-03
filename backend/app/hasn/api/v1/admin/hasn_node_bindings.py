from typing import Annotated

from fastapi import APIRouter, Depends, Path

from backend.app.hasn.schema.hasn_node_bindings import (
    CreateHasnNodeBindingsParam,
    DeleteHasnNodeBindingsParam,
    GetHasnNodeBindingsDetail,
    UpdateHasnNodeBindingsParam,
)
from backend.app.hasn.service.hasn_node_bindings_service import hasn_node_bindings_service
from backend.common.pagination import DependsPagination, PageData
from backend.common.response.response_schema import ResponseModel, ResponseSchemaModel, response_base
from backend.common.security.jwt import DependsJwtAuth
from backend.common.security.permission import RequestPermission
from backend.common.security.rbac import DependsRBAC
from backend.database.db import CurrentSession, CurrentSessionTransaction

router = APIRouter()


@router.get('/{pk}', summary='获取HASN Node Owner Binding 租约详情', dependencies=[DependsJwtAuth], name='admin_get_hasn_node_bindings')
async def get_hasn_node_bindings(
    db: CurrentSession, pk: Annotated[int, Path(description='HASN Node Owner Binding 租约 ID')]
) -> ResponseSchemaModel[GetHasnNodeBindingsDetail]:
    hasn_node_bindings = await hasn_node_bindings_service.get(db=db, pk=pk)
    return response_base.success(data=hasn_node_bindings)


@router.get(
    '',
    summary='分页获取所有HASN Node Owner Binding 租约',
    dependencies=[
        DependsJwtAuth,
        DependsPagination,
    ],
 name='admin_get_hasn_node_bindingss_paginated')
async def get_hasn_node_bindingss_paginated(db: CurrentSession) -> ResponseSchemaModel[PageData[GetHasnNodeBindingsDetail]]:
    page_data = await hasn_node_bindings_service.get_list(db=db)
    return response_base.success(data=page_data)


@router.post(
    '',
    summary='创建HASN Node Owner Binding 租约',
    dependencies=[
        Depends(RequestPermission('hasn:node:bindings:add')),
        DependsRBAC,
    ],
)
async def create_hasn_node_bindings(db: CurrentSessionTransaction, obj: CreateHasnNodeBindingsParam) -> ResponseModel:
    await hasn_node_bindings_service.create(db=db, obj=obj)
    return response_base.success()


@router.put(
    '/{pk}',
    summary='更新HASN Node Owner Binding 租约',
    dependencies=[
        Depends(RequestPermission('hasn:node:bindings:edit')),
        DependsRBAC,
    ],
)
async def update_hasn_node_bindings(
    db: CurrentSessionTransaction, pk: Annotated[int, Path(description='HASN Node Owner Binding 租约 ID')], obj: UpdateHasnNodeBindingsParam
) -> ResponseModel:
    count = await hasn_node_bindings_service.update(db=db, pk=pk, obj=obj)
    if count > 0:
        return response_base.success()
    return response_base.fail()


@router.delete(
    '',
    summary='批量删除HASN Node Owner Binding 租约',
    dependencies=[
        Depends(RequestPermission('hasn:node:bindings:del')),
        DependsRBAC,
    ],
)
async def delete_hasn_node_bindingss(db: CurrentSessionTransaction, obj: DeleteHasnNodeBindingsParam) -> ResponseModel:
    count = await hasn_node_bindings_service.delete(db=db, obj=obj)
    if count > 0:
        return response_base.success()
    return response_base.fail()
