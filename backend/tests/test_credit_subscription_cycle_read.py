"""订阅「当前周期」读路径验收。

这批断言锁的是 2026-08-21 修掉的四个可见症状（生产实测：4 份免费合同全部命中）：

1. **重置日定格**——``billing_cycle_end`` 直接返回合同列，而那两列建号时写一次后
   无人推进（``_refresh_billing_cycle`` 全仓零调用），于是「7 月 18 日重置」
   在 8 月 21 日仍原样显示；
2. **已用超过额度**——分子取 NewAPI 全量日消费、分母取云端 ``monthly_credits``，
   两本账相除渲染出「本周期已用 151.59 / 100 积分」；
3. **假的套餐额度**——``monthly_remaining`` 硬编码 ``0.0``，UI 上是「套餐额度 0」；
4. **权威侧没有订阅池时仍报额度**——合同上写着「每 30 天 100 积分」，
   而 NewAPI 里一个订阅都没有，那个 100 从来没有执行力。
"""

from __future__ import annotations

from datetime import datetime, timedelta
from datetime import timezone as dt_timezone
from decimal import Decimal
from typing import Any

import pytest

from backend.app.billing.service.credit_account_service import (
    CREDIT_STATUS_OK,
    CREDIT_STATUS_UNAVAILABLE,
)
from backend.app.billing.service.credit_service import credit_service

pytestmark = pytest.mark.asyncio

TEST_DB: Any = None
_NOW = datetime(2026, 8, 21, 12, 0, tzinfo=dt_timezone.utc)


class _Contract:
    """免费合同的最小替身，字段与 `UserSubscription` 同名。"""

    def __init__(self, **overrides: Any) -> None:
        self.user_id = 116
        self.app_code = 'huanxing'
        self.tier = 'free'
        self.subscription_type = 'monthly'
        self.status = 'active'
        self.monthly_credits = Decimal('100')
        # 生产实测的那份合同：2026-06-18 建，窗口再没动过。
        self.billing_cycle_start = datetime(2026, 6, 18, 22, 56, tzinfo=dt_timezone.utc)
        self.billing_cycle_end = datetime(2026, 7, 18, 22, 56, tzinfo=dt_timezone.utc)
        self.contract_start_at = datetime(2026, 6, 18, 22, 56, tzinfo=dt_timezone.utc)
        self.subscription_start_date = datetime(2026, 6, 18, 22, 56, tzinfo=dt_timezone.utc)
        self.subscription_end_date = None
        self.next_grant_date = None
        self.cycle_seconds = 30 * 24 * 60 * 60
        for key, value in overrides.items():
            setattr(self, key, value)


class _Tier:
    display_name = '免费版'
    credits_per_cycle = Decimal('100')
    storage_bytes = 10 * 1024**3
    max_agents = 20


def _account(subscriptions: list[dict[str, Any]], *, status: str = CREDIT_STATUS_OK) -> dict[str, Any]:
    return {
        'credit_status': status,
        'measured_at': '2026-08-21T12:00:00Z',
        'available_credits': '4811.6' if status == CREDIT_STATUS_OK else None,
        'wallet_credits': '4811.6' if status == CREDIT_STATUS_OK else None,
        'subscriptions': subscriptions,
        'unavailable_reason': None,
        'retryable': False,
    }


def _authoritative_subscription() -> dict[str, Any]:
    """权威侧真有订阅池时的快照：本期 8/17 → 9/16，已用 12 / 额度 100。"""
    return {
        'external_subscription_id': 'HXF0001',
        'status': 'active',
        'cycle_limit_credits': '100',
        'cycle_used_credits': '12',
        'cycle_remaining_credits': '88',
        'cycle_start_at': '2026-08-17T00:00:00Z',
        'next_reset_at': '2026-09-16T00:00:00Z',
        # 免费合同无商业到期：这一列为 null 是正常的，不得被当成「周期终点未知」。
        'cycle_end_at': None,
    }


def _patch(monkeypatch, *, contract: Any, account: dict[str, Any], daily_consumed: str = '151.59') -> None:
    import backend.app.billing.service.billing_usage_service as usage_module
    import backend.app.billing.service.credit_account_service as account_module
    import backend.app.billing.service.credit_service as service_module


    class _Accounts:
        async def get_account(self, db, user_id, app_code='huanxing'):  # noqa: ANN001
            return account

    class _Usage:
        """记录被查询的窗口，好断言「统计的是当前期而不是一段死掉的历史」。"""

        def __init__(self) -> None:
            self.window: tuple[datetime, datetime] | None = None

        async def get_cycle_consumed(self, db, user_id, start, end, app_code='huanxing'):  # noqa: ANN001
            self.window = (start, end)
            return {'consumed_credits': Decimal(daily_consumed), 'request_count': 3, 'token_count': 10}

    class _Pricing:
        async def get_tier(self, db, key):  # noqa: ANN001
            return _Tier()

    usage = _Usage()
    # 选行接缝在 `_current_contract`（它按「此刻生效」挑合同，不是物理第一行）
    async def _current(self, db, user_id, app_code):  # noqa: ANN001
        return contract

    monkeypatch.setattr(service_module.CreditService, '_current_contract', _current)
    monkeypatch.setattr(service_module, 'offering_pricing', _Pricing())
    monkeypatch.setattr(service_module.timezone, 'now', lambda: _NOW)
    monkeypatch.setattr(account_module, 'credit_account_service', _Accounts())
    monkeypatch.setattr(usage_module, 'billing_usage_service', usage)
    # 合同分支要能认出替身
    monkeypatch.setattr(service_module, 'UserSubscription', _Contract)
    return usage


