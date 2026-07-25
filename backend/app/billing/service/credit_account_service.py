"""权威积分账户读服务（doc94 F3）：余额只有一个来源。

**改造前**：可用积分 = `(users.quota − users.used_quota) / RATE`。这个公式本身就是错的——
`users.quota` 已经是当前剩余额度，`used_quota` 是累计用量，两者再相减会算出负数或错误进度
（截图里「套餐 100、本月已用 487、仍可请求」正是它的可见症状）。

**改造后**：直接读 NewAPI 的权威账户快照，`available_credits` 就是 `total_available_credits`，
云端不做任何算术。

三条硬约束（doc94 §4.5）：

1. **带 measured_at**：余额相关字段必须携带 NewAPI 的测量时刻，一路透传到 WebUI，
   由展示层自行判断新鲜度并显示「数据更新于 X 前」；
2. **不参与任何门禁**：这里返回的值不得用于判断「能不能发起模型调用」——
   硬门禁只有一处，就是 NewAPI relay 的 403；
3. **不可用就说不可用**：NewAPI 读不到时返回 ``credit_status='unavailable'`` 且余额字段为 ``None``，
   **绝不**回落云端旧余额、也**绝不**伪造 0。伪造 0 会让用户以为额度用光了，
   回落旧值会让用户按一个早已过时的数字做决定。
"""

from __future__ import annotations

from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from backend.app.billing.observability.metrics import BILLING_CREDIT_ACCOUNT_UNAVAILABLE_TOTAL
from backend.app.newapi.credit_client import NewApiCreditError, newapi_credit_client
from backend.common.log import log

#: 账户读取状态：ok=拿到权威快照；unavailable=NewAPI 读不到；unmapped=用户还没有 NewAPI 账户。
CREDIT_STATUS_OK = 'ok'
CREDIT_STATUS_UNAVAILABLE = 'unavailable'
CREDIT_STATUS_UNMAPPED = 'unmapped'


def _unavailable(status: str, reason: str) -> dict[str, Any]:
    """构造「读不到权威余额」的响应形状：余额字段一律为 None，不是 0。"""
    return {
        'credit_status': status,
        'measured_at': None,
        'available_credits': None,
        'wallet_credits': None,
        'subscriptions': [],
        'unavailable_reason': reason,
        'retryable': status == CREDIT_STATUS_UNAVAILABLE,
    }


class CreditAccountService:
    """读 NewAPI 权威积分账户。"""

    @staticmethod
    async def get_account(db: AsyncSession, user_id: int, app_code: str = 'huanxing') -> dict[str, Any]:
        """返回权威账户快照（含 credit_status 与 measured_at）。

        本方法只读、不写，也不做任何余额算术：把 NewAPI 的数字原样搬过来。
        """
        from backend.app.newapi.crud import llm_newapi_user_mapping_dao

        mapping = await llm_newapi_user_mapping_dao.get_by_user(db, user_id, app_code)
        if not mapping or not mapping.newapi_user_id:
            # 还没有 NewAPI 账户：这是「尚未开通」，不是「余额为 0」，两者必须区分。
            return _unavailable(CREDIT_STATUS_UNMAPPED, '用户尚未开通 NewAPI 账户')

        try:
            account = await newapi_credit_client.get_credit_account(int(mapping.newapi_user_id))
        except NewApiCreditError as exc:
            BILLING_CREDIT_ACCOUNT_UNAVAILABLE_TOTAL.inc()
            log.warning(f'[CreditAccount] 权威账户读取失败 user_id={user_id}: {exc}')
            return _unavailable(CREDIT_STATUS_UNAVAILABLE, str(exc))

        subscriptions = []
        for item in account.get('subscriptions') or []:
            subscriptions.append(
                {
                    'external_subscription_id': item.get('external_subscription_id'),
                    'status': item.get('status'),
                    'cycle_limit_credits': item.get('cycle_limit_credits'),
                    'cycle_used_credits': item.get('cycle_used_credits'),
                    'cycle_remaining_credits': item.get('cycle_remaining_credits'),
                    'cycle_start_at': item.get('cycle_start_at'),
                    'cycle_end_at': item.get('cycle_end_at'),
                }
            )

        return {
            'credit_status': CREDIT_STATUS_OK,
            # measured_at 必须原样透传：展示层据此判断新鲜度，绝不由云端重新盖时间戳。
            'measured_at': account.get('measured_at'),
            # 直接用 NewAPI 的 total_available_credits，不做 quota−used_quota 这类推算。
            'available_credits': account.get('total_available_credits'),
            'wallet_credits': (account.get('wallet') or {}).get('remaining_credits'),
            'subscriptions': subscriptions,
            'unavailable_reason': None,
            'retryable': False,
        }


credit_account_service: CreditAccountService = CreditAccountService()
