"""Admin API — 回调日志"""

from typing import Annotated

from fastapi import APIRouter, Query

from backend.app.billing.crud.crud_pay_notify_log import pay_notify_log_dao
from backend.app.billing.schema.pay_notify_log import GetPayNotifyLogDetail
from backend.common.pagination import DependsPagination, PageData, paging_data
from backend.common.response.response_schema import ResponseSchemaModel, response_base
from backend.common.security.jwt import DependsJwtAuth
from backend.database.db import CurrentSession

router = APIRouter()


@router.get(
    '',
    summary='分页获取回调日志',
    dependencies=[DependsJwtAuth, DependsPagination],
 name='admin_get_notify_logs_paginated')
async def get_notify_logs_paginated(
    db: CurrentSession,
    order_no: Annotated[str | None, Query(description='按订单号筛选')] = None,
    channel_code: Annotated[str | None, Query(description='按渠道筛选')] = None,
) -> ResponseSchemaModel[PageData[GetPayNotifyLogDetail]]:
    select_stmt = await pay_notify_log_dao.get_select(order_no=order_no, channel_code=channel_code)
    page_data = await paging_data(db, select_stmt)
    return response_base.success(data=page_data)
