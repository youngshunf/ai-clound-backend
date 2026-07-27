from typing import Annotated

from fastapi import APIRouter, Path

from backend.app.hasn.schema.hasn_sync_inbox_events import (
    GetHasnSyncInboxEventsDetail,
)
from backend.app.hasn.service.hasn_sync_inbox_events_service import hasn_sync_inbox_events_service
from backend.common.pagination import DependsPagination, PageData
from backend.common.response.response_schema import ResponseSchemaModel, response_base
from backend.common.security.jwt import DependsJwtAuth
from backend.database.db import CurrentSyncSession

router = APIRouter()


@router.get('/{pk}', summary='获取HASN 客户端上行 outbox 幂等/冲突详情', dependencies=[DependsJwtAuth], name='admin_get_hasn_sync_inbox_events')
async def get_hasn_sync_inbox_events(
    db: CurrentSyncSession, pk: Annotated[int, Path(description='HASN 客户端上行 outbox 幂等/冲突 ID')]
) -> ResponseSchemaModel[GetHasnSyncInboxEventsDetail]:
    hasn_sync_inbox_events = await hasn_sync_inbox_events_service.get(db=db, pk=pk)
    return response_base.success(data=hasn_sync_inbox_events)


@router.get(
    '',
    summary='分页获取所有HASN 客户端上行 outbox 幂等/冲突',
    dependencies=[
        DependsJwtAuth,
        DependsPagination,
    ],
 name='admin_get_hasn_sync_inbox_eventss_paginated')
async def get_hasn_sync_inbox_eventss_paginated(
    db: CurrentSyncSession,
) -> ResponseSchemaModel[PageData[GetHasnSyncInboxEventsDetail]]:
    page_data = await hasn_sync_inbox_events_service.get_list(db=db)
    return response_base.success(data=page_data)
