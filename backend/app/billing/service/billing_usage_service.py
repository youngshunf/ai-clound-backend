"""计费用量取数服务（doc94 D1：唯一计量源是 NewAPI，云端不做金额算术）。

**改造前**：这里持有一份 quota→积分的换算函数与常量，
并用 `(quota − used_quota)` 推算「可用积分」。两个问题：换算常量在云端与 NewAPI 各存一份，
以及那个公式本身就是错的（`users.quota` 已是当前剩余额度，再减累计用量会算出负数）。

**改造后**：只剩「本计费周期消耗了多少」一件事，数值由 NewAPI 换算成积分字符串后原样取用。
可用余额不在这里——它在 `credit_account_service`，同样是 NewAPI 权威快照。

读不到时返回 0 消耗并如实记录：这是**展示用**的周期进度，不参与任何门禁
（能不能发起模型调用只有一处判定，就是 NewAPI relay 的 403）。
"""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal

from sqlalchemy.ext.asyncio import AsyncSession

from backend.app.billing.service.credit_usage_service import SHANGHAI_TZ_OFFSET_MINUTES
from backend.app.newapi.credit_client import NewApiCreditError, newapi_credit_client
from backend.app.newapi.crud import llm_newapi_user_mapping_dao
from backend.common.log import log


class BillingUsageService:
    """计费周期内的真实消耗（NewAPI 权威）。"""

    @staticmethod
    async def get_cycle_consumed(
        db: AsyncSession,
        user_id: int,
        start: datetime,
        end: datetime,
        app_code: str = 'huanxing',
    ) -> dict:
        """计费周期内的真实消耗。无映射/不可达 → 全 0（如实记录，不重试掩盖）。

        返回 ``{consumed_credits(Decimal), request_count, token_count}``。
        """
        zero = {'consumed_credits': Decimal(0), 'request_count': 0, 'token_count': 0}
        mapping = await llm_newapi_user_mapping_dao.get_by_user(db, user_id, app_code)
        if not mapping or not mapping.newapi_user_id:
            return zero

        try:
            payload = await newapi_credit_client.get_usage_daily(
                int(mapping.newapi_user_id),
                tz_offset_minutes=SHANGHAI_TZ_OFFSET_MINUTES,
                start=int(start.timestamp()),
                end=int(end.timestamp()),
            )
        except NewApiCreditError as exc:
            log.warning(f'[BillingUsage] 周期消耗读取失败，降级为 0: user_id={user_id}, error={exc}')
            return zero

        consumed = Decimal(0)
        requests = 0
        tokens = 0
        for row in payload.get('items') or []:
            # 金额已是 NewAPI 换算好的积分字符串，这里只做求和，不做单位换算。
            consumed += Decimal(str(row.get('consumed_credits') or 0))
            requests += int(row.get('request_count') or 0)
            tokens += int(row.get('token_count') or 0)
        return {'consumed_credits': consumed, 'request_count': requests, 'token_count': tokens}


billing_usage_service = BillingUsageService()
