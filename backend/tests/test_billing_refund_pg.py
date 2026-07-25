"""统一商业化内核 MK-9 退款编排层真实 PG 验收（实施/92 MK-9 退款补齐）——零 mock。

退款编排（``pay_order_service.refund_order``）是内核唯一退款入口，本文件把它**从头到尾真实跑通**：
  ① 退款回收注册表（``reverse_fulfillment``）路由 + fail-closed（无 kind / 未注册 → 拒绝退款）；
  ② 五个 kind 的退款回收处理器经各 registrar 真实注册（与发货处理器成对）；
  ③ 应用购买退款端到端（真实 PG）：建已支付订单 + 权益 → refund_order →
     订单 status=2 + refund_amount 落库 + pay_refund status=1 + 权益 revoked；
  ④ 幂等：重复退款返回既有退款记录、不二次扣款/不重复回收；
  ⑤ ``confirm_refund_notify``（渠道异步退款终态确认）幂等 + PROCESSING→SUCCESS 推进。

**外部边界隔离（唯一 seam）**：``refund_order`` 里唯一动真钱的是渠道 SDK 退款网络调用
（``PayOrderService._invoke_channel_refund`` 包 ``client.refund``，会向微信/支付宝发真实退款请求）。
按标准约束「0.01 元真实退款人验属福仔专项，AI 不动真钱」，测试**只隔离这一个第三方网络边界**
（monkeypatch 该 static 方法返回确定性回执），**不 mock 任何业务逻辑**——回收/落库/状态翻转全真实跑 PG。

需本地 PostgreSQL :15432（含 hasn_billing.pay_order/pay_refund/pay_channel + hasn 应用目录/权益表）。
"""

from __future__ import annotations

import uuid

from types import SimpleNamespace
from typing import TYPE_CHECKING, NoReturn

import pytest
import pytest_asyncio
import sqlalchemy as sa

from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

from backend.app.billing.core import fulfillment
from backend.app.billing.crud.crud_pay_refund import pay_refund_dao
from backend.app.billing.model.pay_channel import PayChannel
from backend.app.billing.model.pay_order import PayOrder
from backend.app.billing.service.pay_order_service import PayOrderService, pay_order_service
from backend.app.hasn.model.hasn_app_catalog import HasnAppCatalog
from backend.app.hasn.model.hasn_app_entitlement import HasnAppEntitlement
from backend.app.hasn.model.hasn_humans import HasnHumans
from backend.app.hasn.service.app_purchase_callback import apply_app_purchase
from backend.common.exception import errors
from backend.database.db import SQLALCHEMY_DATABASE_URL
from backend.utils.timezone import timezone

if TYPE_CHECKING:
    from collections.abc import AsyncIterator

    from sqlalchemy.ext.asyncio import AsyncSession

pytestmark = pytest.mark.asyncio


def _uid() -> str:
    return uuid.uuid4().hex[:8]


# ============================ ① 退款回收注册表：路由 + fail-closed（纯逻辑，无 DB）============================


async def test_reverse_fulfillment_routes_to_registered_handler() -> None:
    """注册了退款回收处理器的 kind → 路由到该处理器（收 db + order）。"""
    seen = []

    async def _probe(db, *, order, refund_no=None) -> None:  # noqa: ANN001
        seen.append((db, order.order_no, refund_no))

    fulfillment.register_refund_handler('test:refundprobe', _probe)
    sentinel_db = object()
    order = SimpleNamespace(order_no='r1', order_type=None, offering_ref={'kind': 'test:refundprobe'})
    await fulfillment.reverse_fulfillment(sentinel_db, order, refund_no='RFr1')
    # 回收处理器必须拿到退款单号：积分类回收的幂等键就建在它上面
    assert seen == [(sentinel_db, 'r1', 'RFr1')]


async def test_reverse_fulfillment_fail_closed_no_kind() -> None:
    """订单无 kind（无 offering_ref 且 order_type 不可映射）→ 拒绝退款（不退钱不回收 > 退钱不回收）。"""
    order = SimpleNamespace(order_no='r2', order_type='unknown_type', offering_ref=None)
    with pytest.raises(errors.RequestError, match='无商品类型'):
        await fulfillment.reverse_fulfillment(object(), order)


async def test_reverse_fulfillment_fail_closed_unregistered_kind() -> None:
    """kind 未注册退款回收处理器 → 拒绝退款（避免退钱不回收权益）。"""
    order = SimpleNamespace(order_no='r3', order_type=None, offering_ref={'kind': 'test:refundneverregistered'})
    with pytest.raises(errors.RequestError, match='未注册退款回收处理器'):
        await fulfillment.reverse_fulfillment(object(), order)


