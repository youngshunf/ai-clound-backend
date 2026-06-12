from typing import Annotated

from fastapi import APIRouter, Depends, Path, Query

from backend.app.hasn_growth.schema.optout_record import (
    CreateOptoutRecordParam,
    DeleteOptoutRecordParam,
    GetOptoutRecordDetail,
    UpdateOptoutRecordParam,
)
from backend.app.hasn_growth.service.optout_record_service import optout_record_service
from backend.common.pagination import DependsPagination, PageData
from backend.common.response.response_schema import ResponseModel, ResponseSchemaModel, response_base
from backend.common.security.jwt import DependsJwtAuth
from backend.common.security.permission import RequestPermission
from backend.common.security.rbac import DependsRBAC
from backend.database.db import CurrentSession, CurrentSessionTransaction

router = APIRouter()


@router.get('/{pk}', summary='获取获客退订/勿扰登记（合规硬约束，outreach.send 入口硬查，命中即 blocked_optout）详情', dependencies=[DependsJwtAuth], name='admin_get_optout_record')
async def get_optout_record(
    db: CurrentSession, pk: Annotated[int, Path(description='获客退订/勿扰登记（合规硬约束，outreach.send 入口硬查，命中即 blocked_optout） ID')]
) -> ResponseSchemaModel[GetOptoutRecordDetail]:
    optout_record = await optout_record_service.get(db=db, pk=pk)
    return response_base.success(data=optout_record)


@router.get(
    '',
    summary='分页获取所有获客退订/勿扰登记（合规硬约束，outreach.send 入口硬查，命中即 blocked_optout）',
    dependencies=[
        DependsJwtAuth,
        DependsPagination,
    ],
    name='admin_get_optout_record_paginated',
)
async def get_optout_record_paginated(db: CurrentSession) -> ResponseSchemaModel[PageData[GetOptoutRecordDetail]]:
    page_data = await optout_record_service.get_list(db=db)
    return response_base.success(data=page_data)


@router.post(
    '',
    summary='创建获客退订/勿扰登记（合规硬约束，outreach.send 入口硬查，命中即 blocked_optout）',
    dependencies=[
        Depends(RequestPermission('optout:record:add')),
        DependsRBAC,
    ],
    name='admin_create_optout_record',
)
async def create_optout_record(db: CurrentSessionTransaction, obj: CreateOptoutRecordParam) -> ResponseModel:
    await optout_record_service.create(db=db, obj=obj)
    return response_base.success()


@router.put(
    '/{pk}',
    summary='更新获客退订/勿扰登记（合规硬约束，outreach.send 入口硬查，命中即 blocked_optout）',
    dependencies=[
        Depends(RequestPermission('optout:record:edit')),
        DependsRBAC,
    ],
    name='admin_update_optout_record',
)
async def update_optout_record(
    db: CurrentSessionTransaction, pk: Annotated[int, Path(description='获客退订/勿扰登记（合规硬约束，outreach.send 入口硬查，命中即 blocked_optout） ID')], obj: UpdateOptoutRecordParam
) -> ResponseModel:
    count = await optout_record_service.update(db=db, pk=pk, obj=obj)
    if count > 0:
        return response_base.success()
    return response_base.fail()


@router.delete(
    '',
    summary='批量删除获客退订/勿扰登记（合规硬约束，outreach.send 入口硬查，命中即 blocked_optout）',
    dependencies=[
        Depends(RequestPermission('optout:record:del')),
        DependsRBAC,
    ],
    name='admin_delete_optout_record',
)
async def delete_optout_record(db: CurrentSessionTransaction, obj: DeleteOptoutRecordParam) -> ResponseModel:
    count = await optout_record_service.delete(db=db, obj=obj)
    if count > 0:
        return response_base.success()
    return response_base.fail()
