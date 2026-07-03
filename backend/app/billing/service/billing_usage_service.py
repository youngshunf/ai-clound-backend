"""计费用量取数服务 —— new-api 为权威计量源（经 HTTP 管理 API，无 DB 直连）。

唤星的 LLM 网关是 new-api。真实的「可用额度」与「消耗」都在 new-api：
- `users.quota / used_quota`：账户总额度 / 已用额度（quota 微单位，÷ NEWAPI_QUOTA_PER_DOLLAR = 唤星积分）
- `/api/log`（type=2）：每次 LLM 调用的 prompt/completion tokens + quota 明细
- `/api/data`：按 model+hour 聚合（token_used=prompt+completion 合计、quota、count）

可用积分 = (quota − used_quota)/RATE 必须实时取 new-api（§5A D7：展示用实时 new-api），
不能读内部 user_credit_balance（账本每小时才回扣，会和真实消耗有滞后）。

无 new-api 映射的遗留用户 / new-api 不可达 → 返回 None/空/0，由调用方如实回退，绝不伪造（零 fake）。

2026-06-15 解耦：`newapi_async_db_session + newapi_direct_dao` → `newapi_admin_client`。

@author Ysf
"""

from datetime import date, datetime
from decimal import Decimal
from zoneinfo import ZoneInfo

from sqlalchemy.ext.asyncio import AsyncSession

from backend.app.newapi.client import NewApiError, newapi_admin_client
from backend.app.newapi.crud import llm_newapi_user_mapping_dao
from backend.app.newapi.service import resolve_newapi_username
from backend.common.log import log
from backend.core.conf import settings

_SHANGHAI_TZ = ZoneInfo('Asia/Shanghai')


def quota_to_credits(quota: int | None) -> Decimal:
    """new-api quota → 唤星积分（与 credits_to_quota 互逆，保留两位小数）。"""
    rate = settings.NEWAPI_QUOTA_PER_DOLLAR or 1
    return (Decimal(int(quota or 0)) / Decimal(rate)).quantize(Decimal('0.01'))


def _to_unix(value: datetime) -> int:
    """tz-aware/naive datetime → Unix 秒（new-api 时间口径）。"""
    return int(value.timestamp())


class BillingUsageService:
    """new-api 权威的可用积分 / 消耗取数（唤星映射表 + new-api 管理 API）。"""

    @staticmethod
    async def get_available_credits(
        db: AsyncSession,
        user_id: int,
        app_code: str = 'huanxing',
    ) -> dict | None:
        """实时可用积分（new-api 权威）。无映射或 new-api 无此用户/不可达 → None。

        返回 {available_credits, total_credits, used_credits, request_count}（积分为 Decimal）。
        """
        mapping = await llm_newapi_user_mapping_dao.get_by_user(db, user_id, app_code)
        if not mapping:
            return None
        try:
            quota = await newapi_admin_client.get_user_quota(mapping.newapi_user_id)
        except NewApiError as exc:
            # new-api 不可达 → 降级让调用方回退（不 500 整个订阅页），如实记录
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
        """计费周期内的真实消耗（new-api /api/data 聚合）。无映射/不可达 → 全 0。

        返回 {consumed_credits(Decimal), request_count, token_count}。
        """
        zero = {'consumed_credits': Decimal(0), 'request_count': 0, 'token_count': 0}
        mapping = await llm_newapi_user_mapping_dao.get_by_user(db, user_id, app_code)
        if not mapping:
            return zero
        username = await resolve_newapi_username(mapping)
        try:
            rows = await newapi_admin_client.get_quota_data(
                username=username, start_timestamp=_to_unix(start), end_timestamp=_to_unix(end)
            )
        except NewApiError as exc:
            log.warning(f'[BillingUsage] new-api 周期消耗读取失败，降级为 0: user_id={user_id}, error={exc!r}')
            return zero
        quota_sum = sum(int(r.get('quota') or 0) for r in rows)
        token_sum = sum(int(r.get('token_used') or 0) for r in rows)
        req_sum = sum(int(r.get('count') or 0) for r in rows)
        return {
            'consumed_credits': quota_to_credits(quota_sum),
            'request_count': req_sum,
            'token_count': token_sum,
        }

    @staticmethod
    async def get_daily_consumed(
        db: AsyncSession,
        user_id: int,
        start: datetime,
        end: datetime,
        app_code: str = 'huanxing',
    ) -> dict[date, dict]:
        """按本地日（Asia/Shanghai）聚合的真实消耗（new-api /api/data）。无映射/不可达 → 空 dict。

        /api/data 行是 model+hour 桶（created_at 已 hour 对齐）；按本地日归并。
        返回 {day(date): {consumed_credits(Decimal,≤0), request_count, token_count}}。
        消耗以**负数**返回，便于与内部发放/购买（正数）按日合并。
        """
        mapping = await llm_newapi_user_mapping_dao.get_by_user(db, user_id, app_code)
        if not mapping:
            return {}
        username = await resolve_newapi_username(mapping)
        try:
            rows = await newapi_admin_client.get_quota_data(
                username=username, start_timestamp=_to_unix(start), end_timestamp=_to_unix(end)
            )
        except NewApiError as exc:
            log.warning(f'[BillingUsage] new-api 按日消耗读取失败，降级为空: user_id={user_id}, error={exc!r}')
            return {}
        # 按本地日累加 hour 桶
        acc: dict[date, dict] = {}
        for row in rows:
            ts = int(row.get('created_at') or 0)
            day = datetime.fromtimestamp(ts, tz=_SHANGHAI_TZ).date()
            bucket = acc.setdefault(day, {'quota': 0, 'token_used': 0, 'count': 0})
            bucket['quota'] += int(row.get('quota') or 0)
            bucket['token_used'] += int(row.get('token_used') or 0)
            bucket['count'] += int(row.get('count') or 0)
        return {
            day: {
                'consumed_credits': -quota_to_credits(b['quota']),
                'request_count': b['count'],
                'token_count': b['token_used'],
            }
            for day, b in acc.items()
        }


billing_usage_service = BillingUsageService()
