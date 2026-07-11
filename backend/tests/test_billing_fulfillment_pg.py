"""统一发货分发器 MK-3 真实 PG 验收（实施/92 MK-3）——零 mock。

覆盖施工清单 MK-3「订单发货收敛：kind-handler 分发器」：
1. build_offering_ref：order_type → offering.kind 唯一映射 + 未知类型返 None；
2. dispatch_fulfillment：命中 kind 处理器执行 / 无 offering_ref 回落 False / 未注册 kind 回落 False /
   处理器异常透传；
3. 四个内核发货 kind（llm_tier/credit_pack/app/seat）+ lead_pack 经各 registrar 真实注册；
4. pay_order.offering_ref JSONB 真实往返（下单入口快照可持久、可被分发器读回）。

分发器处理器为真实 async 函数（探针），order 用数据 double（SimpleNamespace / 真实 PayOrder 行），
非行为 mock。需本地 PostgreSQL :15432（含 hasn_billing.pay_order + offering_ref 列）。
"""

from __future__ import annotations

import pathlib

from types import SimpleNamespace
from typing import TYPE_CHECKING

import pytest
import pytest_asyncio

from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

from backend.app.billing.core import fulfillment
from backend.app.billing.model.pay_order import PayOrder
from backend.database.db import SQLALCHEMY_DATABASE_URL, async_engine
from backend.utils.timezone import timezone

if TYPE_CHECKING:
    from collections.abc import AsyncIterator

pytestmark = pytest.mark.asyncio

_BACKEND = pathlib.Path(__file__).resolve().parents[1]
_PAYORDER_MIGRATION = (
    _BACKEND / 'sql' / 'billing' / 'migrations' / '2026-07-10-billing-kernel-payorder-offering-ref.sql'
)
_TEST_ORDER_NO = 'HXMK3TESTORDER0001'


# ── 纯分发器逻辑（真实 SUT，数据 double，无 DB）──
def test_build_offering_ref_maps_kind() -> None:
    assert fulfillment.build_offering_ref('subscribe')['kind'] == fulfillment.KIND_LLM_TIER
    assert fulfillment.build_offering_ref('credit_pack')['kind'] == fulfillment.KIND_CREDIT_PACK
    assert fulfillment.build_offering_ref('app_purchase')['kind'] == fulfillment.KIND_APP
    assert fulfillment.build_offering_ref('app_seat')['kind'] == fulfillment.KIND_SEAT
    assert fulfillment.build_offering_ref('lead_pack')['kind'] == fulfillment.KIND_LEAD_PACK
    # 快照透传 offering_key/plan_key
    ref = fulfillment.build_offering_ref('subscribe', offering_key='llm:tier', plan_key='pro')
    assert ref == {'offering_key': 'llm:tier', 'plan_key': 'pro', 'kind': 'llm_tier'}
    # 未知 order_type → None（不落 offering_ref）
    assert fulfillment.build_offering_ref('bogus') is None


async def test_dispatch_routes_to_registered_handler() -> None:
    seen = []

    async def _probe(order) -> None:  # noqa: ANN001
        seen.append(order.order_no)

    fulfillment.register_fulfillment('test:mk3probe', _probe)
    order = SimpleNamespace(order_no='o1', offering_ref={'kind': 'test:mk3probe'})
    assert await fulfillment.dispatch_fulfillment(order) is True
    assert seen == ['o1']


async def test_dispatch_returns_false_without_offering_ref() -> None:
    # None / 空 dict 均回落 False（存量订单走旧 order_type 分发）
    assert await fulfillment.dispatch_fulfillment(SimpleNamespace(order_no='o2', offering_ref=None)) is False
    assert await fulfillment.dispatch_fulfillment(SimpleNamespace(order_no='o3', offering_ref={})) is False


async def test_dispatch_returns_false_for_unregistered_kind() -> None:
    order = SimpleNamespace(order_no='o4', offering_ref={'kind': 'test:mk3neverregistered'})
    assert await fulfillment.dispatch_fulfillment(order) is False


