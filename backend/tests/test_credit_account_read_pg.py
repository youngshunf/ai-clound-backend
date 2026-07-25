"""doc94 F3 权威余额读路径验收。

锁住三件事：

1. ``available_credits`` 直接取 NewAPI 的 ``total_available_credits``，
   **不再**用 ``quota − used_quota`` 推算——那个公式本身就是错的
   （``users.quota`` 已经是剩余额度，再减累计用量会算出负数或错误进度）；
2. NewAPI 读不到时返回 ``credit_status=unavailable`` 且余额为 ``None``，
   既不回落云端旧值，也不伪造 0；
3. ``measured_at`` 原样透传，不由云端重新盖时间戳。
"""

from __future__ import annotations

from typing import Any

import pytest

from backend.app.billing.service.credit_account_service import (
    CREDIT_STATUS_OK,
    CREDIT_STATUS_UNAVAILABLE,
    CREDIT_STATUS_UNMAPPED,
    credit_account_service,
)
from backend.app.newapi.credit_client import NewApiCreditError

pytestmark = pytest.mark.asyncio

_ACCOUNT = {
    'newapi_user_id': 4242,
    'wallet': {'remaining_credits': '3'},
    'subscriptions': [
        {
            'external_subscription_id': 'HXC0001',
            'status': 'active',
            'cycle_limit_credits': '10',
            'cycle_used_credits': '4',
            'cycle_remaining_credits': '6',
            'cycle_start_at': '2026-07-01T00:00:00Z',
            'cycle_end_at': '2026-07-31T00:00:00Z',
        }
    ],
    # 钱包 3 + 订阅剩余 6 = 9。若云端按 quota−used_quota 推算会得到别的数。
    'total_available_credits': '9',
    'measured_at': '2026-07-25T10:00:00Z',
}


class _Mapping:
    def __init__(self, newapi_user_id: int | None) -> None:
        self.newapi_user_id = newapi_user_id


class _MappingDao:
    def __init__(self, mapping: Any) -> None:
        self._mapping = mapping

    async def get_by_user(self, db, user_id, app_code):  # noqa: ANN001
        return self._mapping


def _patch(monkeypatch, *, mapping: Any, account: Any = None, error: Exception | None = None) -> None:
    import backend.app.newapi.crud as crud_module
    import backend.app.billing.service.credit_account_service as service_module

    monkeypatch.setattr(crud_module, 'llm_newapi_user_mapping_dao', _MappingDao(mapping))

    class _Client:
        async def get_credit_account(self, newapi_user_id: int) -> dict:
            if error is not None:
                raise error
            return account

    monkeypatch.setattr(service_module, 'newapi_credit_client', _Client())


async def test_available_credits_comes_straight_from_authority(monkeypatch) -> None:
    """可用积分直接取权威快照的 total_available_credits，云端不做任何算术。"""
    _patch(monkeypatch, mapping=_Mapping(4242), account=_ACCOUNT)

    result = await credit_account_service.get_account(None, 1)

    assert result['credit_status'] == CREDIT_STATUS_OK
    assert result['available_credits'] == '9'
    assert result['wallet_credits'] == '3'
    # measured_at 原样透传：展示层据此判断新鲜度
    assert result['measured_at'] == '2026-07-25T10:00:00Z'
    assert result['subscriptions'][0]['cycle_remaining_credits'] == '6'


async def test_unavailable_returns_null_not_zero(monkeypatch) -> None:
    """NewAPI 读不到 → 余额为 None、状态 unavailable、可重试；绝不伪造 0，也不回落旧值。"""
    _patch(
        monkeypatch,
        mapping=_Mapping(4242),
        error=NewApiCreditError('模拟不可达', code='newapi_credit_unreachable', retryable=True),
    )

    result = await credit_account_service.get_account(None, 1)

    assert result['credit_status'] == CREDIT_STATUS_UNAVAILABLE
    assert result['available_credits'] is None
    assert result['wallet_credits'] is None
    assert result['measured_at'] is None
    assert result['retryable'] is True
    assert result['subscriptions'] == []


async def test_unmapped_user_is_distinguished_from_zero_balance(monkeypatch) -> None:
    """尚未开通 NewAPI 账户是「未开通」，不是「余额为 0」，两者必须能区分开。"""
    _patch(monkeypatch, mapping=None)

    result = await credit_account_service.get_account(None, 1)

    assert result['credit_status'] == CREDIT_STATUS_UNMAPPED
    assert result['available_credits'] is None
    assert result['retryable'] is False
