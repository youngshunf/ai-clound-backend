from typing import Annotated

from fastapi import APIRouter, Depends, Path

from backend.app.hasn.schema.hasn_contacts import (
    CreateHasnContactsParam,
    DeleteHasnContactsParam,
    GetHasnContactsDetail,
    UpdateHasnContactsParam,
)
from backend.app.hasn.service.hasn_contacts_service import hasn_contacts_service
from backend.common.pagination import DependsPagination, PageData
from backend.common.response.response_schema import ResponseModel, ResponseSchemaModel, response_base
from backend.common.security.jwt import DependsJwtAuth
from backend.common.security.permission import RequestPermission
from backend.common.security.rbac import DependsRBAC
from backend.database.db import CurrentSession, CurrentSessionTransaction

router = APIRouter()


@router.get('/{pk}', summary='获取HASN 联系人关系详情', dependencies=[DependsJwtAuth], name='admin_get_hasn_contacts')
async def get_hasn_contacts(
    db: CurrentSession, pk: Annotated[int, Path(description='HASN 联系人关系 ID')]
) -> ResponseSchemaModel[GetHasnContactsDetail]:
    hasn_contacts = await hasn_contacts_service.get(db=db, pk=pk)
    return response_base.success(data=hasn_contacts)


@router.get(
    '',
    summary='分页获取所有HASN 联系人关系',
    dependencies=[
        DependsJwtAuth,
        DependsPagination,
    ],
 name='admin_get_hasn_contactss_paginated')
async def get_hasn_contactss_paginated(db: CurrentSession) -> ResponseSchemaModel[PageData[GetHasnContactsDetail]]:
    page_data = await hasn_contacts_service.get_list(db=db)
    return response_base.success(data=page_data)


@router.post(
    '',
    summary='创建HASN 联系人关系',
    dependencies=[
        Depends(RequestPermission('hasn:contacts:add')),
        DependsRBAC,
    ],
)
async def create_hasn_contacts(db: CurrentSessionTransaction, obj: CreateHasnContactsParam) -> ResponseModel:
    await hasn_contacts_service.create(db=db, obj=obj)
    return response_base.success()


@router.put(
    '/{pk}',
    summary='更新HASN 联系人关系',
    dependencies=[
        Depends(RequestPermission('hasn:contacts:edit')),
        DependsRBAC,
    ],
)
async def update_hasn_contacts(
    db: CurrentSessionTransaction, pk: Annotated[int, Path(description='HASN 联系人关系 ID')], obj: UpdateHasnContactsParam
) -> ResponseModel:
    count = await hasn_contacts_service.update(db=db, pk=pk, obj=obj)
    if count > 0:
        return response_base.success()
    return response_base.fail()


@router.delete(
    '',
    summary='批量删除HASN 联系人关系',
    dependencies=[
        Depends(RequestPermission('hasn:contacts:del')),
        DependsRBAC,
    ],
)
async def delete_hasn_contactss(db: CurrentSessionTransaction, obj: DeleteHasnContactsParam) -> ResponseModel:
    count = await hasn_contacts_service.delete(db=db, obj=obj)
    if count > 0:
        return response_base.success()
    return response_base.fail()
