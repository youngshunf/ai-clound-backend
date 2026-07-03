from typing import Annotated

from fastapi import APIRouter, Depends, Path

from backend.app.hasn.schema.hasn_task_run import (
    CreateHasnTaskRunParam,
    DeleteHasnTaskRunParam,
    GetHasnTaskRunDetail,
    UpdateHasnTaskRunParam,
)
from backend.app.hasn.service.hasn_task_run_service import hasn_task_run_service
from backend.common.pagination import DependsPagination, PageData
from backend.common.response.response_schema import ResponseModel, ResponseSchemaModel, response_base
from backend.common.security.jwt import DependsJwtAuth
from backend.common.security.permission import RequestPermission
from backend.common.security.rbac import DependsRBAC
from backend.database.db import CurrentSession, CurrentSessionTransaction

router = APIRouter()


@router.get('/{pk}', summary='获取任务执行记录详情', dependencies=[DependsJwtAuth], name='admin_get_hasn_task_run')
async def get_hasn_task_run(
    db: CurrentSession, pk: Annotated[int, Path(description='任务执行记录 ID')]
) -> ResponseSchemaModel[GetHasnTaskRunDetail]:
    hasn_task_run = await hasn_task_run_service.get(db=db, pk=pk)
    return response_base.success(data=hasn_task_run)


@router.get(
    '',
    summary='分页获取所有任务执行记录',
    dependencies=[
        DependsJwtAuth,
        DependsPagination,
    ],
    name='admin_get_hasn_task_run_paginated',
)
async def get_hasn_task_run_paginated(db: CurrentSession) -> ResponseSchemaModel[PageData[GetHasnTaskRunDetail]]:
    page_data = await hasn_task_run_service.get_list(db=db)
    return response_base.success(data=page_data)


@router.post(
    '',
    summary='创建任务执行记录',
    dependencies=[
        Depends(RequestPermission('hasn:task:run:add')),
        DependsRBAC,
    ],
    name='admin_create_hasn_task_run',
)
async def create_hasn_task_run(db: CurrentSessionTransaction, obj: CreateHasnTaskRunParam) -> ResponseModel:
    await hasn_task_run_service.create(db=db, obj=obj)
    return response_base.success()


@router.put(
    '/{pk}',
    summary='更新任务执行记录',
    dependencies=[
        Depends(RequestPermission('hasn:task:run:edit')),
        DependsRBAC,
    ],
    name='admin_update_hasn_task_run',
)
async def update_hasn_task_run(
    db: CurrentSessionTransaction, pk: Annotated[int, Path(description='任务执行记录 ID')], obj: UpdateHasnTaskRunParam
) -> ResponseModel:
    count = await hasn_task_run_service.update(db=db, pk=pk, obj=obj)
    if count > 0:
        return response_base.success()
    return response_base.fail()


@router.delete(
    '',
    summary='批量删除任务执行记录',
    dependencies=[
        Depends(RequestPermission('hasn:task:run:del')),
        DependsRBAC,
    ],
    name='admin_delete_hasn_task_run',
)
async def delete_hasn_task_run(db: CurrentSessionTransaction, obj: DeleteHasnTaskRunParam) -> ResponseModel:
    count = await hasn_task_run_service.delete(db=db, obj=obj)
    if count > 0:
        return response_base.success()
    return response_base.fail()
