from typing import Annotated

from fastapi import APIRouter, Path

from backend.app.hasn.schema.hasn_contacts import (
    GetHasnContactsDetail,
)
from backend.app.hasn.service.hasn_contacts_service import hasn_contacts_service
from backend.common.pagination import DependsPagination, PageData
from backend.common.response.response_schema import ResponseSchemaModel, response_base
from backend.common.security.jwt import DependsJwtAuth
from backend.database.db import CurrentImSession

router = APIRouter()


@router.get('/{pk}', summary='获取HASN 联系人关系详情', dependencies=[DependsJwtAuth], name='admin_get_hasn_contacts')
async def get_hasn_contacts(
    db: CurrentImSession, pk: Annotated[int, Path(description='HASN 联系人关系 ID')]
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
async def get_hasn_contactss_paginated(
    db: CurrentImSession,
) -> ResponseSchemaModel[PageData[GetHasnContactsDetail]]:
    page_data = await hasn_contacts_service.get_list(db=db)
    return response_base.success(data=page_data)
