"""按日流水必须**按来源分开**统计，不能只给一个净额（2026-08-21 生产实测）。

那天 user 116 的账户上同时发生了五件事：

    subscription_activate  free_tier_backfill   +100      免费档周期额度
    subscription_expire    upgrade_supersede     -91.68   升级时旧池未用完部分作废
    subscription_activate  subscribe            +500      轻享版周期额度
    wallet_grant           credit_pack          +100 ×2   两笔积分包
    NewAPI 用量                                  -45.53   LLM 消耗

旧口径 `_granted_by_day` **只看 wallet_grant/​wallet_revoke**，订阅那两笔根本不进流水；
再把剩下的压成 `net = +154.47` 一个绿色数字，主人既看不到自己花了多少，
也看不到有 91.68 被清零作废了。

这批断言锁住：四类各自成列、金额不互相抵消、净额是四类加消耗的真实和。
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone as dt_timezone
from decimal import Decimal
from typing import Any

import pytest

from backend.app.billing.service.credit_usage_service import credit_usage_service

pytestmark = pytest.mark.asyncio

_SHANGHAI = dt_timezone(timedelta(hours=8))
#: 事件投递时刻都落在北京时间 2026-08-21 当天。
_DAY = '2026-08-21'


class _Event:
    def __init__(self, event_type: str, applied: str, hour: int) -> None:
        self.delivered_at = datetime(2026, 8, 21, hour, 0, tzinfo=_SHANGHAI)
        self.event_type = event_type
        self.applied_credits = Decimal(applied)


#: 生产那天的真实事件集合。
_PROD_EVENTS = [
    _Event('subscription_activate', '100', 19),
    _Event('subscription_expire', '91.68412', 20),
    _Event('subscription_activate', '500', 20),
    _Event('wallet_grant', '100', 21),
    _Event('wallet_grant', '100', 21),
]


class _FakeResult:
    def __init__(self, rows: list[Any]) -> None:
        self._rows = rows

    def all(self) -> list[Any]:
        return self._rows


class _FakeDb:
    def __init__(self, events: list[_Event]) -> None:
        self.events = events

    async def execute(self, stmt: Any) -> _FakeResult:
        return _FakeResult([(e.delivered_at, e.event_type, e.applied_credits) for e in self.events])


def _patch_usage(monkeypatch, *, consumed_credits: str) -> None:
    """只打桩「NewAPI 消耗」这一侧；分桶逻辑跑真实实现，DB 用假库喂事件。

    ⚠️ 不要去 monkeypatch `_movements_by_day`——那样测的就是桩而不是被测代码
    （初版这么写还撞了自我递归）。`daily_flow(db, …)` 会把同一个 db 传下去，
    所以直接传假库即可让真实分桶逻辑跑起来。
    """
    import backend.app.billing.service.credit_usage_service as mod

    async def _uid(db, user_id, app_code):  # noqa: ANN001
        return 7

    class _Client:
        async def get_usage_daily(self, newapi_user_id, **kwargs):  # noqa: ANN001
            return {
                'measured_at': '2026-08-21T13:00:00Z',
                'items': [{
                    'day': _DAY,
                    'consumed_credits': consumed_credits,
                    'request_count': 152,
                    'token_count': 15175000,
                }],
            }

    monkeypatch.setattr(mod, '_newapi_user_id', _uid)
    monkeypatch.setattr(mod, 'newapi_credit_client', _Client())


async def test_four_buckets_are_reported_separately(monkeypatch) -> None:
    """订阅发放 / 订阅清零 / 积分包入账 / 消耗，四个数各自可读，互不抵消。"""
    _patch_usage(monkeypatch, consumed_credits='45.525102')

    result = await credit_usage_service.daily_flow(_FakeDb(_PROD_EVENTS), 116, page=1, size=10)
    day = next(i for i in result['items'] if i['date'] == _DAY)

    assert day['subscription_granted'] == Decimal('600'), '免费档 100 + 轻享版 500'
    assert day['subscription_revoked'] == Decimal('-91.68412'), '升级清零必须自己成列，不许被发放吃掉'
    assert day['pack_granted'] == Decimal('200'), '两笔积分包'
    assert day['pack_revoked'] == Decimal(0)
    assert day['consumed'] == Decimal('-45.525102')


async def test_subscription_grants_are_no_longer_invisible(monkeypatch) -> None:
    """订阅发放此前**完全不进流水**（旧口径只看 wallet_*），现在必须出现。"""
    only_subscription = [_Event('subscription_activate', '500', 20)]
    _patch_usage(monkeypatch, consumed_credits='0')

    result = await credit_usage_service.daily_flow(_FakeDb(only_subscription), 116, page=1, size=10)
    day = next(i for i in result['items'] if i['date'] == _DAY)

    assert day['subscription_granted'] == Decimal('500')
    assert day['granted'] == Decimal('500'), '入账合计要包含订阅，不再只有钱包那一路'


async def test_net_is_the_true_sum_of_every_component(monkeypatch) -> None:
    """净额必须等于四类变动 + 消耗；旧口径漏算订阅两笔，净额本身就是错的。"""
    _patch_usage(monkeypatch, consumed_credits='45.525102')

    result = await credit_usage_service.daily_flow(_FakeDb(_PROD_EVENTS), 116, page=1, size=10)
    day = next(i for i in result['items'] if i['date'] == _DAY)

    expected = (
        day['subscription_granted'] + day['subscription_revoked']
        + day['pack_granted'] + day['pack_revoked'] + day['consumed']
    )
    assert day['net'] == expected
    # 旧口径下这天的净额是 +154.47（只算了钱包 200 与消耗 45.53），现在不该再是那个数
    assert day['net'] != Decimal('154.474898')
