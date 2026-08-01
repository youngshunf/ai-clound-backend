"""通用下单入参（G2）+ 通用报价端点（G4）真实 PG 验收——零 mock（实施/95）。

本文件要证明的**唯一一件事**：新增一个加购/买断型商品，后端退化为「加两行库 + 注册一个
feature_key」，不必碰支付主干、不必碰前端契约。

因此最关键的用例是 `test_v1_fictional_offering_walks_the_whole_chain_without_code_change`：
它用一个**与云端节点毫无关系的虚构商品**（本文件里只出现库行，代码里一个字符都没为它写过）
走完 **报价端点可见 → 通用下单 → 支付回调 → 权益落行 → `resolve_access` 放行 → 配额生效**。
这条一旦要靠改代码才能过，就说明「通用」是假的。

其余守卫：

- **V7 防篡改**：`CreatePayOrderParam` 里**根本没有价格字段**，客户端塞 `price_amount` /
  `pay_amount` / `amount` 一律被丢弃，金额永远从 `billing_plan.price_amount` 算；
- 下单校验的每个失败分支各自给**明确 4xx**（商品不存在/已下架/档位不存在/档位已下架/
  feature_key 未注册/kind 无履约处理器/份数非正/非现金计价/免费档），绝不静默下单；
- 报价端点 `kind` **必填**——`billing_offering` 没有 `app_code` 列（doc05 §3），不收窄就会把
  多条产品线混在一起返回，前端直接渲染即重演桌面端串档事故；
- 存量五个 `order_type` 的入参校验与 kind 映射**逐条不变**（通用路是新增，不是改写）。

支付渠道 SDK（微信/支付宝线上接口）是外部网络边界，本文件走
`PayOrderService.prepare_offering_order`——它是 `_create_offering_order` 里除 SDK 调用外的
**全部生产代码**（目录读 → 定价 → 落单）。数据库、路由、信封、履约分发、付费墙判定全是真实实现。

真实 PostgreSQL :15432（`hasn_billing` + `public.hasn_app_entitlement`），全程在事务内跑完回滚。
"""

from __future__ import annotations

import uuid

from decimal import Decimal
from types import SimpleNamespace
from typing import TYPE_CHECKING, Any

import httpx
import pytest
import pytest_asyncio
import sqlalchemy as sa

from fastapi import FastAPI
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool
from starlette_context.middleware import ContextMiddleware
from starlette_context.plugins import RequestIdPlugin

from backend.app.billing.api.v1.open.pricing import router as open_pricing_router
from backend.app.billing.core import fulfillment
from backend.app.billing.model.billing_offering import BillingOffering
from backend.app.billing.model.billing_plan import BillingPlan
from backend.app.billing.model.pay_channel import PayChannel
from backend.app.billing.schema.pay_order import CreatePayOrderParam
from backend.app.billing.service.access_service import resolve_access
from backend.app.billing.service.feature_plan_callback import FULFILLED_GRANT_KEY
from backend.app.billing.service.pay_order_service import PayOrderService
from backend.app.hasn.model.hasn_app_entitlement import HasnAppEntitlement
from backend.app.hasn.model.hasn_humans import HasnHumans
from backend.common.exception import errors
from backend.common.exception.exception_handler import register_exception
from backend.database.db import SQLALCHEMY_DATABASE_URL, async_engine, get_db

if TYPE_CHECKING:
    from collections.abc import AsyncIterator

    from sqlalchemy.ext.asyncio import AsyncSession

pytestmark = pytest.mark.asyncio

# ── 虚构商品：本文件之外没有任何代码认识它，只有下面这几行库行 ──
# feature_key 走 `workflow_template:` 这个已注册前缀，满足下单口的防漂移闸，
# 又不会污染 `feature_registry.validate_offering_consistency` 的一致性校验。
PROBE_OFFERING = 'feature:g95probe'
PROBE_FEATURE = 'workflow_template:g95_probe'
PROBE_MONTHLY_PRICE = Decimal('66.00')
PROBE_LIFETIME_PRICE = Decimal('660.00')

# 同一目录里另一条产品线（不同 kind）：用来证明报价端点不会把不同 kind 混着回
OTHER_KIND_OFFERING = 'credits:g95probe'

_QUOTES = '/api/v1/user_tier/open/offerings'


def _uid() -> str:
    return uuid.uuid4().hex[:10]


# ─── 真实 HTTP 壳（只装报价路由；信封/序列化/查询参数校验全走真实实现） ───

_APP = FastAPI()
_APP.add_middleware(ContextMiddleware, plugins=(RequestIdPlugin(),))
register_exception(_APP)
_APP.include_router(open_pricing_router, prefix='/api/v1/user_tier/open')


