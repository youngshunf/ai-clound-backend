from typing import Annotated

from fastapi import APIRouter, Path

from backend.app.hasn.schema.hasn_sync_events import (
    GetHasnSyncEventsDetail,
)
from backend.app.hasn.service.hasn_sync_events_service import hasn_sync_events_service
from backend.common.pagination import DependsPagination, PageData
from backend.common.response.response_schema import ResponseSchemaModel, response_base
from backend.common.security.jwt import DependsJwtAuth
from backend.database.db import CurrentSyncSession

router = APIRouter()


@router.get('/{pk}', summary='获取HASN 服务端下行同步事件详情', dependencies=[DependsJwtAuth], name='admin_get_hasn_sync_events')
async def get_hasn_sync_events(
    db: CurrentSyncSession, pk: Annotated[int, Path(description='HASN 服务端下行同步事件 ID')]
) -> ResponseSchemaModel[GetHasnSyncEventsDetail]:
    hasn_sync_events = await hasn_sync_events_service.get(db=db, pk=pk)
    return response_base.success(data=hasn_sync_events)


@router.get(
    '',
    summary='分页获取所有HASN 服务端下行同步事件',
    dependencies=[
        DependsJwtAuth,
        DependsPagination,
    ],
 name='admin_get_hasn_sync_eventss_paginated')
async def get_hasn_sync_eventss_paginated(
    db: CurrentSyncSession,
) -> ResponseSchemaModel[PageData[GetHasnSyncEventsDetail]]:
    page_data = await hasn_sync_events_service.get_list(db=db)
    return response_base.success(data=page_data)
