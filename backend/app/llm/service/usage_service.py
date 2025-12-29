"""用量统计 Service"""

from datetime import date
from decimal import Decimal
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from backend.app.llm.core.rate_limiter import rate_limiter
from backend.app.llm.crud.crud_usage_log import usage_log_dao
from backend.app.llm.schema.usage_log import (
    DailyUsage,
    GetUsageLogDetail,
    GetUsageLogList,
    ModelUsage,
    QuotaInfo,
    UsageSummary,
)
from backend.common.pagination import paging_data


class UsageService:
    """用量统计服务"""

    @staticmethod
    async def get_summary(
        db: AsyncSession,
        *,
        user_id: int,
        start_date: date | None = None,
        end_date: date | None = None,
    ) -> UsageSummary:
        """获取用量汇总"""
        return await usage_log_dao.get_summary(
            db,
            user_id=user_id,
            start_date=start_date,
            end_date=end_date,
        )

    @staticmethod
    async def get_daily_usage(
        db: AsyncSession,
        *,
        user_id: int,
        days: int = 30,
    ) -> list[DailyUsage]:
        """获取每日用量"""
        return await usage_log_dao.get_daily_usage(db, user_id=user_id, days=days)

    @staticmethod
    async def get_model_usage(
        db: AsyncSession,
        *,
        user_id: int,
        start_date: date | None = None,
        end_date: date | None = None,
    ) -> list[ModelUsage]:
        """获取模型用量"""
        return await usage_log_dao.get_model_usage(
            db,
            user_id=user_id,
            start_date=start_date,
            end_date=end_date,
        )

    @staticmethod
    async def get_usage_logs(
        db: AsyncSession,
        *,
        user_id: int,
        api_key_id: int | None = None,
        model_name: str | None = None,
        status: str | None = None,
        start_date: date | None = None,
        end_date: date | None = None,
    ) -> dict[str, Any]:
        """获取用量日志列表（分页）"""
        stmt = await usage_log_dao.get_list(
            user_id=user_id,
            api_key_id=api_key_id,
            model_name=model_name,
            status=status,
            start_date=start_date,
            end_date=end_date,
        )
        page_data = await paging_data(db, stmt)
        return page_data

    @staticmethod
    async def get_quota_info(
        api_key_id: int,
        *,
        rpm_limit: int,
        daily_limit: int,
        monthly_limit: int,
    ) -> QuotaInfo:
        """获取配额信息"""
        usage_info = await rate_limiter.get_usage_info(
            api_key_id,
            rpm_limit=rpm_limit,
            daily_limit=daily_limit,
            monthly_limit=monthly_limit,
        )

        return QuotaInfo(
            daily_token_limit=usage_info['daily_token_limit'],
            daily_token_used=usage_info['daily_token_used'],
            daily_token_remaining=usage_info['daily_token_remaining'],
            monthly_token_limit=usage_info['monthly_token_limit'],
            monthly_token_used=usage_info['monthly_token_used'],
            monthly_token_remaining=usage_info['monthly_token_remaining'],
            rpm_limit=usage_info['rpm_limit'],
            current_rpm=usage_info['current_rpm'],
        )


usage_service = UsageService()