def test_all_core_refund_handlers_registered_after_registrars() -> None:
    """五个内核 kind 的退款回收处理器经各 registrar 真实注册（与发货处理器成对）。"""
    from backend.app.billing.service.pay_callbacks import register_callbacks
    from backend.app.hasn.service.app_purchase_callback import register_app_purchase_callback
    from backend.app.hasn.service.app_seat_purchase_callback import register_app_seat_purchase_callback
    from backend.app.hasn_growth.service.lead_pack_callback import register_lead_pack_callback

    register_callbacks()
    register_app_purchase_callback()
    register_app_seat_purchase_callback()
    register_lead_pack_callback()

    kinds = fulfillment.get_registered_refund_kinds()
    assert {
        fulfillment.KIND_LLM_TIER,
        fulfillment.KIND_CREDIT_PACK,
        fulfillment.KIND_APP,
        fulfillment.KIND_SEAT,
        fulfillment.KIND_LEAD_PACK,
    } <= kinds, f'内核退款回收 kind 未齐: {kinds}'


# ============================ 真实 PG 夹具 + 种子 ============================


@pytest_asyncio.fixture
async def db() -> AsyncIterator[AsyncSession]:
    engine = create_async_engine(SQLALCHEMY_DATABASE_URL, poolclass=NullPool)
    try:
        async with engine.connect() as conn:
            await conn.execute(sa.select(1))
    except Exception as exc:
        await engine.dispose()
        pytest.skip(f'本地 PostgreSQL 不可达，跳过: {exc!r}')
    session = async_sessionmaker(engine, expire_on_commit=False)()
    try:
        yield session
    finally:
        await session.rollback()
        await session.close()
        await engine.dispose()


async def _seed_human(db: AsyncSession, *, user_id: int) -> str:
    hasn_id = f'h_rf_{_uid()}{_uid()}'[:38]
    db.add(HasnHumans(hasn_id=hasn_id, star_id=f's{user_id}', user_id=user_id, nickname='退款用户', status='active'))
    await db.flush()
    return hasn_id


async def _seed_channel(db: AsyncSession) -> PayChannel:
    """建一个启用的支付渠道行（退款走 _resolve_channel 需要真实渠道行；SDK 网络调用另行隔离）。"""
    ch = PayChannel(code=f'wx_test_{_uid()}', name='退款测试渠道', status=1)
    db.add(ch)
    await db.flush()
    return ch


async def _seed_app_catalog(db: AsyncSession) -> HasnAppCatalog:
    from decimal import Decimal

    cat = HasnAppCatalog(
        app_id=f'rf_{_uid()}',
        name=f'退款应用 {_uid()}',
        status='published',
        access_type='purchase',
        scope=['personal'],
        purchasable_by='both',
        price_amount=Decimal('39.90'),
        price_unit='cny',
        billing_cycle='month',
    )
    db.add(cat)
    await db.flush()
    return cat


async def _seed_paid_app_order(
    db: AsyncSession, *, user_id: int, channel_code: str, app_id: str, pay_amount: int = 3990
) -> PayOrder:
    """建一笔已支付（status=1）的应用购买订单，携 offering_ref.kind=app。"""
    order = PayOrder(
        order_no=f'RFORDER{_uid()}{_uid()}'[:32],
        user_id=user_id,
        channel_code=channel_code,
        order_type='app_purchase',
        subject='退款应用购买',
        amount=pay_amount,
        pay_amount=pay_amount,
        status=1,  # 已支付
        billing_cycle='month',
        expire_time=timezone.now(),
        extra_data={'app_code': 'huanxing', 'app_id': app_id},
        offering_ref=fulfillment.build_offering_ref('app_purchase', offering_key=f'app:{app_id}', plan_key='standard'),
    )
    db.add(order)
    await db.flush()
    return order


# ============================ ③ 应用购买退款端到端（真实 PG，隔离渠道 SDK）============================


