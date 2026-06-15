"""LLM 用量汇总（new-api 权威，Owner JWT）。

解耦后 `/api/v1/llm/usage/summary` 由 app/newapi 接管（原 `app/llm/api/v1/usage.py` 随网关删除）。
- owner 只见自己；超管可指定 user_id（None=全部）。
- 数据经 new-api `/log` 汇总（无 DB 直连）；无映射 / 不可达 → 全 0（如实回退，零 fake）。

daemon `domains/billing` 经 owner 通道代理此端点（`GET /api/v1/billing/usage/summary` → 此处）。
"""

from datetime import date, datetime, time, timedelta
from typing import Annotated

from fastapi import APIRouter, Query, Request

from backend.app.newapi.crud import llm_newapi_user_mapping_dao
from backend.app.newapi.service import resolve_newapi_username, summarize_usage
from backend.common.response.response_schema import ResponseModel, response_base
from backend.common.security.jwt import DependsJwtAuth
from backend.database.db import CurrentSession
from backend.utils.timezone import timezone

router = APIRouter()

_DEFAULT_WINDOW_DAYS = 30


def _to_unix(d: date, *, end: bool) -> int:
    """date → Unix 秒（end=True 取当日 23:59:59，否则 00:00:00）。"""
    return int(datetime.combine(d, time.max if end else time.min).timestamp())


def _zero_summary() -> dict:
    return {
        'total_requests': 0,
        'success_requests': 0,
        'error_requests': 0,
        'total_tokens': 0,
        'total_input_tokens': 0,
        'total_output_tokens': 0,
        'total_cost': 0,
        'avg_latency_ms': 0,
    }


@router.get('/summary', summary='获取用量汇总（new-api 权威）', dependencies=[DependsJwtAuth])
async def get_usage_summary(
    request: Request,
    db: CurrentSession,
    user_id: Annotated[int | None, Query(description='用户 ID (超管可指定，None=全部)')] = None,
    start_date: Annotated[date | None, Query(description='开始日期')] = None,
    end_date: Annotated[date | None, Query(description='结束日期')] = None,
) -> ResponseModel:
    # 超管可查指定用户/全部；普通用户只能查自己
    query_user_id = user_id if request.user.is_superuser else request.user.id

    end_dt = end_date or timezone.now().date()
    start_dt = start_date or (end_dt - timedelta(days=_DEFAULT_WINDOW_DAYS))
    start_ts = _to_unix(start_dt, end=False)
    end_ts = _to_unix(end_dt, end=True)

    if query_user_id is None:
        username = None  # 超管全量
    else:
        mapping = await llm_newapi_user_mapping_dao.get_by_user(db, query_user_id, 'huanxing')
        if not mapping:
            return response_base.success(data=_zero_summary())
        username = await resolve_newapi_username(mapping)

    data = await summarize_usage(username, start_ts, end_ts)
    return response_base.success(data=data)
