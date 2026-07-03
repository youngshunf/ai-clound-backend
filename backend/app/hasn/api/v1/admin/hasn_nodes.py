from typing import Annotated

from fastapi import APIRouter, Depends, Path

from backend.app.hasn.schema.hasn_nodes import (
    CreateHasnNodesParam,
    DeleteHasnNodesParam,
    GetHasnNodesDetail,
    UpdateHasnNodesParam,
)
from backend.app.hasn.service.hasn_nodes_service import hasn_nodes_service
from backend.common.pagination import DependsPagination, PageData
from backend.common.response.response_schema import ResponseModel, ResponseSchemaModel, response_base
from backend.common.security.jwt import DependsJwtAuth
from backend.common.security.permission import RequestPermission
from backend.common.security.rbac import DependsRBAC
from backend.database.db import CurrentSession, CurrentSessionTransaction

router = APIRouter()


@router.get('/{pk}', summary='获取HASN Node 主详情', dependencies=[DependsJwtAuth], name='admin_admin_get_hasn_nodes')
async def admin_get_hasn_nodes(
    db: CurrentSession, pk: Annotated[int, Path(description='HASN Node 主 ID')]
) -> ResponseSchemaModel[GetHasnNodesDetail]:
    hasn_nodes = await hasn_nodes_service.get(db=db, pk=pk)
    return response_base.success(data=hasn_nodes)


@router.get(
    '',
    summary='分页获取所有HASN Node 主',
    dependencies=[
        DependsJwtAuth,
        DependsPagination,
    ],
 name='admin_get_hasn_nodess_paginated')
async def get_hasn_nodess_paginated(db: CurrentSession) -> ResponseSchemaModel[PageData[GetHasnNodesDetail]]:
    page_data = await hasn_nodes_service.get_list(db=db)
    return response_base.success(data=page_data)


@router.post(
    '',
    summary='创建HASN Node 主',
    dependencies=[
        Depends(RequestPermission('hasn:nodes:add')),
        DependsRBAC,
    ],
)
async def create_hasn_nodes(db: CurrentSessionTransaction, obj: CreateHasnNodesParam) -> ResponseModel:
    await hasn_nodes_service.create(db=db, obj=obj)
    return response_base.success()


@router.put(
    '/{pk}',
    summary='更新HASN Node 主',
    dependencies=[
        Depends(RequestPermission('hasn:nodes:edit')),
        DependsRBAC,
    ],
)
async def update_hasn_nodes(
    db: CurrentSessionTransaction, pk: Annotated[int, Path(description='HASN Node 主 ID')], obj: UpdateHasnNodesParam
) -> ResponseModel:
    count = await hasn_nodes_service.update(db=db, pk=pk, obj=obj)
    if count > 0:
        return response_base.success()
    return response_base.fail()


@router.delete(
    '',
    summary='批量删除HASN Node 主',
    dependencies=[
        Depends(RequestPermission('hasn:nodes:del')),
        DependsRBAC,
    ],
)
async def delete_hasn_nodess(db: CurrentSessionTransaction, obj: DeleteHasnNodesParam) -> ResponseModel:
    count = await hasn_nodes_service.delete(db=db, obj=obj)
    if count > 0:
        return response_base.success()
    return response_base.fail()
