from typing import Annotated

from fastapi import APIRouter, Depends, Path

from backend.app.hasn_growth.schema.lead_firecrawl_request import (
    CreateLeadFirecrawlRequestParam,
    DeleteLeadFirecrawlRequestParam,
    GetLeadFirecrawlRequestDetail,
    UpdateLeadFirecrawlRequestParam,
)
from backend.app.hasn_growth.service.lead_firecrawl_request_service import lead_firecrawl_request_service
from backend.common.pagination import DependsPagination, PageData
from backend.common.response.response_schema import ResponseModel, ResponseSchemaModel, response_base
from backend.common.security.jwt import DependsJwtAuth
from backend.common.security.permission import RequestPermission
from backend.common.security.rbac import DependsRBAC
from backend.database.db import CurrentSession, CurrentSessionTransaction

router = APIRouter()


@router.get('/{pk}', summary='获取Firecrawl request audit for AI lead automation详情', dependencies=[DependsJwtAuth], name='admin_get_lead_firecrawl_request')
async def get_lead_firecrawl_request(
    db: CurrentSession, pk: Annotated[int, Path(description='Firecrawl request audit for AI lead automation ID')]
) -> ResponseSchemaModel[GetLeadFirecrawlRequestDetail]:
    lead_firecrawl_request = await lead_firecrawl_request_service.get(db=db, pk=pk)
    return response_base.success(data=lead_firecrawl_request)


@router.get(
    '',
    summary='分页获取所有Firecrawl request audit for AI lead automation',
    dependencies=[
        DependsJwtAuth,
        DependsPagination,
    ],
 name='admin_get_lead_firecrawl_requests_paginated')
async def get_lead_firecrawl_requests_paginated(db: CurrentSession) -> ResponseSchemaModel[PageData[GetLeadFirecrawlRequestDetail]]:
    page_data = await lead_firecrawl_request_service.get_list(db=db)
    return response_base.success(data=page_data)


@router.post(
    '',
    summary='创建Firecrawl request audit for AI lead automation',
    dependencies=[
        Depends(RequestPermission('lead:firecrawl:request:add')),
        DependsRBAC,
    ],
)
async def create_lead_firecrawl_request(db: CurrentSessionTransaction, obj: CreateLeadFirecrawlRequestParam) -> ResponseModel:
    await lead_firecrawl_request_service.create(db=db, obj=obj)
    return response_base.success()


@router.put(
    '/{pk}',
    summary='更新Firecrawl request audit for AI lead automation',
    dependencies=[
        Depends(RequestPermission('lead:firecrawl:request:edit')),
        DependsRBAC,
    ],
)
async def update_lead_firecrawl_request(
    db: CurrentSessionTransaction, pk: Annotated[int, Path(description='Firecrawl request audit for AI lead automation ID')], obj: UpdateLeadFirecrawlRequestParam
) -> ResponseModel:
    count = await lead_firecrawl_request_service.update(db=db, pk=pk, obj=obj)
    if count > 0:
        return response_base.success()
    return response_base.fail()


@router.delete(
    '',
    summary='批量删除Firecrawl request audit for AI lead automation',
    dependencies=[
        Depends(RequestPermission('lead:firecrawl:request:del')),
        DependsRBAC,
    ],
)
async def delete_lead_firecrawl_requests(db: CurrentSessionTransaction, obj: DeleteLeadFirecrawlRequestParam) -> ResponseModel:
    count = await lead_firecrawl_request_service.delete(db=db, obj=obj)
    if count > 0:
        return response_base.success()
    return response_base.fail()
