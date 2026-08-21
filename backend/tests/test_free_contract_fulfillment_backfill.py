"""存量免费合同补履约验收（2026-08-21）。

生产实测：4 份免费合同全部是 doc94 迁移**之前**建的，
``external_subscription_id`` 为空、``fulfillment_status`` 落在加列时的 DEFAULT
``'not_required'``，``credit_grant_event`` 零行，NewAPI ``user_subscriptions`` 零行。
也就是说合同上写着「每 30 天 100 积分」，而权威侧一个订阅池都没有——
那 100 从来没有生效过。

而 ``ensure_free_contract`` 过去见到任何生效合同就无条件 ``return existing``，
于是那批合同**此生**都拿不到履约。这批断言锁住补齐动作与它的三条边界。
"""

from __future__ import annotations

from datetime import datetime
from datetime import timezone as dt_timezone
from decimal import Decimal
from typing import Any

import pytest

from backend.app.billing.service.credit_grant_service import credit_grant_service

pytestmark = pytest.mark.asyncio

TEST_DB: Any = None
_ANCHOR = datetime(2026, 6, 18, 22, 56, tzinfo=dt_timezone.utc)


class _Contract:
    """生产上那份从未履约的存量免费合同。"""

    def __init__(self, **overrides: Any) -> None:
        self.id = 4
        self.user_id = 116
        self.app_code = 'huanxing'
        self.tier = 'free'
        self.status = 'active'
        self.monthly_credits = Decimal('100')
        self.contract_no = ''
        # 这两列正是「从未投递」的证据
        self.external_subscription_id = ''
        self.fulfillment_status = 'not_required'
        self.contract_start_at = _ANCHOR
        self.subscription_start_date = _ANCHOR
        self.cycle_seconds = None
        self.free_policy_version = None
        self.free_grant_epoch = None
        for key, value in overrides.items():
            setattr(self, key, value)


class _Tier:
    display_name = '免费版'
    credits_per_cycle = Decimal('100')
    storage_bytes = 10 * 1024**3
    max_agents = 20


class _Mapping:
    def __init__(self, newapi_user_id: int | None) -> None:
        self.newapi_user_id = newapi_user_id


def _patch(monkeypatch, *, mapping: Any) -> list[dict[str, Any]]:
    """打桩三处外部依赖，返回被登记的履约命令列表。"""
    import backend.app.billing.service.credit_grant_service as service_module
    import backend.app.newapi.crud as crud_module

    enqueued: list[dict[str, Any]] = []

    class _MappingDao:
        async def get_by_user(self, db, user_id, app_code):  # noqa: ANN001
            return mapping

    class _Pricing:
        async def get_tier(self, db, key):  # noqa: ANN001
            return _Tier()

    class _EventService:
        async def enqueue(self, db, **kwargs):  # noqa: ANN001
            enqueued.append(kwargs)
            return object()

    monkeypatch.setattr(crud_module, 'llm_newapi_user_mapping_dao', _MappingDao())
    monkeypatch.setattr(service_module, 'offering_pricing', _Pricing())
    monkeypatch.setattr(service_module, 'credit_grant_event_service', _EventService())
    return enqueued


async def test_legacy_contract_gets_backfilled(monkeypatch) -> None:
    """从未履约的存量免费合同补上连接键、周期长度与一条 activate 命令。"""
    enqueued = _patch(monkeypatch, mapping=_Mapping(7))
    contract = _Contract()

    assert await credit_grant_service.backfill_free_contract_fulfillment(
        TEST_DB, contract, app_code='huanxing'
    )

    assert contract.external_subscription_id, '补上合同与 NewAPI 订阅池之间的连接键'
    assert contract.external_subscription_id == contract.contract_no
    assert contract.fulfillment_status == 'pending'
    assert contract.cycle_seconds == 30 * 24 * 60 * 60

    assert len(enqueued) == 1
    payload = enqueued[0]['payload_extra']
    assert payload['reason'] == 'free_tier_backfill'
    assert payload['end_at'] is None, '免费合同无商业到期'
    assert payload['cycle_count'] is None, '无限期循环'
    # 起点延续合同本来的节奏，不是「回填当天」——否则所有存量用户的重置日会挤在同一天
    assert payload['start_at'].startswith('2026-06-18')
    assert enqueued[0]['newapi_user_id'] == 7


async def test_already_fulfilled_contract_is_left_alone(monkeypatch) -> None:
    """已有连接键 = 已经投递过，不得再登记第二条命令。"""
    enqueued = _patch(monkeypatch, mapping=_Mapping(7))
    contract = _Contract(external_subscription_id='HXF已存在', fulfillment_status='succeeded')

    assert not await credit_grant_service.backfill_free_contract_fulfillment(
        TEST_DB, contract, app_code='huanxing'
    )
    assert enqueued == []
    assert contract.fulfillment_status == 'succeeded'


async def test_missing_newapi_mapping_leaves_contract_untouched(monkeypatch) -> None:
    """还没开通 NewAPI 账户时必须**一个字段都不动**就退出。

    反过来（先写字段、enqueue 失败再吞异常）会留下一份「已标 pending、
    有连接键、但 outbox 里没有对应命令」的合同——它既不会被投递，
    也不会再被本方法认领（判据正是那个键为空），永久卡死。
    """
    enqueued = _patch(monkeypatch, mapping=None)
    contract = _Contract()

    assert not await credit_grant_service.backfill_free_contract_fulfillment(
        TEST_DB, contract, app_code='huanxing'
    )
    assert enqueued == []
    assert contract.external_subscription_id == ''
    assert contract.fulfillment_status == 'not_required'
    assert contract.contract_no == ''
    assert contract.cycle_seconds is None


async def test_paid_contract_is_not_a_backfill_target(monkeypatch) -> None:
    """付费合同不走免费档回填：它的履约由支付回调登记，键空间也不同。"""
    enqueued = _patch(monkeypatch, mapping=_Mapping(7))
    contract = _Contract(tier='pro')

    assert not await credit_grant_service.backfill_free_contract_fulfillment(
        TEST_DB, contract, app_code='huanxing'
    )
    assert enqueued == []
