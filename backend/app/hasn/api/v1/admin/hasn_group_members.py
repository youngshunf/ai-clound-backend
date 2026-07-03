from typing import Annotated

from fastapi import APIRouter, Depends, Path

from backend.app.hasn.schema.hasn_group_members import (
    CreateHasnGroupMembersParam,
    DeleteHasnGroupMembersParam,
    GetHasnGroupMembersDetail,
    UpdateHasnGroupMembersParam,
)
from backend.app.hasn.service.hasn_group_members_service import hasn_group_members_service
from backend.common.pagination import DependsPagination, PageData
from backend.common.response.response_schema import ResponseModel, ResponseSchemaModel, response_base
from backend.common.security.jwt import DependsJwtAuth
from backend.common.security.permission import RequestPermission
from backend.common.security.rbac import DependsRBAC
from backend.database.db import CurrentSession, CurrentSessionTransaction

router = APIRouter()


@router.get('/{pk}', summary='获取HASN 群成员详情', dependencies=[DependsJwtAuth], name='admin_get_hasn_group_members')
async def get_hasn_group_members(
    db: CurrentSession, pk: Annotated[int, Path(description='HASN 群成员 ID')]
) -> ResponseSchemaModel[GetHasnGroupMembersDetail]:
    hasn_group_members = await hasn_group_members_service.get(db=db, pk=pk)
    return response_base.success(data=hasn_group_members)


@router.get(
    '',
    summary='分页获取所有HASN 群成员',
    dependencies=[
        DependsJwtAuth,
        DependsPagination,
    ],
 name='admin_get_hasn_group_memberss_paginated')
async def get_hasn_group_memberss_paginated(db: CurrentSession) -> ResponseSchemaModel[PageData[GetHasnGroupMembersDetail]]:
    page_data = await hasn_group_members_service.get_list(db=db)
    return response_base.success(data=page_data)


@router.post(
    '',
    summary='创建HASN 群成员',
    dependencies=[
        Depends(RequestPermission('hasn:group:members:add')),
        DependsRBAC,
    ],
)
async def create_hasn_group_members(db: CurrentSessionTransaction, obj: CreateHasnGroupMembersParam) -> ResponseModel:
    await hasn_group_members_service.create(db=db, obj=obj)
    return response_base.success()


@router.put(
    '/{pk}',
    summary='更新HASN 群成员',
    dependencies=[
        Depends(RequestPermission('hasn:group:members:edit')),
        DependsRBAC,
    ],
)
async def update_hasn_group_members(
    db: CurrentSessionTransaction, pk: Annotated[int, Path(description='HASN 群成员 ID')], obj: UpdateHasnGroupMembersParam
) -> ResponseModel:
    count = await hasn_group_members_service.update(db=db, pk=pk, obj=obj)
    if count > 0:
        return response_base.success()
    return response_base.fail()


@router.delete(
    '',
    summary='批量删除HASN 群成员',
    dependencies=[
        Depends(RequestPermission('hasn:group:members:del')),
        DependsRBAC,
    ],
)
async def delete_hasn_group_memberss(db: CurrentSessionTransaction, obj: DeleteHasnGroupMembersParam) -> ResponseModel:
    count = await hasn_group_members_service.delete(db=db, obj=obj)
    if count > 0:
        return response_base.success()
    return response_base.fail()
