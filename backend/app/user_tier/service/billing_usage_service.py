"""计费用量取数服务 —— new-api 为权威计量源。

唤星的 LLM 网关是 new-api（非内置 litellm）。真实的「可用额度」与「消耗」都在 new-api 库：
- `users.quota / used_quota`：账户总额度 / 已用额度（quota 单位，÷ NEWAPI_CREDITS_TO_QUOTA_RATE = 唤星积分）
- `logs`（type=2）：每次 LLM 调用的 prompt/completion tokens + quota 消耗明细

本服务把 new-api 的 quota 单位换算回唤星积分，供订阅概览「可用积分 / 本月已用」与
积分流水「按日消耗」读取。**唤星侧只把套餐额度下推到 new-api（quota），消耗由 new-api 记账**，
因此可用积分 = (quota − used_quota)/RATE 必须实时取 new-api，不能读内部 user_credit_balance
（那套内部账本不被 new-api 消耗扣减、会和真实消耗脱节）。

无 new-api 映射的遗留用户 → 返回 None/空，由调用方如实回退/置零，绝不伪造（零 fake）。

@author Ysf
"""

from datetime import date, datetime
from decimal import Decimal

from sqlalchemy.ext.asyncio import AsyncSession

from backend.app.llm.crud.crud_llm_newapi_user_mapping import (
    llm_newapi_user_mapping_dao,
    newapi_direct_dao,
)
from backend.common.log import log
from backend.core.conf import settings
from backend.database.db import newapi_async_db_session


def quota_to_credits(quota: int | None) -> Decimal:
    """new-api quota → 唤星积分（与 credits_to_quota 互逆，保留两位小数）。"""
    rate = settings.NEWAPI_CREDITS_TO_QUOTA_RATE or 1
    return (Decimal(int(quota or 0)) / Decimal(rate)).quantize(Decimal('0.01'))


def _to_unix(value: datetime) -> int:
    """tz-aware/naive datetime → Unix 秒（new-api logs.created_at 口径）。"""
    return int(value.timestamp())


class BillingUsageService:
    """new-api 权威的可用积分 / 消耗取数（跨库：唤星映射表 + new-api 库）。"""

    @staticmethod
    async def get_available_credits(
        db: AsyncSession,
        user_id: int,
        app_code: str = 'huanxing',
    ) -> dict | None:
        """实时可用积分（new-api 权威）。无映射或 new-api 无此用户 → None。

        返回 {available_credits, total_credits, used_credits, request_count}（积分为 Decimal）。
        """
        mapping = await llm_newapi_user_mapping_dao.get_by_user(db, user_id, app_code)
        if not mapping:
            return None
        try:
            async with newapi_async_db_session() as newapi_db:
                quota = await newapi_direct_dao.get_newapi_user_quota(newapi_db, mapping.newapi_user_id)
        except Exception as exc:  # noqa: BLE001
            # new-api 库不可达 → 降级让调用方回退内部账本（不 500 整个订阅页），如实记录
            log.warning(f'[BillingUsage] new-api quota 读取失败，降级: user_id={user_id}, error={exc!r}')
            return None
        if not quota:
            return None
        total = int(quota['quota'] or 0)
        used = int(quota['used_quota'] or 0)
        return {
            'available_credits': quota_to_credits(max(total - used, 0)),
            'total_credits': quota_to_credits(total),
            'used_credits': quota_to_credits(used),
            'request_count': int(quota['request_count'] or 0),
        }

    @staticmethod
    async def get_cycle_consumed(
        db: AsyncSession,
        user_id: int,
        start: datetime,
        end: datetime,
        app_code: str = 'huanxing',
    ) -> dict:
        """计费周期内的真实消耗（new-api logs）。无映射 → 全 0。

        返回 {consumed_credits(Decimal), request_count, token_count}。
        """
        mapping = await llm_newapi_user_mapping_dao.get_by_user(db, user_id, app_code)
        if not mapping:
            return {'consumed_credits': Decimal('0'), 'request_count': 0, 'token_count': 0}
        try:
            async with newapi_async_db_session() as newapi_db:
                total = await newapi_direct_dao.get_consumption_total(
                    newapi_db, mapping.newapi_user_id, _to_unix(start), _to_unix(end),
                )
        except Exception as exc:  # noqa: BLE001
            log.warning(f'[BillingUsage] new-api 周期消耗读取失败，降级为 0: user_id={user_id}, error={exc!r}')
            return {'consumed_credits': Decimal('0'), 'request_count': 0, 'token_count': 0}
        return {
            'consumed_credits': quota_to_credits(total['quota']),
            'request_count': total['request_count'],
            'token_count': total['prompt_tokens'] + total['completion_tokens'],
        }

    @staticmethod
    async def get_daily_consumed(
        db: AsyncSession,
        user_id: int,
        start: datetime,
        end: datetime,
        app_code: str = 'huanxing',
    ) -> dict[date, dict]:
        """按本地日聚合的真实消耗（new-api logs）。无映射 → 空 dict。

        返回 {day(date): {consumed_credits(Decimal,≤0), request_count, token_count}}。
        消耗以**负数**返回，便于与内部发放/购买（正数）按日合并。
        """
        mapping = await llm_newapi_user_mapping_dao.get_by_user(db, user_id, app_code)
        if not mapping:
            return {}
        try:
            async with newapi_async_db_session() as newapi_db:
                rows = await newapi_direct_dao.get_daily_consumption(
                    newapi_db, mapping.newapi_user_id, _to_unix(start), _to_unix(end),
                )
        except Exception as exc:  # noqa: BLE001
            log.warning(f'[BillingUsage] new-api 按日消耗读取失败，降级为空: user_id={user_id}, error={exc!r}')
            return {}
        result: dict[date, dict] = {}
        for row in rows:
            result[row['day']] = {
                'consumed_credits': -quota_to_credits(row['quota']),
                'request_count': row['request_count'],
                'token_count': row['prompt_tokens'] + row['completion_tokens'],
            }
        return result


billing_usage_service = BillingUsageService()