async def test_refund_order_end_to_end_reverses_entitlement(db: AsyncSession, monkeypatch) -> None:
    """已支付应用购买订单退款：**先回收权益、后退钱**（doc94 §4.4 saga·真实 PG）。

    改造后 refund_order 只做数据库能原子完成的事——建退款单（status=0 受理中）+ 回收权益；
    渠道调用移出事务，在额度/权益回收成功之后由 worker 触发。
    这样「退了钱却没回收」与「回收了却没退钱」两种裂口都不会出现。
    """
    # 各 registrar 真实注册退款回收处理器（含 KIND_APP → revoke_app_purchase）
    from backend.app.hasn.service.app_purchase_callback import register_app_purchase_callback

    register_app_purchase_callback()

    user_id = 994_100_000 + int(_uid(), 16) % 100_000
    await _seed_human(db, user_id=user_id)
    ch = await _seed_channel(db)
    cat = await _seed_app_catalog(db)
    order = await _seed_paid_app_order(db, user_id=user_id, channel_code=ch.code, app_id=cat.app_id)

    # 发货：写 owner 维度 active 权益（退款要回收的正是它）
    ent = await apply_app_purchase(db, order=order)
    assert ent is not None and ent.status == 'active'

    # 隔离唯一动真钱的外部边界：渠道 SDK 退款网络调用（返回确定性回执，不发真实退款请求）
    captured: dict = {}

    def _fake_channel_refund(channel, merchant_config, *, order_no, refund_no, refund_amount, total_amount, reason):  # noqa: ANN001
        captured.update(order_no=order_no, refund_no=refund_no, refund_amount=refund_amount, total_amount=total_amount)
        return {'channel_refund_no': f'CH{refund_no}', 'refund_status': 'SUCCESS'}

    monkeypatch.setattr(PayOrderService, '_invoke_channel_refund', staticmethod(_fake_channel_refund))

    result = await pay_order_service.refund_order(db=db, order_no=order.order_no, reason='用户申请退款')

    # 退款结果：确定性退款单号 RF{order_no}、受理中（渠道尚未调用）、非幂等重放
    assert result.refund_no == f'RF{order.order_no}'
    assert result.status == 0 and result.already_refunded is False
    assert result.refund_amount == order.pay_amount
    # 关键：事务里绝不调渠道——退钱必须排在回收之后
    assert captured == {}, '退款受理阶段不得调用支付渠道'

    # pay_refund 记录落库（受理中）
    refund_row = await pay_refund_dao.get_by_refund_no(db, f'RF{order.order_no}')
    assert refund_row is not None and refund_row.status == 0
    assert refund_row.fulfillment_status == 'pending'
    assert refund_row.user_id == user_id

    # 权益已回收（fail-closed 的核心：退钱必回收）
    ent_after = (
        await db.execute(sa.select(HasnAppEntitlement).where(HasnAppEntitlement.id == ent.id))
    ).scalar_one()
    assert ent_after.status == 'revoked', '退款后应用权益必须撤销'


async def test_refund_order_idempotent_second_call(db: AsyncSession, monkeypatch) -> None:
    """重复退款：第二次返回既有退款记录（already_refunded），不二次调渠道/不重复落 pay_refund。"""
    from backend.app.hasn.service.app_purchase_callback import register_app_purchase_callback

    register_app_purchase_callback()

    user_id = 994_200_000 + int(_uid(), 16) % 100_000
    await _seed_human(db, user_id=user_id)
    ch = await _seed_channel(db)
    cat = await _seed_app_catalog(db)
    order = await _seed_paid_app_order(db, user_id=user_id, channel_code=ch.code, app_id=cat.app_id)
    await apply_app_purchase(db, order=order)

    call_count = {'n': 0}

    def _fake_channel_refund(channel, merchant_config, **kwargs):  # noqa: ANN001
        call_count['n'] += 1
        return {'channel_refund_no': 'CH1', 'refund_status': 'SUCCESS'}

    monkeypatch.setattr(PayOrderService, '_invoke_channel_refund', staticmethod(_fake_channel_refund))

    first = await pay_order_service.refund_order(db=db, order_no=order.order_no)
    assert first.already_refunded is False and call_count['n'] == 0, '受理阶段不调渠道'

    # 第二次退款：同一张退款单已在途 → 返回当前进度，不重复建单、不调渠道
    second = await pay_order_service.refund_order(db=db, order_no=order.order_no)
    assert second.refund_no == first.refund_no
    assert second.status == 0
    assert call_count['n'] == 0, '重复受理不得触发渠道退款'

    # pay_refund 只有一行
    rows = (
        await db.execute(
            sa.select(sa.func.count())
            .select_from(pay_refund_dao.model)
            .where(pay_refund_dao.model.refund_no == f'RF{order.order_no}')
        )
    ).scalar_one()
    assert rows == 1, '幂等退款不得重复落 pay_refund'


