from typing import Annotated

from fastapi import APIRouter, Path

from backend.app.hasn.schema.hasn_contact_requests import (
    GetHasnContactRequestsDetail,
)
from backend.app.hasn.service.hasn_contact_requests_service import hasn_contact_requests_service
from backend.common.pagination import DependsPagination, PageData
from backend.common.response.response_schema import ResponseSchemaModel, response_base
from backend.common.security.jwt import DependsJwtAuth
from backend.database.db import CurrentImSession

router = APIRouter()


@router.get(
    '/{pk}',
    summary='获取HASN 好友请求表（请求生命周期独立于 hasn_contacts 关系表）详情',
    dependencies=[DependsJwtAuth],
    name='admin_get_hasn_contact_requests',
)
async def get_hasn_contact_requests(
    db: CurrentImSession,
    pk: Annotated[int, Path(description='HASN 好友请求表（请求生命周期独立于 hasn_contacts 关系表） ID')],
) -> ResponseSchemaModel[GetHasnContactRequestsDetail]:
    hasn_contact_requests = await hasn_contact_requests_service.get(db=db, pk=pk)
    return response_base.success(data=hasn_contact_requests)


@router.get(
    '',
    summary='分页获取所有HASN 好友请求表（请求生命周期独立于 hasn_contacts 关系表）',
    dependencies=[
        DependsJwtAuth,
        DependsPagination,
    ],
    name='admin_get_hasn_contact_requests_paginated',
)
async def get_hasn_contact_requests_paginated(
    db: CurrentImSession,
) -> ResponseSchemaModel[PageData[GetHasnContactRequestsDetail]]:
    page_data = await hasn_contact_requests_service.get_list(db=db)
    return response_base.success(data=page_data)
