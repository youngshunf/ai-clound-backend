from typing import Annotated

from fastapi import APIRouter, Query

from backend.app.huanxing.service.analytics_service import analytics_service
from backend.common.log import log
from backend.common.response.response_schema import response_base
from backend.common.security.jwt import DependsJwtAuth
from backend.database.db import CurrentSession

router = APIRouter()


@router.get(
    '',
    summary='分析看板数据',
    description='返回唤星平台的综合分析数据',
    dependencies=[DependsJwtAuth],
 name='admin_get_analytics')
async def get_analytics(
    db: CurrentSession,
    days: Annotated[int, Query(description='趋势图天数', ge=7, le=90)] = 30,
):
    try:
        log.info(f'Analytics API called with days={days}')
        data = await analytics_service.get_analytics(db=db, days=days)
        log.info(f'Analytics data OK: {len(data)} keys')
        return response_base.success(data=data)
    except Exception as e:
        log.error(f'Analytics error: {type(e).__name__}: {e}')
        import traceback
        log.error(traceback.format_exc())
        raise