async def test_refund_rejects_unpaid_order(db: AsyncSession, monkeypatch) -> None:
    """未支付（status=0）订单不可退款——拒绝，且绝不调渠道 SDK。"""
    from backend.app.hasn.service.app_purchase_callback import register_app_purchase_callback

    register_app_purchase_callback()

    user_id = 994_300_000 + int(_uid(), 16) % 100_000
    await _seed_human(db, user_id=user_id)
    ch = await _seed_channel(db)
    cat = await _seed_app_catalog(db)
    order = await _seed_paid_app_order(db, user_id=user_id, channel_code=ch.code, app_id=cat.app_id)
    order.status = 0  # 改为待支付
    await db.flush()

    def _must_not_call(channel, merchant_config, **kwargs) -> NoReturn:  # noqa: ANN001
        raise AssertionError('未支付订单不得触达渠道退款')

    monkeypatch.setattr(PayOrderService, '_invoke_channel_refund', staticmethod(_must_not_call))

    with pytest.raises(errors.RequestError, match='只有已支付订单可退款'):
        await pay_order_service.refund_order(db=db, order_no=order.order_no)


# ============================ ⑤ 退款回调终态确认：幂等 + PROCESSING→SUCCESS ============================


async def test_confirm_refund_notify_idempotent_on_success(db: AsyncSession, monkeypatch) -> None:
    """退款已同步成功（status=1）后再收 SUCCESS 回调 → 幂等无变更（返回 False）。"""
    from backend.app.hasn.service.app_purchase_callback import register_app_purchase_callback

    register_app_purchase_callback()

    user_id = 994_400_000 + int(_uid(), 16) % 100_000
    await _seed_human(db, user_id=user_id)
    ch = await _seed_channel(db)
    cat = await _seed_app_catalog(db)
    order = await _seed_paid_app_order(db, user_id=user_id, channel_code=ch.code, app_id=cat.app_id)
    await apply_app_purchase(db, order=order)

    monkeypatch.setattr(
        PayOrderService,
        '_invoke_channel_refund',
        staticmethod(lambda *a, **k: {'channel_refund_no': 'CH1', 'refund_status': 'SUCCESS'}),
    )
    await pay_order_service.refund_order(db=db, order_no=order.order_no)

    # 退款改为 saga（doc94 §4.4）：refund_order 只受理（status=0），渠道调用发生在额度回收成功之后。
    # 因此第一次 SUCCESS 回调会把退款单推进到成功终态，第二次才是幂等无变更。
    first = await pay_order_service.confirm_refund_notify(
        db=db, refund_no=f'RF{order.order_no}', refund_status='SUCCESS'
    )
    assert first is True, '首次 SUCCESS 回调应把受理中的退款单推进到成功'
    changed = await pay_order_service.confirm_refund_notify(
        db=db, refund_no=f'RF{order.order_no}', refund_status='SUCCESS'
    )
    assert changed is False, '退款已成功，重复回调应幂等无变更'


async def test_confirm_refund_notify_advances_processing_to_success(db: AsyncSession) -> None:
    """渠道异步退款：先落 PROCESSING(status=0)，收 SUCCESS 回调 → 推进 status=1 + 订单置退款态。"""
    user_id = 994_500_000 + int(_uid(), 16) % 100_000
    await _seed_human(db, user_id=user_id)
    ch = await _seed_channel(db)
    cat = await _seed_app_catalog(db)
    order = await _seed_paid_app_order(db, user_id=user_id, channel_code=ch.code, app_id=cat.app_id)

    # 模拟「渠道退款受理中」：pay_refund status=0 待处理，订单仍 status=1
    refund_no = f'RF{order.order_no}'
    await pay_refund_dao.create(
        db,
        {
            'refund_no': refund_no,
            'order_no': order.order_no,
            'user_id': user_id,
            'refund_amount': order.pay_amount,
            'channel_code': ch.code,
            'status': 0,
        },
    )

    changed = await pay_order_service.confirm_refund_notify(
        db=db, refund_no=refund_no, refund_status='SUCCESS', channel_refund_no='CH_ASYNC_1'
    )
    assert changed is True, 'PROCESSING→SUCCESS 应有状态变更'

    refund_row = await pay_refund_dao.get_by_refund_no(db, refund_no)
    assert refund_row.status == 1 and refund_row.channel_refund_no == 'CH_ASYNC_1'

    reloaded = (
        await db.execute(sa.select(PayOrder).where(PayOrder.order_no == order.order_no))
    ).scalar_one()
    assert reloaded.status == 2, '退款回调成功后订单应置已退款(2)'


async def test_confirm_refund_notify_unknown_refund_no_ignored(db: AsyncSession) -> None:
    """收到未知退款单号的回调（非本内核发起）→ 记日志忽略，返回 False，不抛。"""
    changed = await pay_order_service.confirm_refund_notify(
        db=db, refund_no=f'RF_UNKNOWN_{_uid()}', refund_status='SUCCESS'
    )
    assert changed is False
