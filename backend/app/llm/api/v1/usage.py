"""用量统计 API"""

from datetime import date
from typing import Annotated

from fastapi import APIRouter, Header, Query, Request

from backend.app.llm.schema.usage_log import (
    DailyUsage,
    GetUsageLogList,
    ModelUsage,
    QuotaInfo,
    UsageSummary,
)
from backend.app.llm.service.api_key_service import api_key_service
from backend.app.llm.service.usage_service import usage_service
from backend.common.pagination import DependsPagination, PageData
from backend.common.response.response_schema import ResponseSchemaModel, response_base
from backend.common.security.jwt import DependsJwtAuth
from backend.database.db import CurrentSession

router = APIRouter()


@router.get(
    '/summary',
    summary='获取用量汇总',
    dependencies=[DependsJwtAuth],
)
async def get_usage_summary(
    request: Request,
    db: CurrentSession,
    start_date: Annotated[date | None, Query(description='开始日期')] = None,
    end_date: Annotated[date | None, Query(description='结束日期')] = None,
) -> ResponseSchemaModel[UsageSummary]:
    user_id = request.user.id
    data = await usage_service.get_summary(db, user_id=user_id, start_date=start_date, end_date=end_date)
    return response_base.success(data=data)


@router.get(
    '/daily',
    summary='获取每日用量',
    dependencies=[DependsJwtAuth],
)
async def get_daily_usage(
    request: Request,
    db: CurrentSession,
    days: Annotated[int, Query(description='天数', ge=1, le=365)] = 30,
) -> ResponseSchemaModel[list[DailyUsage]]:
    user_id = request.user.id
    data = await usage_service.get_daily_usage(db, user_id=user_id, days=days)
    return response_base.success(data=data)


@router.get(
    '/models',
    summary='获取模型用量',
    dependencies=[DependsJwtAuth],
)
async def get_model_usage(
    request: Request,
    db: CurrentSession,
    start_date: Annotated[date | None, Query(description='开始日期')] = None,
    end_date: Annotated[date | None, Query(description='结束日期')] = None,
) -> ResponseSchemaModel[list[ModelUsage]]:
    user_id = request.user.id
    data = await usage_service.get_model_usage(db, user_id=user_id, start_date=start_date, end_date=end_date)
    return response_base.success(data=data)


@router.get(
    '/logs',
    summary='获取用量日志',
    dependencies=[DependsJwtAuth, DependsPagination],
)
async def get_usage_logs(
    request: Request,
    db: CurrentSession,
    api_key_id: Annotated[int | None, Query(description='API Key ID')] = None,
    model_name: Annotated[str | None, Query(description='模型名称')] = None,
    status: Annotated[str | None, Query(description='状态')] = None,
    start_date: Annotated[date | None, Query(description='开始日期')] = None,
    end_date: Annotated[date | None, Query(description='结束日期')] = None,
) -> ResponseSchemaModel[PageData[GetUsageLogList]]:
    user_id = request.user.id
    page_data = await usage_service.get_usage_logs(
        db,
        user_id=user_id,
        api_key_id=api_key_id,
        model_name=model_name,
        status=status,
        start_date=start_date,
        end_date=end_date,
    )
    return response_base.success(data=page_data)


@router.get(
    '/quota',
    summary='获取配额信息',
    description='通过 API Key 获取当前配额使用情况',
)
async def get_quota_info(
    db: CurrentSession,
    authorization: str = Header(..., description='Bearer sk-xxx'),
) -> ResponseSchemaModel[QuotaInfo]:
    # 提取 API Key
    api_key = authorization.replace('Bearer ', '') if authorization.startswith('Bearer ') else authorization

    # 验证 API Key
    api_key_record = await api_key_service.verify_api_key(db, api_key)

    # 获取速率限制
    rate_limits = await api_key_service.get_rate_limits(db, api_key_record)

    # 获取配额信息
    data = await usage_service.get_quota_info(
        api_key_record.id,
        rpm_limit=rate_limits['rpm_limit'],
        daily_limit=rate_limits['daily_token_limit'],
        monthly_limit=rate_limits['monthly_token_limit'],
    )
    return response_base.success(data=data)
