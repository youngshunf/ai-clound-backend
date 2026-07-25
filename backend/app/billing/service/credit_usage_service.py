"""积分流水读服务（doc94 D1）：消费只有 NewAPI 一个来源。

**改造前**：云端维护 `credit_transaction` 流水表，每小时把 NewAPI 的用量同步过来记一笔，
并用自己的换算常量把 quota 换算成积分。于是「这次调用扣了多少积分」
在两侧各有一套算法与一份数据，两者一旦不一致，用户看到的流水就和真实扣费对不上。

**改造后**：

- **消费**从 NewAPI 读，金额由 NewAPI 换算成积分字符串，云端**原样透传**，不做任何算术；
- **入账**（订阅发放、积分包到账、赠送）从云端自己的履约事件 `credit_grant_event` 读——
  那是云端权威的「发了什么」，不是余额表；
- NewAPI 读不到时如实返回 `usage_status='unavailable'`，**不回落旧值、不伪造空列表当作「没有消费」**。
  把「读不到」显示成「没花钱」，比显示错误更糟。

日边界统一按 Asia/Shanghai，并把时区偏移交给 NewAPI 计算，避免两侧各切各的日。
"""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from typing import Any
from zoneinfo import ZoneInfo

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.app.billing.model.credit_grant_event import CreditGrantEvent
from backend.app.billing.observability.metrics import BILLING_CREDIT_ACCOUNT_UNAVAILABLE_TOTAL
from backend.app.newapi.credit_client import NewApiCreditError, newapi_credit_client
from backend.common.log import log

_SHANGHAI_TZ = ZoneInfo('Asia/Shanghai')

#: 展示时区相对 UTC 的分钟偏移；交给 NewAPI 做日切分。
SHANGHAI_TZ_OFFSET_MINUTES = 480

USAGE_STATUS_OK = 'ok'
USAGE_STATUS_UNAVAILABLE = 'unavailable'
USAGE_STATUS_UNMAPPED = 'unmapped'


async def _newapi_user_id(db: AsyncSession, user_id: int, app_code: str) -> int | None:
    from backend.app.newapi.crud import llm_newapi_user_mapping_dao

    mapping = await llm_newapi_user_mapping_dao.get_by_user(db, user_id, app_code)
    if not mapping or not mapping.newapi_user_id:
        return None
    return int(mapping.newapi_user_id)