def _register_all_fulfillment_registrars() -> None:
    """按应用启动同款顺序真实调用各 registrar（下单口要查 kind 有没有履约处理器）。"""
    from backend.app.billing.service.feature_plan_callback import register_feature_plan_callback
    from backend.app.billing.service.pay_callbacks import register_callbacks
    from backend.app.hasn.service.app_purchase_callback import register_app_purchase_callback
    from backend.app.hasn.service.app_seat_purchase_callback import register_app_seat_purchase_callback
    from backend.app.hasn_growth.service.lead_pack_callback import register_lead_pack_callback

    register_callbacks()
    register_app_purchase_callback()
    register_app_seat_purchase_callback()
    register_lead_pack_callback()
    register_feature_plan_callback()


@pytest_asyncio.fixture
async def env() -> AsyncIterator[Any]:
    await async_engine.dispose()
    engine = create_async_engine(SQLALCHEMY_DATABASE_URL, poolclass=NullPool)
    try:
        async with engine.connect() as conn:
            await conn.execute(sa.select(1))
    except Exception as exc:
        await engine.dispose()
        pytest.skip(f'本地 PostgreSQL 不可达，跳过: {exc!r}')

    _register_all_fulfillment_registrars()
    session = async_sessionmaker(engine, expire_on_commit=False)()

    async def _yield_session() -> AsyncIterator[AsyncSession]:  # noqa: RUF029  # FastAPI 依赖要求异步生成器
        yield session

    _APP.dependency_overrides[get_db] = _yield_session
    client = httpx.AsyncClient(transport=httpx.ASGITransport(app=_APP), base_url='http://e2e')
    try:
        yield SimpleNamespace(db=session, client=client)
    finally:
        await client.aclose()
        _APP.dependency_overrides.clear()
        # 全部改动只活在事务里，回滚即彻底清场（不留测试数据污染商品目录）
        await session.rollback()
        await session.close()
        await engine.dispose()
        await async_engine.dispose()


def _data(resp: httpx.Response) -> Any:
    assert resp.status_code == 200, f'{resp.request.method} {resp.request.url} -> {resp.status_code}: {resp.text}'
    body = resp.json()
    assert body.get('code') == 200, f'envelope 非 200: {body}'
    return body['data']


# ─── 种子（全部是「只加库行」，没有一行是为它写的代码） ───


async def _seed_owner(db: AsyncSession) -> tuple[int, str]:
    user_id = 940_000_000 + int(uuid.uuid4().int % 1_000_000)
    hasn_id = f'h_g95_{_uid()}'
    db.add(
        HasnHumans(
            hasn_id=hasn_id, star_id=f's_g95_{_uid()}', user_id=user_id, nickname='加购用户', status='active'
        )
    )
    await db.flush()
    return user_id, hasn_id


async def _seed_channel(db: AsyncSession) -> str:
    """一条真实可用的支付渠道行（下单要按 code 解析渠道；SDK 不在本文件调用）。"""
    code = f'wx_native_g95_{_uid()}'[:32]
    db.add(PayChannel(code=code, name='验收渠道', status=1, config={'mchid': 'acceptance-only'}))
    await db.flush()
    return code


async def _seed_probe_offering(db: AsyncSession, *, status: str = 'active') -> None:
    """虚构 `feature_plan` 商品：两个档（月付 ¥66 / 一次性买断 ¥660）。"""
    db.add(
        BillingOffering(
            key=PROBE_OFFERING,
            kind=fulfillment.KIND_FEATURE_PLAN,
            feature_key=PROBE_FEATURE,
            display_name='通用闭环探针商品',
            status=status,
            source='platform',
            sort_order=990,
        )
    )
    db.add(
        BillingPlan(
            offering_key=PROBE_OFFERING,
            plan_key='monthly',
            price_amount=PROBE_MONTHLY_PRICE,
            price_unit='cny',
            cycle='month',
            quota_json={'max_probe_slots': 2, 'tier': 'probe_monthly'},
            trial_json={'enabled': False},
            grace_json={},
            display_json={'display_name': '按月'},
            status='active',
            sort_order=1,
        )
    )
    db.add(
        BillingPlan(
            offering_key=PROBE_OFFERING,
            plan_key='lifetime',
            price_amount=PROBE_LIFETIME_PRICE,
            price_unit='cny',
            cycle='once',
            quota_json={'max_probe_slots': 5},
            trial_json={'enabled': False},
            grace_json={},
            display_json={'display_name': '买断'},
            status='active',
            sort_order=2,
        )
    )
    await db.flush()


