from typing import Annotated

from fastapi import APIRouter, Depends, Path

from backend.app.hasn_growth.schema.lead_contact import (
    CreateLeadContactParam,
    DeleteLeadContactParam,
    GetLeadContactDetail,
    UpdateLeadContactParam,
)
from backend.app.hasn_growth.service.lead_contact_service import lead_contact_service
from backend.common.pagination import DependsPagination, PageData
from backend.common.response.response_schema import ResponseModel, ResponseSchemaModel, response_base
from backend.common.security.jwt import DependsJwtAuth
from backend.common.security.permission import RequestPermission
from backend.common.security.rbac import DependsRBAC
from backend.database.db import CurrentSession, CurrentSessionTransaction

router = APIRouter()


@router.get('/{pk}', summary='获取Valid deduplicated lead contact详情', dependencies=[DependsJwtAuth], name='admin_get_lead_contact')
async def get_lead_contact(
    db: CurrentSession, pk: Annotated[int, Path(description='Valid deduplicated lead contact ID')]
) -> ResponseSchemaModel[GetLeadContactDetail]:
    lead_contact = await lead_contact_service.get(db=db, pk=pk)
    return response_base.success(data=lead_contact)


@router.get(
    '',
    summary='分页获取所有Valid deduplicated lead contact',
    dependencies=[
        DependsJwtAuth,
        DependsPagination,
    ],
 name='admin_get_lead_contacts_paginated')
async def get_lead_contacts_paginated(db: CurrentSession) -> ResponseSchemaModel[PageData[GetLeadContactDetail]]:
    page_data = await lead_contact_service.get_list(db=db)
    return response_base.success(data=page_data)


@router.post(
    '',
    summary='创建Valid deduplicated lead contact',
    dependencies=[
        Depends(RequestPermission('lead:contact:add')),
        DependsRBAC,
    ],
)
async def create_lead_contact(db: CurrentSessionTransaction, obj: CreateLeadContactParam) -> ResponseModel:
    await lead_contact_service.create(db=db, obj=obj)
    return response_base.success()


@router.put(
    '/{pk}',
    summary='更新Valid deduplicated lead contact',
    dependencies=[
        Depends(RequestPermission('lead:contact:edit')),
        DependsRBAC,
    ],
)
async def update_lead_contact(
    db: CurrentSessionTransaction, pk: Annotated[int, Path(description='Valid deduplicated lead contact ID')], obj: UpdateLeadContactParam
) -> ResponseModel:
    count = await lead_contact_service.update(db=db, pk=pk, obj=obj)
    if count > 0:
        return response_base.success()
    return response_base.fail()


@router.delete(
    '',
    summary='批量删除Valid deduplicated lead contact',
    dependencies=[
        Depends(RequestPermission('lead:contact:del')),
        DependsRBAC,
    ],
)
async def delete_lead_contacts(db: CurrentSessionTransaction, obj: DeleteLeadContactParam) -> ResponseModel:
    count = await lead_contact_service.delete(db=db, obj=obj)
    if count > 0:
        return response_base.success()
    return response_base.fail()