class CreditUsageService:
    """消费流水与日聚合的读侧。"""

    @staticmethod
    async def list_usage(
        db: AsyncSession,
        user_id: int,
        *,
        page: int = 1,
        size: int = 20,
        app_code: str = 'huanxing',
    ) -> dict[str, Any]:
        """分页读消费流水（NewAPI 权威，云端透传）。"""
        newapi_user_id = await _newapi_user_id(db, user_id, app_code)
        if newapi_user_id is None:
            # 尚未开通 NewAPI 账户：这是「没有账户」，不是「没有消费」。
            return {'usage_status': USAGE_STATUS_UNMAPPED, 'items': [], 'total': 0, 'page': page, 'size': size, 'measured_at': None}

        try:
            payload = await newapi_credit_client.get_usage_page(newapi_user_id, page=page, size=size)
        except NewApiCreditError as exc:
            BILLING_CREDIT_ACCOUNT_UNAVAILABLE_TOTAL.inc()
            log.warning(f'[CreditUsage] 流水读取失败 user_id={user_id}: {exc}')
            return {
                'usage_status': USAGE_STATUS_UNAVAILABLE,
                'items': [],
                'total': 0,
                'page': page,
                'size': size,
                'measured_at': None,
                'unavailable_reason': str(exc),
            }

        return {
            'usage_status': USAGE_STATUS_OK,
            'items': payload.get('items') or [],
            'total': int(payload.get('total') or 0),
            'page': int(payload.get('page') or page),
            'size': int(payload.get('size') or size),
            'measured_at': payload.get('measured_at'),
        }

    @staticmethod
    async def daily_flow(
        db: AsyncSession,
        user_id: int,
        *,
        page: int = 1,
        size: int = 20,
        app_code: str = 'huanxing',
    ) -> dict[str, Any]:
        """按本地日聚合的流水：消费取 NewAPI，入账取云端履约事件。

        两边都可能为空，但「NewAPI 读不到」与「当天没消费」必须能区分——
        前者会把 ``usage_status`` 置为 ``unavailable``。
        """
        newapi_user_id = await _newapi_user_id(db, user_id, app_code)

        usage_status = USAGE_STATUS_OK
        measured_at: str | None = None
        consumed_by_day: dict[str, dict[str, Any]] = {}

        if newapi_user_id is None:
            usage_status = USAGE_STATUS_UNMAPPED
        else:
            try:
                payload = await newapi_credit_client.get_usage_daily(
                    newapi_user_id, tz_offset_minutes=SHANGHAI_TZ_OFFSET_MINUTES
                )
                measured_at = payload.get('measured_at')
                for row in payload.get('items') or []:
                    consumed_by_day[str(row.get('day'))] = row
            except NewApiCreditError as exc:
                BILLING_CREDIT_ACCOUNT_UNAVAILABLE_TOTAL.inc()
                log.warning(f'[CreditUsage] 日聚合读取失败 user_id={user_id}: {exc}')
                usage_status = USAGE_STATUS_UNAVAILABLE

        granted_by_day = await CreditUsageService._granted_by_day(db, user_id, app_code)

        merged: dict[str, dict[str, Any]] = {}
        for day in set(consumed_by_day) | set(granted_by_day):
            consumed_row = consumed_by_day.get(day) or {}
            # NewAPI 的消耗是正数；展示口径用负数表示「花掉了」。
            consumed = -Decimal(str(consumed_row.get('consumed_credits') or 0))
            granted = granted_by_day.get(day, Decimal(0))
            request_count = int(consumed_row.get('request_count') or 0)
            merged[day] = {
                'date': day,
                'consumed': consumed,
                'granted': granted,
                'net': granted + consumed,
                'count': request_count + (1 if granted else 0),
                'request_count': request_count,
                'token_count': int(consumed_row.get('token_count') or 0),
            }

        days = sorted(merged, reverse=True)
        total = len(days)
        page_days = days[(page - 1) * size : (page - 1) * size + size]
        return {
            'usage_status': usage_status,
            'measured_at': measured_at,
            'items': [merged[day] for day in page_days],
            'total': total,
            'page': page,
            'size': size,
            'total_pages': (total + size - 1) // size if size else 0,
        }

    @staticmethod
    async def _granted_by_day(db: AsyncSession, user_id: int, app_code: str) -> dict[str, Decimal]:
        """从履约事件里取「按本地日的入账合计」。

        只统计**已成功**的钱包发放/回收：pending 的命令还没到账，把它算进流水
        等于告诉用户钱已经到了。
        """
        rows = (
            await db.execute(
                select(CreditGrantEvent.delivered_at, CreditGrantEvent.event_type, CreditGrantEvent.applied_credits)
                .where(
                    CreditGrantEvent.user_id == user_id,
                    CreditGrantEvent.app_code == app_code,
                    CreditGrantEvent.status == 'succeeded',
                    CreditGrantEvent.event_type.in_(('wallet_grant', 'wallet_revoke')),
                    CreditGrantEvent.applied_credits.is_not(None),
                )
            )
        ).all()

        acc: dict[str, Decimal] = {}
        for delivered_at, event_type, applied_credits in rows:
            if delivered_at is None or applied_credits is None:
                continue
            day = CreditUsageService._local_day(delivered_at)
            amount = Decimal(str(applied_credits))
            if event_type == 'wallet_revoke':
                amount = -amount
            acc[day] = acc.get(day, Decimal(0)) + amount
        return acc

    @staticmethod
    def _local_day(value: datetime) -> str:
        """UTC 时刻 → Asia/Shanghai 本地日字符串（与 NewAPI 的日切分口径一致）。"""
        return value.astimezone(_SHANGHAI_TZ).strftime('%Y-%m-%d')


credit_usage_service: CreditUsageService = CreditUsageService()