async def _seed_other_kind_offering(db: AsyncSession) -> None:
    """另一条产品线（kind=credit_pack）：报价端点按 kind 收窄时不得混进来。"""
    db.add(
        BillingOffering(
            key=OTHER_KIND_OFFERING,
            kind=fulfillment.KIND_CREDIT_PACK,
            feature_key='credits:topup',
            display_name='探针积分包',
            status='active',
            source='platform',
            sort_order=991,
        )
    )
    db.add(
        BillingPlan(
            offering_key=OTHER_KIND_OFFERING,
            plan_key='probe_pack',
            price_amount=Decimal('10.00'),
            price_unit='cny',
            cycle='once',
            quota_json={'credits': 100},
            trial_json={},
            grace_json={},
            display_json={},
            status='active',
            sort_order=1,
        )
    )
    await db.flush()


def _order_param(**overrides: Any) -> CreatePayOrderParam:
    payload: dict[str, Any] = {
        'order_type': fulfillment.ORDER_TYPE_OFFERING,
        'channel_code': overrides.pop('channel_code'),
        'offering_key': PROBE_OFFERING,
        'plan_key': 'monthly',
    }
    payload.update(overrides)
    return CreatePayOrderParam(**payload)


async def _entitlements(db: AsyncSession, *, feature_key: str, subject_id: str) -> list[HasnAppEntitlement]:
    return list(
        (
            await db.execute(
                sa.select(HasnAppEntitlement).where(
                    HasnAppEntitlement.feature_key == feature_key,
                    HasnAppEntitlement.subject_id == subject_id,
                )
            )
        ).scalars().all()
    )


# ══════════════════ V1（最关键）：只加库行跑通全链路 ══════════════════


async def test_v1_fictional_offering_walks_the_whole_chain_without_code_change(env: Any) -> None:
    """**V1**：虚构商品只加 offering/plan 行 + 一个已注册前缀的 feature_key，走完全链路。

    报价端点可见 → 通用下单（金额从目录读）→ 支付回调 → 权益落行 → `resolve_access` 放行 → 配额生效。

    本用例里没有一行代码认识 `feature:g95probe`：它不在任何常量、任何 if 分支、任何 handler 里。
    这条过了才谈得上「新商品＝加库行」；一旦要靠改代码才能过，说明还是特例实现。
    """
    db = env.db
    user_id, owner = await _seed_owner(db)
    channel_code = await _seed_channel(db)
    await _seed_probe_offering(db)

    # ① 报价端点可见：前端拿得到价格、周期、配额、购买深链
    quotes = _data(await env.client.get(_QUOTES, params={'kind': 'feature_plan', 'offering_key': PROBE_OFFERING}))
    assert len(quotes) == 1, f'虚构商品应在通用报价面可见: {quotes}'
    quote = quotes[0]
    assert quote['offering_key'] == PROBE_OFFERING
    assert quote['feature_key'] == PROBE_FEATURE
    assert quote['kind'] == 'feature_plan'
    assert quote['purchase_uri'] == f'hasn://billing/offering/{PROBE_OFFERING}'
    monthly = next(p for p in quote['plans'] if p['plan_key'] == 'monthly')
    assert Decimal(str(monthly['price_amount'])) == PROBE_MONTHLY_PRICE
    assert monthly['cycle'] == 'month'
    assert monthly['quota_json']['max_probe_slots'] == 2

    # ② 通用下单：请求只说「买哪个商品的哪个档、几份」，价格由服务端从目录算
    obj = _order_param(channel_code=channel_code, quantity=3)
    order, channel, _merchant = await PayOrderService.prepare_offering_order(
        db=db, user_id=user_id, obj=obj, user_ip='127.0.0.1', app_code='huanxing'
    )
    assert channel.code == channel_code
    assert order.order_type == 'offering'
    assert order.pay_amount == int(PROBE_MONTHLY_PRICE * 100) * 3 == 19800
    assert order.billing_cycle == 'month'
    assert (order.extra_data or {})['quantity'] == 3
    # kind 取自目录实际的 offering.kind，而不是由 order_type 钉死
    assert order.offering_ref == {
        'offering_key': PROBE_OFFERING,
        'plan_key': 'monthly',
        'kind': 'feature_plan',
    }

    # ③ 支付回调：走生产回调入口（订单置已支付 + 按 kind 分发履约，同事务）
    changed = await PayOrderService.handle_pay_notify(
        db=db,
        order_no=order.order_no,
        channel_order_no=f'ch_{_uid()}',
        pay_amount=order.pay_amount,
        channel_code=channel_code,
        raw_data='{"acceptance": true}',
    )
    assert changed is True

    # ④ 权益落行：一条行、配额 = 档位值 × 份数
    rows = await _entitlements(db, feature_key=PROBE_FEATURE, subject_id=owner)
    assert len(rows) == 1, f'应只发一条权益行，实得 {len(rows)}'
    assert rows[0].quota_json['max_probe_slots'] == 2 * 3
    assert rows[0].status == 'active'
    assert rows[0].order_ref == order.order_no
    assert order.fulfillment_status == 'succeeded'
    assert (order.extra_data or {})[FULFILLED_GRANT_KEY]['feature_key'] == PROBE_FEATURE

    # ⑤ 付费墙放行 + 配额可读
    decision = await resolve_access(db, feature_key=PROBE_FEATURE, subject_type='owner', subject_id=owner)
    assert decision.allowed is True
    assert decision.reason == 'entitled'
    assert decision.quota is not None
    assert decision.quota.snapshot['max_probe_slots'] == 6