async def test_cycle_window_comes_from_authority_when_subscription_exists(monkeypatch) -> None:
    """权威侧有订阅池时，周期窗口与已用/额度全部取权威，一对数字来自同一本账。"""
    _patch(monkeypatch, contract=_Contract(), account=_account([_authoritative_subscription()]))

    info = await credit_service.get_user_credits_info(TEST_DB, 116)

    assert info['billing_cycle_start'].startswith('2026-08-17')
    # 重置日取 next_reset_at，而不是 cycle_end_at（后者是合同终止，免费合同为 null）
    assert info['billing_cycle_end'].startswith('2026-09-16')
    assert info['monthly_credits'] == 100.0
    assert info['cycle_consumed_credits'] == 12.0
    assert info['monthly_remaining'] == 88.0


async def test_cycle_window_never_stays_in_the_past_without_authority(monkeypatch) -> None:
    """权威侧没有订阅池时，窗口按合同锚点滚动推进——绝不返回一个已经过去的重置日。

    这是本次修复的核心症状：旧写法直接返回合同列 2026-06-18 → 2026-07-18，
    到 8 月 21 日仍原样显示「7 月 18 日重置」。
    """
    usage = _patch(monkeypatch, contract=_Contract(), account=_account([]))

    info = await credit_service.get_user_credits_info(TEST_DB, 116)

    cycle_start = datetime.fromisoformat(info['billing_cycle_start'])
    cycle_end = datetime.fromisoformat(info['billing_cycle_end'])
    assert cycle_start <= _NOW < cycle_end, '当前期必须包含此刻'
    assert cycle_end - cycle_start == timedelta(days=30), '一期恰好 30 天'
    # 锚点节奏保持不变：起点与合同起点相差整数个周期，不是「从今天重新开始」
    assert (cycle_start - _Contract().contract_start_at).total_seconds() % (30 * 86400) == 0
    # 消耗窗口也跟着走到当前期，而不是停在 6/18–7/18
    assert usage.window is not None
    assert usage.window[0] == cycle_start


async def test_window_stays_self_consistent_when_authority_omits_next_reset(monkeypatch) -> None:
    """升级窗口：NewAPI 还是旧版（不发 next_reset_at）时，终点必须从**权威起点**推出。

    否则会拿合同锚点滚出来的终点去配权威的起点，得到「起点 8/17、终点 9/13」
    这种两端不同源、对不齐的窗口。
    """
    stale = _authoritative_subscription()
    del stale['next_reset_at']
    _patch(monkeypatch, contract=_Contract(), account=_account([stale]))

    info = await credit_service.get_user_credits_info(TEST_DB, 116)

    cycle_start = datetime.fromisoformat(info['billing_cycle_start'])
    cycle_end = datetime.fromisoformat(info['billing_cycle_end'])
    assert cycle_start.isoformat().startswith('2026-08-17'), '起点仍取权威'
    assert cycle_end - cycle_start == timedelta(days=30), '终点从权威起点推出，两端同源'


async def test_no_authoritative_pool_reports_zero_quota_not_the_contract_number(monkeypatch) -> None:
    """权威侧一个订阅都没有时，周期额度是 0，不是合同列上那个没有执行力的 100。

    生产实测：4 份免费合同全部 `external_subscription_id` 为空、从未履约，
    NewAPI 侧零订阅。把合同上的 100 当分母，才会渲染出「已用 151.59 / 100」。
    """
    _patch(monkeypatch, contract=_Contract(), account=_account([]))

    info = await credit_service.get_user_credits_info(TEST_DB, 116)

    assert info['monthly_credits'] == 0.0, '没有订阅池就没有周期额度'
    # 读不到就是 None，不是 0——写死的 0 在 UI 上是「套餐额度 0」这句假话
    assert info['monthly_remaining'] is None
    assert info['bonus_remaining'] is None


