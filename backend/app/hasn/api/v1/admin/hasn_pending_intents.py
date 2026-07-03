from typing import Annotated

from fastapi import APIRouter, Depends, Path

from backend.app.hasn.schema.hasn_pending_intents import (
    CreateHasnPendingIntentsParam,
    DeleteHasnPendingIntentsParam,
    GetHasnPendingIntentsDetail,
    UpdateHasnPendingIntentsParam,
)
from backend.app.hasn.service.hasn_pending_intents_service import hasn_pending_intents_service
from backend.common.pagination import DependsPagination, PageData
from backend.common.response.response_schema import ResponseModel, ResponseSchemaModel, response_base
from backend.common.security.jwt import DependsJwtAuth
from backend.common.security.permission import RequestPermission
from backend.common.security.rbac import DependsRBAC
from backend.database.db import CurrentSession, CurrentSessionTransaction

router = APIRouter()


@router.get('/{pk}', summary='获取HASN 第三方渠道反向 onboarding pending intent 详情', dependencies=[DependsJwtAuth], name='admin_get_hasn_pending_intents')
async def get_hasn_pending_intents(
    db: CurrentSession, pk: Annotated[int, Path(description='HASN 第三方渠道反向 onboarding pending intent  ID')]
) -> ResponseSchemaModel[GetHasnPendingIntentsDetail]:
    hasn_pending_intents = await hasn_pending_intents_service.get(db=db, pk=pk)
    return response_base.success(data=hasn_pending_intents)


@router.get(
    '',
    summary='分页获取所有HASN 第三方渠道反向 onboarding pending intent ',
    dependencies=[
        DependsJwtAuth,
        DependsPagination,
    ],
 name='admin_get_hasn_pending_intentss_paginated')
async def get_hasn_pending_intentss_paginated(db: CurrentSession) -> ResponseSchemaModel[PageData[GetHasnPendingIntentsDetail]]:
    page_data = await hasn_pending_intents_service.get_list(db=db)
    return response_base.success(data=page_data)


@router.post(
    '',
    summary='创建HASN 第三方渠道反向 onboarding pending intent ',
    dependencies=[
        Depends(RequestPermission('hasn:pending:intents:add')),
        DependsRBAC,
    ],
)
async def create_hasn_pending_intents(db: CurrentSessionTransaction, obj: CreateHasnPendingIntentsParam) -> ResponseModel:
    await hasn_pending_intents_service.create(db=db, obj=obj)
    return response_base.success()


@router.put(
    '/{pk}',
    summary='更新HASN 第三方渠道反向 onboarding pending intent ',
    dependencies=[
        Depends(RequestPermission('hasn:pending:intents:edit')),
        DependsRBAC,
    ],
)
async def update_hasn_pending_intents(
    db: CurrentSessionTransaction, pk: Annotated[int, Path(description='HASN 第三方渠道反向 onboarding pending intent  ID')], obj: UpdateHasnPendingIntentsParam
) -> ResponseModel:
    count = await hasn_pending_intents_service.update(db=db, pk=pk, obj=obj)
    if count > 0:
        return response_base.success()
    return response_base.fail()


@router.delete(
    '',
    summary='批量删除HASN 第三方渠道反向 onboarding pending intent ',
    dependencies=[
        Depends(RequestPermission('hasn:pending:intents:del')),
        DependsRBAC,
    ],
)
async def delete_hasn_pending_intentss(db: CurrentSessionTransaction, obj: DeleteHasnPendingIntentsParam) -> ResponseModel:
    count = await hasn_pending_intents_service.delete(db=db, obj=obj)
    if count > 0:
        return response_base.success()
    return response_base.fail()