async def test_v1_buyout_plan_grants_permanent_entitlement(env: Any) -> None:
    """同一虚构商品的买断档（`cycle=once`）也只加库行：金额取买断价、权益 `expires_at` 为空（永久）。"""
    db = env.db
    user_id, owner = await _seed_owner(db)
    channel_code = await _seed_channel(db)
    await _seed_probe_offering(db)

    obj = _order_param(channel_code=channel_code, plan_key='lifetime')
    order, _channel, _merchant = await PayOrderService.prepare_offering_order(
        db=db, user_id=user_id, obj=obj, app_code='huanxing'
    )
    assert order.pay_amount == int(PROBE_LIFETIME_PRICE * 100)
    assert order.billing_cycle == 'once'

    await PayOrderService.handle_pay_notify(
        db=db,
        order_no=order.order_no,
        channel_order_no=f'ch_{_uid()}',
        pay_amount=order.pay_amount,
        channel_code=channel_code,
    )
    rows = await _entitlements(db, feature_key=PROBE_FEATURE, subject_id=owner)
    assert len(rows) == 1
    assert rows[0].expires_at is None, 'once 买断必须落成永久（NULL）'
    assert rows[0].quota_json['max_probe_slots'] == 5


# ══════════════════ V7：客户端传价格一律被忽略 ══════════════════


def test_v7_create_param_has_no_price_field_at_all() -> None:
    """**V7 第一层**：入参 schema 里**根本没有价格字段**，客户端塞进来的价格被直接丢弃。

    「忽略」不是靠服务端记得别读，而是靠这个字段压根不存在——没有字段就没有篡改面。
    """
    obj = CreatePayOrderParam(
        order_type='offering',
        channel_code='wx_native',
        offering_key=PROBE_OFFERING,
        plan_key='monthly',
        # 下面这些是攻击者可能塞进来的价格字段，一个都不该被吸收
        price_amount=1,
        pay_amount=1,
        amount=1,
        price=1,
        unit_price_fen=1,
    )
    dumped = obj.model_dump()
    for banned in ('price_amount', 'pay_amount', 'amount', 'price', 'unit_price_fen'):
        assert not hasattr(obj, banned), f'入参不该存在价格字段 {banned}'
        assert banned not in dumped, f'入参序列化后不该带出价格字段 {banned}'
        assert banned not in CreatePayOrderParam.model_fields, f'入参 schema 不该声明价格字段 {banned}'


async def test_v7_client_supplied_price_is_ignored_amount_comes_from_catalog(env: Any) -> None:
    """**V7 第二层**：带着一堆伪造价格字段真下一单，金额仍严格等于「目录单价 × 份数」。"""
    db = env.db
    user_id, _owner = await _seed_owner(db)
    channel_code = await _seed_channel(db)
    await _seed_probe_offering(db)

    obj = CreatePayOrderParam(
        order_type='offering',
        channel_code=channel_code,
        offering_key=PROBE_OFFERING,
        plan_key='monthly',
        quantity=2,
        price_amount=Decimal('0.01'),
        pay_amount=1,
        amount=1,
    )
    order, _channel, _merchant = await PayOrderService.prepare_offering_order(
        db=db, user_id=user_id, obj=obj, app_code='huanxing'
    )
    expected = int(PROBE_MONTHLY_PRICE * 100) * 2
    assert order.pay_amount == expected == 13200, '金额必须从目录读，客户端出价无效'
    assert order.amount == expected
    assert (order.extra_data or {})['unit_price_fen'] == int(PROBE_MONTHLY_PRICE * 100)


# ══════════════════ 下单校验：每个失败分支各自明确 4xx ══════════════════


