from typing import Annotated

from fastapi import APIRouter, Depends, Path, Query

from backend.app.hasn_growth.schema.lead_contact_source import (
    CreateLeadContactSourceParam,
    DeleteLeadContactSourceParam,
    GetLeadContactSourceDetail,
    UpdateLeadContactSourceParam,
)
from backend.app.hasn_growth.service.lead_contact_source_service import lead_contact_source_service
from backend.common.pagination import DependsPagination, PageData
from backend.common.response.response_schema import ResponseModel, ResponseSchemaModel, response_base
from backend.common.security.jwt import DependsJwtAuth
from backend.common.security.permission import RequestPermission
from backend.common.security.rbac import DependsRBAC
from backend.database.db import CurrentSession, CurrentSessionTransaction

router = APIRouter()


@router.get('/{pk}', summary='获取Lead multi-source evidence详情', dependencies=[DependsJwtAuth], name='admin_get_lead_contact_source')
async def get_lead_contact_source(
    db: CurrentSession, pk: Annotated[int, Path(description='Lead multi-source evidence ID')]
) -> ResponseSchemaModel[GetLeadContactSourceDetail]:
    lead_contact_source = await lead_contact_source_service.get(db=db, pk=pk)
    return response_base.success(data=lead_contact_source)


@router.get(
    '',
    summary='分页获取所有Lead multi-source evidence',
    dependencies=[
        DependsJwtAuth,
        DependsPagination,
    ],
 name='admin_get_lead_contact_sources_paginated')
async def get_lead_contact_sources_paginated(db: CurrentSession) -> ResponseSchemaModel[PageData[GetLeadContactSourceDetail]]:
    page_data = await lead_contact_source_service.get_list(db=db)
    return response_base.success(data=page_data)


@router.post(
    '',
    summary='创建Lead multi-source evidence',
    dependencies=[
        Depends(RequestPermission('lead:contact:source:add')),
        DependsRBAC,
    ],
)
async def create_lead_contact_source(db: CurrentSessionTransaction, obj: CreateLeadContactSourceParam) -> ResponseModel:
    await lead_contact_source_service.create(db=db, obj=obj)
    return response_base.success()


@router.put(
    '/{pk}',
    summary='更新Lead multi-source evidence',
    dependencies=[
        Depends(RequestPermission('lead:contact:source:edit')),
        DependsRBAC,
    ],
)
async def update_lead_contact_source(
    db: CurrentSessionTransaction, pk: Annotated[int, Path(description='Lead multi-source evidence ID')], obj: UpdateLeadContactSourceParam
) -> ResponseModel:
    count = await lead_contact_source_service.update(db=db, pk=pk, obj=obj)
    if count > 0:
        return response_base.success()
    return response_base.fail()


@router.delete(
    '',
    summary='批量删除Lead multi-source evidence',
    dependencies=[
        Depends(RequestPermission('lead:contact:source:del')),
        DependsRBAC,
    ],
)
async def delete_lead_contact_sources(db: CurrentSessionTransaction, obj: DeleteLeadContactSourceParam) -> ResponseModel:
    count = await lead_contact_source_service.delete(db=db, obj=obj)
    if count > 0:
        return response_base.success()
    return response_base.fail()