async def test_unavailable_authority_does_not_fabricate_a_cycle(monkeypatch) -> None:
    """权威读不到时不假装知道周期额度：那是「不知道」，不是「额度为 0 已用满」。"""
    _patch(
        monkeypatch,
        contract=_Contract(),
        account=_account([_authoritative_subscription()], status=CREDIT_STATUS_UNAVAILABLE),
    )

    info = await credit_service.get_user_credits_info(TEST_DB, 116)

    assert info['credit_status'] == CREDIT_STATUS_UNAVAILABLE
    assert info['monthly_remaining'] is None
    assert info['monthly_credits'] == 0.0
    # 窗口仍要是当前期，不能因为读不到就退回那个定格的历史窗口
    assert datetime.fromisoformat(info['billing_cycle_end']) > _NOW


async def test_unparsable_authority_field_is_treated_as_missing(monkeypatch) -> None:
    """权威快照字段解析不出来时整体按缺失处理，绝不用半截的和冒充真实数字。"""
    broken = _authoritative_subscription() | {'cycle_used_credits': '还没算出来'}
    _patch(monkeypatch, contract=_Contract(), account=_account([broken]))

    info = await credit_service.get_user_credits_info(TEST_DB, 116)

    assert info['monthly_remaining'] is None
    assert info['monthly_credits'] == 0.0


# ── 选行：升过档之后必须取「此刻生效的」那份，不是物理第一行 ──────────────

class _Row:
    """`_current_contract` 只按 status / id 挑行，这里给它一个最小替身。"""

    def __init__(self, row_id: int, tier: str, status: str) -> None:
        self.id = row_id
        self.tier = tier
        self.status = status


class _FakeResult:
    def __init__(self, row: Any) -> None:
        self._row = row

    def scalar_one_or_none(self) -> Any:
        return self._row


class _FakeDb:
    """忠实模拟 `where … order_by … limit 1`：**过滤和排序方向都要照做**。

    ⚠️ 这个假库最初无论如何都返回 `max(id)`，于是「去掉 order_by」这种变异根本
    影响不到它——测试看起来在守选行，实际只守了「有没有过滤」。真 bug 恰恰是
    「没有排序、拿物理第一行」，那一半没被守住。现在按 SQL 里的 DESC/ASC 取首行。
    """

    def __init__(self, rows: list[_Row]) -> None:
        self.rows = rows

    async def execute(self, stmt: Any) -> _FakeResult:
        sql = ' '.join(str(stmt).split())
        candidates = list(self.rows)
        if 'status IN' in sql:
            candidates = [r for r in candidates if r.status in ('active', 'cancel_at_period_end')]
        descending = 'DESC' in sql.upper()
        candidates.sort(key=lambda r: r.id, reverse=descending)
        return _FakeResult(candidates[0] if candidates else None)


async def test_current_contract_prefers_the_active_one_after_an_upgrade() -> None:
    """升级会留下「旧 free 已终止 + 新 lite 生效」两行，必须取后者。

    2026-08-21 真实支付升级到轻享版时暴露：旧写法用不带排序/过滤的通用 DAO，
    取到物理第一行（那份已 expired 的 free），页面显示「免费版 · 已过期」，
    而用户实际持有一份生效中的轻享版。此前不可能发现——微信回调一直是坏的，
    没有任何用户成功升过档，也就从来没有第二份合同。
    """
    db = _FakeDb([_Row(4, 'free', 'expired'), _Row(9, 'lite', 'active')])

    picked = await credit_service._current_contract(db, 116, 'huanxing')

    assert picked is not None
    assert picked.tier == 'lite'
    assert picked.status == 'active'


async def test_current_contract_falls_back_to_latest_when_none_active() -> None:
    """一份生效的都没有时退回最近一份，页面仍能显示「上次的档位 + 已过期」，而不是空白。"""
    db = _FakeDb([_Row(4, 'free', 'expired'), _Row(9, 'lite', 'expired')])

    picked = await credit_service._current_contract(db, 116, 'huanxing')

    assert picked is not None
    assert picked.tier == 'lite', '退回的是最近一份，不是最老那份'


async def test_cancel_at_period_end_still_counts_as_current() -> None:
    """取消自动续费的合同仍在有效期内，必须算作「此刻生效」。"""
    db = _FakeDb([_Row(4, 'free', 'expired'), _Row(9, 'lite', 'cancel_at_period_end')])

    picked = await credit_service._current_contract(db, 116, 'huanxing')

    assert picked is not None and picked.tier == 'lite'


async def test_scheduled_future_contract_must_not_shadow_the_active_one() -> None:
    """降级会排一份**尚未生效**的 scheduled 合同，它的 id 比当前生效那份更大。

    只按 id 倒序而不过滤状态，就会取到那份还没开始的合同，页面提前显示降级后的档位——
    而用户这一期的钱已经付过、额度还该按原档算。状态过滤就是为这种情形存在的，
    这条用例专门守它（去掉 `status IN (...)` 即红）。
    """
    db = _FakeDb([_Row(9, 'pro', 'active'), _Row(12, 'lite', 'scheduled')])

    picked = await credit_service._current_contract(db, 116, 'huanxing')

    assert picked is not None
    assert picked.tier == 'pro', 'scheduled 合同尚未生效，不得顶替当前生效的那份'