async def test_order_rejects_unknown_offering(env: Any) -> None:
    """商品不存在 → 明确 4xx，不落单。"""
    db = env.db
    user_id, _owner = await _seed_owner(db)
    channel_code = await _seed_channel(db)
    obj = _order_param(channel_code=channel_code, offering_key='feature:never_seeded')
    with pytest.raises(errors.RequestError, match='商品不存在'):
        await PayOrderService.prepare_offering_order(db=db, user_id=user_id, obj=obj, app_code='huanxing')


async def test_order_rejects_inactive_offering(env: Any) -> None:
    """商品已下架 → 明确 4xx（下架即不可新购；已付款订单的履约另有口径，不受此限）。"""
    db = env.db
    user_id, _owner = await _seed_owner(db)
    channel_code = await _seed_channel(db)
    await _seed_probe_offering(db, status='inactive')
    obj = _order_param(channel_code=channel_code)
    with pytest.raises(errors.RequestError, match='已下架'):
        await PayOrderService.prepare_offering_order(db=db, user_id=user_id, obj=obj, app_code='huanxing')


async def test_order_rejects_unknown_plan(env: Any) -> None:
    """档位不存在 → 明确 4xx（猜一个档位等于按错价卖货）。"""
    db = env.db
    user_id, _owner = await _seed_owner(db)
    channel_code = await _seed_channel(db)
    await _seed_probe_offering(db)
    obj = _order_param(channel_code=channel_code, plan_key='never_seeded')
    with pytest.raises(errors.RequestError, match='档位不存在或已下架'):
        await PayOrderService.prepare_offering_order(db=db, user_id=user_id, obj=obj, app_code='huanxing')


async def test_order_rejects_inactive_plan(env: Any) -> None:
    """档位已下架 → 与「不存在」同样拒绝：下架档不能继续卖。"""
    db = env.db
    user_id, _owner = await _seed_owner(db)
    channel_code = await _seed_channel(db)
    await _seed_probe_offering(db)
    await db.execute(
        sa.update(BillingPlan)
        .where(BillingPlan.offering_key == PROBE_OFFERING, BillingPlan.plan_key == 'monthly')
        .values(status='inactive')
    )
    obj = _order_param(channel_code=channel_code)
    with pytest.raises(errors.RequestError, match='档位不存在或已下架'):
        await PayOrderService.prepare_offering_order(db=db, user_id=user_id, obj=obj, app_code='huanxing')


async def test_order_rejects_unregistered_feature_key(env: Any) -> None:
    """`feature_key` 未在 `feature_registry` 注册 → 拒绝下单。

    放过去的话，用户会付钱拿到一条付费墙**永远判不出准入**的幽灵权益。
    """
    db = env.db
    user_id, _owner = await _seed_owner(db)
    channel_code = await _seed_channel(db)
    await _seed_probe_offering(db)
    await db.execute(
        sa.update(BillingOffering)
        .where(BillingOffering.key == PROBE_OFFERING)
        .values(feature_key='totally:unregistered')
    )
    obj = _order_param(channel_code=channel_code)
    with pytest.raises(errors.RequestError, match='未注册'):
        await PayOrderService.prepare_offering_order(db=db, user_id=user_id, obj=obj, app_code='huanxing')


async def test_order_rejects_kind_without_fulfillment_handler(env: Any) -> None:
    """商品 kind 没有履约处理器 → 下单口就拒。

    与其让用户先付款、再在回调里 fail-closed 进 `fulfillment_status=dead`，不如在这里 4xx。
    """
    db = env.db
    user_id, _owner = await _seed_owner(db)
    channel_code = await _seed_channel(db)
    await _seed_probe_offering(db)
    await db.execute(
        sa.update(BillingOffering).where(BillingOffering.key == PROBE_OFFERING).values(kind='no_handler_kind')
    )
    assert 'no_handler_kind' not in fulfillment.get_registered_kinds()
    obj = _order_param(channel_code=channel_code)
    with pytest.raises(errors.RequestError, match='缺履约处理器'):
        await PayOrderService.prepare_offering_order(db=db, user_id=user_id, obj=obj, app_code='huanxing')


async def test_order_rejects_non_cash_priced_plan(env: Any) -> None:
    """以积分计价的档位不能走现金渠道：`int(价 × 100)` 这个换算只对「元」成立。"""
    db = env.db
    user_id, _owner = await _seed_owner(db)
    channel_code = await _seed_channel(db)
    await _seed_probe_offering(db)
    await db.execute(
        sa.update(BillingPlan)
        .where(BillingPlan.offering_key == PROBE_OFFERING, BillingPlan.plan_key == 'monthly')
        .values(price_unit='credits')
    )
    obj = _order_param(channel_code=channel_code)
    with pytest.raises(errors.RequestError, match='不支持现金支付下单'):
        await PayOrderService.prepare_offering_order(db=db, user_id=user_id, obj=obj, app_code='huanxing')


