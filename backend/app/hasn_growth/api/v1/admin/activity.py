from typing import Annotated

from fastapi import APIRouter, Depends, Path

from backend.app.hasn_growth.schema.activity import (
    CreateActivityParam,
    DeleteActivityParam,
    GetActivityDetail,
    UpdateActivityParam,
)
from backend.app.hasn_growth.service.activity_service import activity_service
from backend.common.pagination import DependsPagination, PageData
from backend.common.response.response_schema import ResponseModel, ResponseSchemaModel, response_base
from backend.common.security.jwt import DependsJwtAuth
from backend.common.security.permission import RequestPermission
from backend.common.security.rbac import DependsRBAC
from backend.database.db import CurrentSession, CurrentSessionTransaction

router = APIRouter()


@router.get('/{pk}', summary='获取获客客户活动时间线（触达/回复/阶段变更/任务 run/人工备注统一流水）详情', dependencies=[DependsJwtAuth], name='admin_get_activity')
async def get_activity(
    db: CurrentSession, pk: Annotated[int, Path(description='获客客户活动时间线（触达/回复/阶段变更/任务 run/人工备注统一流水） ID')]
) -> ResponseSchemaModel[GetActivityDetail]:
    activity = await activity_service.get(db=db, pk=pk)
    return response_base.success(data=activity)


@router.get(
    '',
    summary='分页获取所有获客客户活动时间线（触达/回复/阶段变更/任务 run/人工备注统一流水）',
    dependencies=[
        DependsJwtAuth,
        DependsPagination,
    ],
    name='admin_get_activity_paginated',
)
async def get_activity_paginated(db: CurrentSession) -> ResponseSchemaModel[PageData[GetActivityDetail]]:
    page_data = await activity_service.get_list(db=db)
    return response_base.success(data=page_data)


@router.post(
    '',
    summary='创建获客客户活动时间线（触达/回复/阶段变更/任务 run/人工备注统一流水）',
    dependencies=[
        Depends(RequestPermission('activity:add')),
        DependsRBAC,
    ],
    name='admin_create_activity',
)
async def create_activity(db: CurrentSessionTransaction, obj: CreateActivityParam) -> ResponseModel:
    await activity_service.create(db=db, obj=obj)
    return response_base.success()


@router.put(
    '/{pk}',
    summary='更新获客客户活动时间线（触达/回复/阶段变更/任务 run/人工备注统一流水）',
    dependencies=[
        Depends(RequestPermission('activity:edit')),
        DependsRBAC,
    ],
    name='admin_update_activity',
)
async def update_activity(
    db: CurrentSessionTransaction, pk: Annotated[int, Path(description='获客客户活动时间线（触达/回复/阶段变更/任务 run/人工备注统一流水） ID')], obj: UpdateActivityParam
) -> ResponseModel:
    count = await activity_service.update(db=db, pk=pk, obj=obj)
    if count > 0:
        return response_base.success()
    return response_base.fail()


@router.delete(
    '',
    summary='批量删除获客客户活动时间线（触达/回复/阶段变更/任务 run/人工备注统一流水）',
    dependencies=[
        Depends(RequestPermission('activity:del')),
        DependsRBAC,
    ],
    name='admin_delete_activity',
)
async def delete_activity(db: CurrentSessionTransaction, obj: DeleteActivityParam) -> ResponseModel:
    count = await activity_service.delete(db=db, obj=obj)
    if count > 0:
        return response_base.success()
    return response_base.fail()
