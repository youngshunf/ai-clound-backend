from typing import Annotated

from fastapi import APIRouter, Depends, Path

from backend.app.hasn.schema.hasn_task import (
    CreateHasnTaskParam,
    DeleteHasnTaskParam,
    GetHasnTaskDetail,
    UpdateHasnTaskParam,
)
from backend.app.hasn.service.hasn_task_service import hasn_task_service
from backend.common.pagination import DependsPagination, PageData
from backend.common.response.response_schema import ResponseModel, ResponseSchemaModel, response_base
from backend.common.security.jwt import DependsJwtAuth
from backend.common.security.permission import RequestPermission
from backend.common.security.rbac import DependsRBAC
from backend.database.db import CurrentSession, CurrentSessionTransaction

router = APIRouter()


@router.get('/{pk}', summary='获取任务定义详情', dependencies=[DependsJwtAuth], name='admin_get_hasn_task')
async def get_hasn_task(
    db: CurrentSession, pk: Annotated[int, Path(description='任务定义 ID')]
) -> ResponseSchemaModel[GetHasnTaskDetail]:
    hasn_task = await hasn_task_service.get(db=db, pk=pk)
    return response_base.success(data=hasn_task)


@router.get(
    '',
    summary='分页获取所有任务定义',
    dependencies=[
        DependsJwtAuth,
        DependsPagination,
    ],
    name='admin_get_hasn_task_paginated',
)
async def get_hasn_task_paginated(db: CurrentSession) -> ResponseSchemaModel[PageData[GetHasnTaskDetail]]:
    page_data = await hasn_task_service.get_list(db=db)
    return response_base.success(data=page_data)


@router.post(
    '',
    summary='创建任务定义',
    dependencies=[
        Depends(RequestPermission('hasn:task:add')),
        DependsRBAC,
    ],
    name='admin_create_hasn_task',
)
async def create_hasn_task(db: CurrentSessionTransaction, obj: CreateHasnTaskParam) -> ResponseModel:
    await hasn_task_service.create(db=db, obj=obj)
    return response_base.success()


@router.put(
    '/{pk}',
    summary='更新任务定义',
    dependencies=[
        Depends(RequestPermission('hasn:task:edit')),
        DependsRBAC,
    ],
    name='admin_update_hasn_task',
)
async def update_hasn_task(
    db: CurrentSessionTransaction, pk: Annotated[int, Path(description='任务定义 ID')], obj: UpdateHasnTaskParam
) -> ResponseModel:
    count = await hasn_task_service.update(db=db, pk=pk, obj=obj)
    if count > 0:
        return response_base.success()
    return response_base.fail()


@router.delete(
    '',
    summary='批量删除任务定义',
    dependencies=[
        Depends(RequestPermission('hasn:task:del')),
        DependsRBAC,
    ],
    name='admin_delete_hasn_task',
)
async def delete_hasn_task(db: CurrentSessionTransaction, obj: DeleteHasnTaskParam) -> ResponseModel:
    count = await hasn_task_service.delete(db=db, obj=obj)
    if count > 0:
        return response_base.success()
    return response_base.fail()