async def test_order_rejects_free_plan(env: Any) -> None:
    """免费档位无需支付 → 明确 4xx，不产生 0 元订单。"""
    db = env.db
    user_id, _owner = await _seed_owner(db)
    channel_code = await _seed_channel(db)
    await _seed_probe_offering(db)
    await db.execute(
        sa.update(BillingPlan)
        .where(BillingPlan.offering_key == PROBE_OFFERING, BillingPlan.plan_key == 'monthly')
        .values(price_amount=Decimal(0))
    )
    obj = _order_param(channel_code=channel_code)
    with pytest.raises(errors.RequestError, match='免费档'):
        await PayOrderService.prepare_offering_order(db=db, user_id=user_id, obj=obj, app_code='huanxing')


@pytest.mark.parametrize('quantity', [0, -1, -100])
def test_order_param_rejects_non_positive_quantity(quantity: int) -> None:
    """份数非正 → 入参校验直接拒（422），不会走到定价。"""
    with pytest.raises(ValueError, match='购买份数必须为正整数'):
        CreatePayOrderParam(
            order_type='offering',
            channel_code='wx_native',
            offering_key=PROBE_OFFERING,
            plan_key='monthly',
            quantity=quantity,
        )


@pytest.mark.parametrize(
    ('missing', 'expect'),
    [('offering_key', 'offering_key'), ('plan_key', 'plan_key')],
)
def test_order_param_requires_offering_and_plan_key(missing: str, expect: str) -> None:
    """通用路必须同时给 `offering_key` 与 `plan_key`，缺一即拒。"""
    payload = {
        'order_type': 'offering',
        'channel_code': 'wx_native',
        'offering_key': PROBE_OFFERING,
        'plan_key': 'monthly',
    }
    payload.pop(missing)
    with pytest.raises(ValueError, match=expect):
        CreatePayOrderParam(**payload)


async def test_service_layer_self_guards_missing_keys(env: Any) -> None:
    """service 层自持：绕过 schema 直接构造缺键入参也拒（service 可能被别的入口直接调用）。"""
    db = env.db
    user_id, _owner = await _seed_owner(db)
    channel_code = await _seed_channel(db)
    obj = _order_param(channel_code=channel_code)
    obj.offering_key = None
    with pytest.raises(errors.RequestError, match='offering_key'):
        await PayOrderService.prepare_offering_order(db=db, user_id=user_id, obj=obj, app_code='huanxing')

    obj2 = _order_param(channel_code=channel_code)
    obj2.quantity = 0
    with pytest.raises(errors.RequestError, match='份数必须为正整数'):
        await PayOrderService.prepare_offering_order(db=db, user_id=user_id, obj=obj2, app_code='huanxing')


# ══════════════════ G4 报价端点 ══════════════════


async def test_quote_endpoint_requires_kind(env: Any) -> None:
    """**kind 必填**：不带过滤直接 422。

    `billing_offering` 没有 `app_code` 列（doc05 §3），全量返回会把多条产品线混在一起，
    前端一旦直接渲染就重演桌面端串档事故。接口层堵死这条路，比指望每个调用方自觉过滤可靠。
    """
    resp = await env.client.get(_QUOTES)
    assert resp.status_code == 422, f'kind 必填，缺失应 422: {resp.status_code} {resp.text}'


async def test_quote_endpoint_rejects_unknown_kind(env: Any) -> None:
    """kind 拼错 → 明确 4xx，而不是回空列表让前端把加购区渲染成「暂无商品」。"""
    resp = await env.client.get(_QUOTES, params={'kind': 'feature_plans'})
    assert resp.status_code == 400, resp.text
    assert '未知的商品种类' in resp.text


async def test_quote_endpoint_does_not_mix_kinds(env: Any) -> None:
    """按 kind 过滤正确：`kind=feature_plan` 里绝不出现别的 kind 的商品。"""
    db = env.db
    await _seed_probe_offering(db)
    await _seed_other_kind_offering(db)

    feature_plans = _data(await env.client.get(_QUOTES, params={'kind': 'feature_plan'}))
    keys = {item['offering_key'] for item in feature_plans}
    assert PROBE_OFFERING in keys
    assert OTHER_KIND_OFFERING not in keys, '不同 kind 的商品不得混进同一次报价'
    assert {item['kind'] for item in feature_plans} == {'feature_plan'}

    packs = _data(await env.client.get(_QUOTES, params={'kind': 'credit_pack'}))
    pack_keys = {item['offering_key'] for item in packs}
    assert OTHER_KIND_OFFERING in pack_keys
    assert PROBE_OFFERING not in pack_keys