async def test_dispatch_reraises_handler_error() -> None:
    async def _boom(order) -> None:  # noqa: ANN001
        raise RuntimeError('发货失败')

    fulfillment.register_fulfillment('test:mk3boom', _boom)
    order = SimpleNamespace(order_no='o5', offering_ref={'kind': 'test:mk3boom'})
    with pytest.raises(RuntimeError, match='发货失败'):
        await fulfillment.dispatch_fulfillment(order)


def test_core_kinds_registered_after_registrars() -> None:
    """四个内核发货 kind + lead_pack 经各 registrar 真实注册（应用启动同款调用）。"""
    from backend.app.billing.service.pay_callbacks import register_callbacks
    from backend.app.hasn.service.app_purchase_callback import register_app_purchase_callback
    from backend.app.hasn.service.app_seat_purchase_callback import register_app_seat_purchase_callback
    from backend.app.hasn_growth.service.lead_pack_callback import register_lead_pack_callback

    register_callbacks()
    register_app_purchase_callback()
    register_app_seat_purchase_callback()
    register_lead_pack_callback()

    kinds = fulfillment.get_registered_kinds()
    assert {
        fulfillment.KIND_LLM_TIER,
        fulfillment.KIND_CREDIT_PACK,
        fulfillment.KIND_APP,
        fulfillment.KIND_SEAT,
        fulfillment.KIND_LEAD_PACK,
    } <= kinds, f'内核发货 kind 未齐: {kinds}'


# ── 真实 PG：pay_order.offering_ref JSONB 往返 + 分发器读回 ──
@pytest_asyncio.fixture
async def sess() -> AsyncIterator:
    await async_engine.dispose()
    engine = create_async_engine(SQLALCHEMY_DATABASE_URL, poolclass=NullPool)
    try:
        async with engine.connect() as conn:
            await conn.execute(select(1))
    except Exception as exc:
        await engine.dispose()
        pytest.skip(f'本地 PostgreSQL 不可达，跳过: {exc!r}')
    # 幂等应用 offering_ref 加列（裸库可跑）
    async with engine.begin() as conn:
        raw = _PAYORDER_MIGRATION.read_text(encoding='utf-8')
        for stmt in (s.strip() for s in raw.split(';')):
            body = '\n'.join(ln for ln in stmt.splitlines() if not ln.lstrip().startswith('--'))
            if body.strip():
                await conn.exec_driver_sql(body)
    s = async_sessionmaker(engine, expire_on_commit=False)()
    try:
        await s.execute(text("DELETE FROM hasn_billing.pay_order WHERE order_no = :o"), {'o': _TEST_ORDER_NO})
        await s.commit()
        yield s
    finally:
        await s.execute(text("DELETE FROM hasn_billing.pay_order WHERE order_no = :o"), {'o': _TEST_ORDER_NO})
        await s.commit()
        await s.close()
        await engine.dispose()
        await async_engine.dispose()


async def test_pay_order_offering_ref_roundtrip_and_dispatch(sess) -> None:
    """下单快照 offering_ref 可持久（JSONB 往返）+ 分发器据其 kind 读回并路由。"""
    ref = {'offering_key': 'llm:tier', 'plan_key': 'pro', 'kind': 'test:mk3roundtrip'}
    order = PayOrder(
        order_no=_TEST_ORDER_NO,
        user_id=999999001,
        order_type='subscribe',
        subject='MK3 往返测试',
        amount=1,
        pay_amount=1,
        expire_time=timezone.now(),
        offering_ref=ref,
    )
    sess.add(order)
    await sess.commit()

    reloaded = (
        await sess.execute(select(PayOrder).where(PayOrder.order_no == _TEST_ORDER_NO))
    ).scalar_one()
    assert reloaded.offering_ref == ref, 'offering_ref JSONB 往返丢失'

    # 分发器据真实持久行的 offering_ref.kind 路由到探针（证明快照可驱动发货）
    routed = []

    async def _probe(o) -> None:  # noqa: ANN001
        routed.append(o.order_no)

    fulfillment.register_fulfillment('test:mk3roundtrip', _probe)
    assert await fulfillment.dispatch_fulfillment(reloaded) is True
    assert routed == [_TEST_ORDER_NO]