async def test_quote_endpoint_narrows_by_offering_key(env: Any) -> None:
    """`offering_key` 进一步收窄到单个商品——调用方明确知道自己卖什么时的推荐用法。"""
    db = env.db
    await _seed_probe_offering(db)
    items = _data(await env.client.get(_QUOTES, params={'kind': 'feature_plan', 'offering_key': PROBE_OFFERING}))
    assert [item['offering_key'] for item in items] == [PROBE_OFFERING]


async def test_quote_endpoint_hides_inactive_by_default(env: Any) -> None:
    """默认只回 active；`status=all` 才带上下架商品（admin 预览用）。"""
    db = env.db
    await _seed_probe_offering(db, status='inactive')

    active_only = _data(await env.client.get(_QUOTES, params={'kind': 'feature_plan'}))
    assert PROBE_OFFERING not in {item['offering_key'] for item in active_only}

    everything = _data(await env.client.get(_QUOTES, params={'kind': 'feature_plan', 'status': 'all'}))
    assert PROBE_OFFERING in {item['offering_key'] for item in everything}

    resp = await env.client.get(_QUOTES, params={'kind': 'feature_plan', 'status': 'bogus'})
    assert resp.status_code == 400 and '未知的状态过滤' in resp.text


async def test_quote_endpoint_hides_inactive_plans(env: Any) -> None:
    """下架的档位不出现在报价里——报价面上不该有「商品上架、档位下架」的半截购买卡。"""
    db = env.db
    await _seed_probe_offering(db)
    await db.execute(
        sa.update(BillingPlan)
        .where(BillingPlan.offering_key == PROBE_OFFERING, BillingPlan.plan_key == 'lifetime')
        .values(status='inactive')
    )
    items = _data(await env.client.get(_QUOTES, params={'kind': 'feature_plan', 'offering_key': PROBE_OFFERING}))
    assert [p['plan_key'] for p in items[0]['plans']] == ['monthly']


async def test_quote_endpoint_carries_full_purchase_card_payload(env: Any) -> None:
    """出参足以渲染购买卡：价格/单位/周期/配额/试用/展示字段/购买深链一个都不少。"""
    db = env.db
    await _seed_probe_offering(db)
    items = _data(await env.client.get(_QUOTES, params={'kind': 'feature_plan', 'offering_key': PROBE_OFFERING}))
    item = items[0]
    assert set(item) >= {
        'offering_key',
        'feature_key',
        'kind',
        'display_name',
        'status',
        'sort_order',
        'plans',
        'purchase_uri',
    }
    # 档位按 sort_order 升序（前端直接顺序渲染）
    assert [p['plan_key'] for p in item['plans']] == ['monthly', 'lifetime']
    plan = item['plans'][0]
    assert set(plan) >= {
        'plan_key',
        'price_amount',
        'price_unit',
        'cycle',
        'quota_json',
        'trial_json',
        'display_json',
        'sort_order',
    }
    assert plan['price_unit'] == 'cny'
    assert plan['trial_json'] == {'enabled': False}
    assert plan['display_json']['display_name'] == '按月'


async def test_quote_purchase_uri_matches_paywall_offer(env: Any) -> None:
    """报价面的 `purchase_uri` 与付费墙拒绝时给的 `AccessDecision.offer.purchase_uri` **必须同源**。

    两处漂移就会出现「同一个商品两条深链」，客户端按哪条都可能打不开。
    """
    db = env.db
    _user_id, owner = await _seed_owner(db)
    await _seed_probe_offering(db)

    items = _data(await env.client.get(_QUOTES, params={'kind': 'feature_plan', 'offering_key': PROBE_OFFERING}))
    decision = await resolve_access(db, feature_key=PROBE_FEATURE, subject_type='owner', subject_id=owner)
    assert decision.allowed is False, '尚未购买时应被拒并给出购买引导'
    assert decision.offer is not None
    assert decision.offer.purchase_uri == items[0]['purchase_uri']


# ══════════════════ 存量五个 order_type 回归不变 ══════════════════


def test_legacy_order_types_untouched() -> None:
    """存量五个 `order_type` 的 kind 映射逐条不变（通用路是**新增**，不是改写）。"""
    assert fulfillment.ORDER_TYPE_TO_KIND == {
        'subscribe': fulfillment.KIND_LLM_TIER,
        'credit_pack': fulfillment.KIND_CREDIT_PACK,
        'app_purchase': fulfillment.KIND_APP,
        'app_seat': fulfillment.KIND_SEAT,
        'lead_pack': fulfillment.KIND_LEAD_PACK,
        'feature_plan': fulfillment.KIND_FEATURE_PLAN,
    }
    # 通用路刻意不进静态映射表：它的 kind 只有目录知道
    assert fulfillment.ORDER_TYPE_OFFERING not in fulfillment.ORDER_TYPE_TO_KIND
    assert fulfillment.build_offering_ref('subscribe', offering_key='llm:tier', plan_key='pro') == {
        'offering_key': 'llm:tier',
        'plan_key': 'pro',
        'kind': 'llm_tier',
    }
    assert fulfillment.build_offering_ref('bogus') is None


def test_legacy_order_param_validation_untouched() -> None:
    """存量五个 `order_type` 的跨字段校验逐条不变。"""
    assert CreatePayOrderParam(order_type='subscribe', tier='pro', channel_code='wx_native').tier == 'pro'
    assert CreatePayOrderParam(order_type='credit_pack', package_id=10, channel_code='alipay_qr').package_id == 10
    assert CreatePayOrderParam(order_type='app_purchase', app_id='quant', channel_code='wx_native').app_id == 'quant'
    assert CreatePayOrderParam(order_type='lead_pack', lead_count=5, channel_code='wx_native').lead_count == 5
    assert (
        CreatePayOrderParam(
            order_type='app_seat', app_id='quant', enterprise_id=1, seats=3, channel_code='wx_native'
        ).seats
        == 3
    )
    for bad in (
        {'order_type': 'subscribe'},
        {'order_type': 'credit_pack'},
        {'order_type': 'app_purchase'},
        {'order_type': 'lead_pack'},
        {'order_type': 'app_seat', 'app_id': 'quant'},
        {'order_type': 'bogus'},
    ):
        with pytest.raises(ValueError):
            CreatePayOrderParam(channel_code='wx_native', **bad)


def test_build_offering_ref_requires_explicit_kind_for_generic_path() -> None:
    """通用路漏传 `kind` 立即炸——绝不落一条无 `offering_ref` 的订单等付款后才 fail-closed。"""
    ref = fulfillment.build_offering_ref(
        'offering', offering_key=PROBE_OFFERING, plan_key='monthly', kind='feature_plan'
    )
    assert ref == {'offering_key': PROBE_OFFERING, 'plan_key': 'monthly', 'kind': 'feature_plan'}
    # 同一条通用路能承载别的 kind（不被钉死在 feature_plan）
    seat_ref = fulfillment.build_offering_ref('offering', offering_key='seat:quant', plan_key='standard', kind='seat')
    assert seat_ref is not None and seat_ref['kind'] == 'seat'
    with pytest.raises(ValueError, match='显式传入 kind'):
        fulfillment.build_offering_ref('offering', offering_key=PROBE_OFFERING, plan_key='monthly')


async def test_generic_path_carries_seat_kind_end_to_end(env: Any) -> None:
    """通用下单路对**非 feature_plan** 商品同样成立：`kind=seat` 的商品照样落出 `kind='seat'` 的快照。

    这条钉死「通用路不是 feature_plan 的马甲」——真钉死成 feature_plan 的实现会在这里红。
    """
    db = env.db
    user_id, _owner = await _seed_owner(db)
    channel_code = await _seed_channel(db)
    seat_key = f'seat:g95probe_{_uid()}'
    db.add(
        BillingOffering(
            key=seat_key,
            kind=fulfillment.KIND_SEAT,
            feature_key=f'seat:g95probe_{_uid()}',
            display_name='探针席位商品',
            status='active',
            source='platform',
            sort_order=992,
        )
    )
    db.add(
        BillingPlan(
            offering_key=seat_key,
            plan_key='standard',
            price_amount=Decimal('30.00'),
            price_unit='cny',
            cycle='month',
            quota_json={},
            trial_json={},
            grace_json={},
            display_json={},
            status='active',
            sort_order=1,
        )
    )
    await db.flush()

    obj = _order_param(channel_code=channel_code, offering_key=seat_key, plan_key='standard', quantity=2)
    order, _channel, _merchant = await PayOrderService.prepare_offering_order(
        db=db, user_id=user_id, obj=obj, app_code='huanxing'
    )
    assert (order.offering_ref or {})['kind'] == 'seat', '通用路的 kind 必须来自目录，而不是钉死成 feature_plan'
    assert order.pay_amount == 3000 * 2
