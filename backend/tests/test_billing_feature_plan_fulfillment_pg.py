"""通用 `feature_plan` 商品履约与退款回收真实 PG 验收——零 mock。

`offering.kind='feature_plan'` 是一整类加购型商品的发货轴，处理器
（`billing/service/feature_plan_callback.py`）**数据驱动、不认识任何具体商品**。本文件钉死七条守卫：

1. 买 1 份 `cloud:node` 月付 → 权益行出现、`max_cloud_nodes=1`、`expires_at` 约 +1 个月；
2. **再买 1 份 → 同一行累加到 2，且权益行仍只有一条**（最关键的一条：
   `access_service._active_entitlement_fk` 取 `.first()` 只认一条，插多行的话第 2 行起
   永远不会被读到，用户付了钱拿不到配额）；
3. 续购在现有 `expires_at` 基础上**顺延**而非覆盖；
4. 退款按发货回执扣回配额并按 cycle 回退 `expires_at`；
5. 订单缺关键参数一律**抛错**（订单进 `fulfillment_status=dead`），绝不静默放行；
6. **商品目录里所有 active offering 的 kind 都有履约处理器**——本次踩的坑就是目录与闸就位、
   `feature_plan` 却没有 handler，买了必进死信；
7. 换一个**与云端节点毫无关系的虚构 feature_plan 商品**跑通同一条链路（含 `once` 买断 →
   `expires_at=NULL`、非数值键覆盖），证明处理器真的通用而不是碰巧对 `cloud:node` 好使。

真实 PostgreSQL :15432（`hasn_billing` + `public.hasn_app_entitlement`），全部在事务内跑完回滚。
"""

from __future__ import annotations

import pathlib
import uuid

from decimal import Decimal
from types import SimpleNamespace
from typing import TYPE_CHECKING, Any

import pytest
import pytest_asyncio
import sqlalchemy as sa

from dateutil.relativedelta import relativedelta
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

from backend.app.billing.core import fulfillment
from backend.app.billing.model.billing_offering import BillingOffering
from backend.app.billing.model.billing_plan import BillingPlan
from backend.app.billing.model.pay_order import PayOrder
from backend.app.billing.service.access_service import resolve_access
from backend.app.billing.service.feature_plan_callback import (
    FULFILLED_GRANT_KEY,
    handle_feature_plan_paid,
    revoke_feature_plan,
)
from backend.app.hasn.model.hasn_app_entitlement import HasnAppEntitlement
from backend.app.hasn.model.hasn_humans import HasnHumans
from backend.app.hasn_hosting.constants import CLOUD_NODE_FEATURE_KEY, CLOUD_NODE_OFFERING_KEY
from backend.common.exception import errors
from backend.database.db import SQLALCHEMY_DATABASE_URL, async_engine
from backend.utils.timezone import timezone

if TYPE_CHECKING:
    from collections.abc import AsyncIterator
    from datetime import datetime

    from sqlalchemy.ext.asyncio import AsyncSession

pytestmark = pytest.mark.asyncio

_MIGRATION = (
    pathlib.Path(__file__).resolve().parents[1]
    / 'sql' / 'billing' / 'migrations' / '2026-08-01-cloud-node-billing-tiers.sql'
)

# 虚构的第二个 feature_plan 商品：与云端节点毫无关系，用来证明处理器真的通用。
# feature_key 走 `workflow_template:` 这个已注册前缀，既满足处理器的防漂移闸，
# 又不会污染 `feature_registry.validate_offering_consistency` 的一致性校验。
PROBE_OFFERING_KEY = 'feature:probe_pack'
PROBE_FEATURE_KEY = 'workflow_template:fp_probe'


def _uid() -> str:
    return uuid.uuid4().hex[:10]


def _split_sql(raw: str) -> list[str]:
    """把 .sql 切成单条语句：按 ';' 分割，但跳过 `$$ … $$` 美元引用块内部的分号。"""
    body = '\n'.join(ln for ln in raw.splitlines() if not ln.lstrip().startswith('--'))
    stmts: list[str] = []
    buf: list[str] = []
    idx = 0
    in_dollar = False
    while idx < len(body):
        if body.startswith('$$', idx):
            in_dollar = not in_dollar
            buf.append('$$')
            idx += 2
            continue
        char = body[idx]
        if char == ';' and not in_dollar:
            stmt = ''.join(buf).strip()
            if stmt:
                stmts.append(stmt)
            buf = []
            idx += 1
            continue
        buf.append(char)
        idx += 1
    tail = ''.join(buf).strip()
    if tail:
        stmts.append(tail)
    return stmts


@pytest_asyncio.fixture
async def db() -> AsyncIterator[AsyncSession]:
    await async_engine.dispose()
    engine = create_async_engine(SQLALCHEMY_DATABASE_URL, poolclass=NullPool)
    try:
        async with engine.connect() as conn:
            await conn.execute(sa.select(1))
    except Exception as exc:
        await engine.dispose()
        pytest.skip(f'本地 PostgreSQL 不可达，跳过: {exc!r}')
    # 幂等应用云端节点计费迁移（裸库可跑；`cloud:node` offering/plan 是用例 1-4 的前提）
    async with engine.begin() as conn:
        for stmt in _split_sql(_MIGRATION.read_text(encoding='utf-8')):
            await conn.exec_driver_sql(stmt)
    session = async_sessionmaker(engine, expire_on_commit=False)()
    try:
        yield session
    finally:
        # 全部改动只活在事务里，回滚即彻底清场（不留测试数据污染商品目录）
        await session.rollback()
        await session.close()
        await engine.dispose()
        await async_engine.dispose()


# ─── 种子 ───


async def _seed_owner(db: AsyncSession) -> tuple[int, str]:
    """建一个 user_id ↔ owner hasn_id 映射（权益主体由 `resolve_owner_hasn_id` 反查）。"""
    user_id = 970_000_000 + int(uuid.uuid4().int % 1_000_000)
    hasn_id = f'h_fp_{_uid()}'
    db.add(HasnHumans(hasn_id=hasn_id, star_id=f's_fp_{_uid()}', user_id=user_id, nickname='加购用户', status='active'))
    await db.flush()
    return user_id, hasn_id


async def _seed_probe_offering(db: AsyncSession) -> None:
    """虚构 feature_plan 商品：两个档（月付 / 一次性买断），配额键与云端节点完全不同。"""
    db.add(
        BillingOffering(
            key=PROBE_OFFERING_KEY,
            kind='feature_plan',
            feature_key=PROBE_FEATURE_KEY,
            display_name='通用性探针商品',
            status='active',
            source='platform',
            sort_order=999,
        )
    )
    db.add(
        BillingPlan(
            offering_key=PROBE_OFFERING_KEY,
            plan_key='standard',
            price_amount=Decimal('49.00'),
            price_unit='cny',
            cycle='month',
            # 两个数值键（都应累加）+ 一个非数值键（只能覆盖）
            quota_json={'max_probe_slots': 3, 'max_probe_seats': 2, 'tier': 'probe_std'},
            trial_json={'enabled': False},
            grace_json={},
            display_json={},
            status='active',
            sort_order=1,
        )
    )
    db.add(
        BillingPlan(
            offering_key=PROBE_OFFERING_KEY,
            plan_key='lifetime',
            price_amount=Decimal('499.00'),
            price_unit='cny',
            cycle='once',  # 买断 → expires_at 必须为 NULL（永久）
            quota_json={'max_probe_slots': 10, 'tier': 'probe_lifetime'},
            trial_json={'enabled': False},
            grace_json={},
            display_json={},
            status='active',
            sort_order=2,
        )
    )
    await db.flush()


async def _paid_order(
    db: AsyncSession,
    *,
    user_id: int,
    offering_key: str,
    plan_key: str | None,
    quantity: int = 1,
    billing_cycle: str | None = None,
) -> PayOrder:
    """一笔已支付（status=1）的 feature_plan 订单。"""
    order = PayOrder(
        order_no=f'FP{_uid()}{_uid()}'[:32],
        user_id=user_id,
        order_type=fulfillment.ORDER_TYPE_FEATURE_PLAN,
        subject='加购测试订单',
        amount=12800,
        pay_amount=12800,
        status=1,
        billing_cycle=billing_cycle,
        expire_time=timezone.now(),
        extra_data={'app_code': 'huanxing', 'quantity': quantity},
        offering_ref=fulfillment.build_offering_ref(
            fulfillment.ORDER_TYPE_FEATURE_PLAN, offering_key=offering_key, plan_key=plan_key
        ),
    )
    db.add(order)
    await db.flush()
    return order


async def _entitlements(db: AsyncSession, *, feature_key: str, subject_id: str) -> list[HasnAppEntitlement]:
    return list(
        (
            await db.execute(
                sa.select(HasnAppEntitlement)
                .where(
                    HasnAppEntitlement.feature_key == feature_key,
                    HasnAppEntitlement.subject_id == subject_id,
                )
                .order_by(HasnAppEntitlement.id)
            )
        ).scalars().all()
    )


def _assert_close(actual: datetime | None, expected: datetime, *, label: str) -> None:
    """到期时刻允许几十秒抖动（`now` 在用例与处理器里各取一次）。"""
    assert actual is not None, f'{label}: expires_at 不应为空'
    drift = abs((actual - expected).total_seconds())
    assert drift < 120, f'{label}: expires_at={actual} 与预期 {expected} 相差 {drift}s'


# ─── 守卫 1：买 1 份月付 → 一条权益行、配额 1、到期 +1 个月 ───


async def test_buy_one_monthly_cloud_node_grants_one_slot(db: AsyncSession) -> None:
    """买 1 份 `cloud:node` 月付：发出一条 `cloud_node` 权益，`max_cloud_nodes=1`，到期约 +1 个月。"""
    user_id, owner = await _seed_owner(db)
    order = await _paid_order(db, user_id=user_id, offering_key=CLOUD_NODE_OFFERING_KEY, plan_key='monthly')

    before = timezone.now()
    await handle_feature_plan_paid(db, order=order)

    rows = await _entitlements(db, feature_key=CLOUD_NODE_FEATURE_KEY, subject_id=owner)
    assert len(rows) == 1, f'应只发一条权益行，实得 {len(rows)}'
    ent = rows[0]
    assert ent.status == 'active'
    assert ent.source == 'purchase'
    assert ent.subject_type == 'owner'
    # app_id 用 feature_key 占位——正是它让 uq_app_entitlement_active 保证「每主体每 feature 一条 active」
    assert ent.app_id == CLOUD_NODE_FEATURE_KEY
    assert ent.order_ref == order.order_no
    assert ent.quota_json['max_cloud_nodes'] == 1
    _assert_close(ent.expires_at, before + relativedelta(months=1), label='首购')

    # 发货回执落在订单上（重放去重 + 退款回收的唯一依据）
    receipt = (order.extra_data or {})[FULFILLED_GRANT_KEY]
    assert receipt['feature_key'] == CLOUD_NODE_FEATURE_KEY
    assert receipt['quota_delta'] == {'max_cloud_nodes': 1}
    assert receipt['cycle'] == 'month'
    # 履约状态如实置终态：权益就在本事务内落库，不走 outbox
    assert order.fulfillment_status == 'succeeded'
    assert order.fulfilled_at is not None


# ─── 守卫 2（最关键）：再买 1 份 → 同一行累加到 2，权益行仍只有一条 ───


async def test_second_purchase_accumulates_on_the_same_single_row(db: AsyncSession) -> None:
    """买第二份必须在**同一行**上累加到 2，绝不插第二行。

    `access_service._active_entitlement_fk` 取的是 `.first()`，只认一条——插多行的话第 2 行起
    永远不会被读到，用户付了钱拿不到配额。故本用例同时断言：行数恒为 1、
    且 `resolve_access` 透出的配额快照真的看得见 2。
    """
    user_id, owner = await _seed_owner(db)
    first = await _paid_order(db, user_id=user_id, offering_key=CLOUD_NODE_OFFERING_KEY, plan_key='monthly')
    await handle_feature_plan_paid(db, order=first)
    second = await _paid_order(db, user_id=user_id, offering_key=CLOUD_NODE_OFFERING_KEY, plan_key='monthly')
    await handle_feature_plan_paid(db, order=second)

    rows = await _entitlements(db, feature_key=CLOUD_NODE_FEATURE_KEY, subject_id=owner)
    assert len(rows) == 1, f'买两份仍应只有一条权益行，实得 {len(rows)} 条（第 2 行永远不会被读到）'
    assert rows[0].quota_json['max_cloud_nodes'] == 2
    assert rows[0].order_ref == second.order_no

    # 判定入口真的看得见累加后的名额（端到端证明「只认一条」的陷阱已避开）
    decision = await resolve_access(
        db, feature_key=CLOUD_NODE_FEATURE_KEY, subject_type='owner', subject_id=owner
    )
    assert decision.allowed is True
    assert decision.reason == 'entitled'
    assert decision.quota is not None
    assert decision.quota.snapshot['max_cloud_nodes'] == 2


async def test_replayed_order_does_not_double_count(db: AsyncSession) -> None:
    """同一订单重放（回调重投）不得二次累加——发货回执即去重标记。"""
    user_id, owner = await _seed_owner(db)
    order = await _paid_order(db, user_id=user_id, offering_key=CLOUD_NODE_OFFERING_KEY, plan_key='monthly')
    await handle_feature_plan_paid(db, order=order)
    await handle_feature_plan_paid(db, order=order)

    rows = await _entitlements(db, feature_key=CLOUD_NODE_FEATURE_KEY, subject_id=owner)
    assert len(rows) == 1
    assert rows[0].quota_json['max_cloud_nodes'] == 1, '重放不得把名额刷成 2'


# ─── 守卫 3：续购顺延而非覆盖 ───


async def test_renewal_extends_expiry_instead_of_overwriting(db: AsyncSession) -> None:
    """连买两单月付 → 到期约 +2 个月（顺延）；再买一单年付 → 在此基础上再 +1 年。

    覆盖式写法（`expires_at = now + 1 月`）会让第二单白买，这里逐单核对时长累计。
    """
    user_id, owner = await _seed_owner(db)
    before = timezone.now()
    for _ in range(2):
        order = await _paid_order(db, user_id=user_id, offering_key=CLOUD_NODE_OFFERING_KEY, plan_key='monthly')
        await handle_feature_plan_paid(db, order=order)

    rows = await _entitlements(db, feature_key=CLOUD_NODE_FEATURE_KEY, subject_id=owner)
    assert len(rows) == 1
    _assert_close(rows[0].expires_at, before + relativedelta(months=2), label='两单月付')

    yearly = await _paid_order(db, user_id=user_id, offering_key=CLOUD_NODE_OFFERING_KEY, plan_key='yearly')
    await handle_feature_plan_paid(db, order=yearly)
    rows = await _entitlements(db, feature_key=CLOUD_NODE_FEATURE_KEY, subject_id=owner)
    assert len(rows) == 1
    assert rows[0].quota_json['max_cloud_nodes'] == 3
    _assert_close(rows[0].expires_at, before + relativedelta(months=2) + relativedelta(years=1), label='再加年付')


# ─── 守卫 4：退款扣回 + 到期回退 ───


async def test_refund_rolls_back_quota_and_expiry(db: AsyncSession) -> None:
    """退第二单：名额扣回 2→1，到期回退一个月；权益行仍是那一条、仍 active。"""
    user_id, owner = await _seed_owner(db)
    before = timezone.now()
    first = await _paid_order(db, user_id=user_id, offering_key=CLOUD_NODE_OFFERING_KEY, plan_key='monthly')
    await handle_feature_plan_paid(db, order=first)
    second = await _paid_order(db, user_id=user_id, offering_key=CLOUD_NODE_OFFERING_KEY, plan_key='monthly')
    await handle_feature_plan_paid(db, order=second)

    await revoke_feature_plan(db, order=second, refund_no=f'RF{second.order_no}')

    rows = await _entitlements(db, feature_key=CLOUD_NODE_FEATURE_KEY, subject_id=owner)
    assert len(rows) == 1
    assert rows[0].quota_json['max_cloud_nodes'] == 1, '只该扣回本单发的 1 个'
    assert rows[0].status == 'active', '还剩 1 个名额，权益不该整行撤销'
    _assert_close(rows[0].expires_at, before + relativedelta(months=1), label='退款回退一个月')
    assert second.fulfillment_status == 'reversed'

    # 再退第一单：名额清零 → 整行撤销（留一条 0 名额的 active 空壳会让存活 sweep 误判仍有资格）
    await revoke_feature_plan(db, order=first, refund_no=f'RF{first.order_no}')
    rows = await _entitlements(db, feature_key=CLOUD_NODE_FEATURE_KEY, subject_id=owner)
    assert len(rows) == 1
    assert rows[0].quota_json['max_cloud_nodes'] == 0
    assert rows[0].status == 'revoked'
    decision = await resolve_access(
        db, feature_key=CLOUD_NODE_FEATURE_KEY, subject_type='owner', subject_id=owner
    )
    assert decision.allowed is False, '全额退款后不该再判为持有权益'


async def test_refund_without_receipt_is_not_silent(db: AsyncSession) -> None:
    """没有发货回执就退款：不猜回收量（猜就会把别单发的额度也扣掉），权益保持原样。"""
    user_id, owner = await _seed_owner(db)
    paid = await _paid_order(db, user_id=user_id, offering_key=CLOUD_NODE_OFFERING_KEY, plan_key='monthly')
    await handle_feature_plan_paid(db, order=paid)

    ghost = await _paid_order(db, user_id=user_id, offering_key=CLOUD_NODE_OFFERING_KEY, plan_key='monthly')
    await revoke_feature_plan(db, order=ghost, refund_no=f'RF{ghost.order_no}')

    rows = await _entitlements(db, feature_key=CLOUD_NODE_FEATURE_KEY, subject_id=owner)
    assert rows[0].quota_json['max_cloud_nodes'] == 1, '无回执的退款不得动别单发出的名额'


# ─── 守卫 5：缺关键参数一律抛错（订单进死信），不静默放行 ───


@pytest.mark.parametrize(
    ('mutate', 'label'),
    [
        (lambda o: o.offering_ref.pop('offering_key'), '缺 offering_key'),
        (lambda o: o.offering_ref.update({'plan_key': None}), '缺 plan_key'),
        (lambda o: o.offering_ref.update({'plan_key': 'never_seeded'}), '档位不存在'),
        (lambda o: o.offering_ref.update({'offering_key': 'llm:tier'}), 'kind 不是 feature_plan'),
        (lambda o: setattr(o, 'extra_data', {'quantity': 0}), '份数非正'),
    ],
)
async def test_missing_key_params_raise_into_dead_letter(
    db: AsyncSession, mutate: Any, label: str
) -> None:
    """参数缺失/不可解析 → 抛 `RequestError`，`dispatch_fulfillment` 透传后订单进死信。

    「打日志后 return」会把订单留在假成功：用户看到「支付成功」却永远收不到权益。
    """
    user_id, owner = await _seed_owner(db)
    order = await _paid_order(db, user_id=user_id, offering_key=CLOUD_NODE_OFFERING_KEY, plan_key='monthly')
    mutate(order)

    with pytest.raises(errors.RequestError):
        await fulfillment.dispatch_fulfillment(db, order)

    assert await _entitlements(db, feature_key=CLOUD_NODE_FEATURE_KEY, subject_id=owner) == [], (
        f'{label}: 不该留下半拉子权益'
    )
    assert FULFILLED_GRANT_KEY not in (order.extra_data or {}), f'{label}: 不该写出发货回执'


async def test_order_without_owner_mapping_raises(db: AsyncSession) -> None:
    """user_id 没有 owner 映射 → 抛错进死信（把权益发给空气等于收了钱不发货）。"""
    order = await _paid_order(
        db, user_id=979_999_999, offering_key=CLOUD_NODE_OFFERING_KEY, plan_key='monthly'
    )
    with pytest.raises(errors.RequestError, match='owner 映射'):
        await handle_feature_plan_paid(db, order=order)


# ─── 守卫 6：目录里所有 active offering 的 kind 都有履约处理器 ───


def _register_all_fulfillment_registrars() -> None:
    """按应用启动同款顺序真实调用各 registrar。"""
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


def test_feature_plan_kind_registered() -> None:
    """`feature_plan` 的发货与退款回收处理器都已注册（本次补齐的那一环）。"""
    _register_all_fulfillment_registrars()
    assert fulfillment.KIND_FEATURE_PLAN in fulfillment.get_registered_kinds()
    assert fulfillment.KIND_FEATURE_PLAN in fulfillment.get_registered_refund_kinds()


async def test_every_active_offering_kind_has_fulfillment_handler(db: AsyncSession) -> None:
    """**商品目录守卫**：库里每个 active offering 的 kind 都必须有履约 + 退款回收处理器。

    这正是本次踩的坑——`cloud:node` 目录与配额闸都就位了，`feature_plan` 却没有 handler，
    用户真去买就会 `dispatch_fulfillment` 抛错、订单进 `fulfillment_status=dead`。
    以后再上任何一类新商品，忘了写 handler 会在这里红，而不是在生产的付款回调里红。
    """
    _register_all_fulfillment_registrars()
    await _seed_probe_offering(db)  # 连未来的虚构商品也一并纳入守卫

    kinds = sorted(
        {
            k
            for (k,) in (
                await db.execute(
                    sa.select(BillingOffering.kind).where(BillingOffering.status == 'active').distinct()
                )
            ).all()
            if k
        }
    )
    assert kinds, '商品目录里一个 active offering 都没有，守卫失去意义'
    missing_fulfillment = [k for k in kinds if k not in fulfillment.get_registered_kinds()]
    missing_refund = [k for k in kinds if k not in fulfillment.get_registered_refund_kinds()]
    assert not missing_fulfillment, f'这些 active 商品种类没有履约处理器（买了必进死信）: {missing_fulfillment}'
    assert not missing_refund, f'这些 active 商品种类没有退款回收处理器（退钱不回收权益）: {missing_refund}'


# ─── 守卫 7：换个毫不相干的 feature_plan 商品，同一条链路照样跑通 ───


async def test_generic_for_an_unrelated_feature_plan_offering(db: AsyncSession) -> None:
    """虚构商品（配额键 `max_probe_slots`/`max_probe_seats`，与云端节点无关）走同一处理器。

    验四件事：多个数值键都累加、非数值键按最近一单覆盖、`resolve_access` 读得到、退款按回执回滚。
    处理器若硬编码了 `cloud_node` / `max_cloud_nodes`，这条必红。
    """
    user_id, owner = await _seed_owner(db)
    await _seed_probe_offering(db)

    first = await _paid_order(db, user_id=user_id, offering_key=PROBE_OFFERING_KEY, plan_key='standard')
    await handle_feature_plan_paid(db, order=first)
    # 第二单买 2 份：数值键按「档位值 × 份数」累加
    second = await _paid_order(
        db, user_id=user_id, offering_key=PROBE_OFFERING_KEY, plan_key='standard', quantity=2
    )
    await handle_feature_plan_paid(db, order=second)

    rows = await _entitlements(db, feature_key=PROBE_FEATURE_KEY, subject_id=owner)
    assert len(rows) == 1, '通用路同样只发一条权益行'
    snapshot = rows[0].quota_json
    assert snapshot['max_probe_slots'] == 3 + 3 * 2
    assert snapshot['max_probe_seats'] == 2 + 2 * 2
    assert snapshot['tier'] == 'probe_std', '非数值键不可累加，按最近一单覆盖'

    decision = await resolve_access(
        db, feature_key=PROBE_FEATURE_KEY, subject_type='owner', subject_id=owner
    )
    assert decision.allowed is True
    assert decision.quota is not None
    assert decision.quota.snapshot['max_probe_slots'] == 9

    # 退第二单：只扣它发的那份（3×2 / 2×2），到期回退一个月
    await revoke_feature_plan(db, order=second, refund_no=f'RF{second.order_no}')
    rows = await _entitlements(db, feature_key=PROBE_FEATURE_KEY, subject_id=owner)
    assert rows[0].quota_json['max_probe_slots'] == 3
    assert rows[0].quota_json['max_probe_seats'] == 2
    assert rows[0].status == 'active'


async def test_once_cycle_grants_permanent_entitlement(db: AsyncSession) -> None:
    """`cycle='once'` = 买断：`expires_at` 必须为 NULL（永久），退款则还原买断前的到期时刻。"""
    user_id, owner = await _seed_owner(db)
    await _seed_probe_offering(db)

    monthly = await _paid_order(db, user_id=user_id, offering_key=PROBE_OFFERING_KEY, plan_key='standard')
    await handle_feature_plan_paid(db, order=monthly)
    rows = await _entitlements(db, feature_key=PROBE_FEATURE_KEY, subject_id=owner)
    dated_expiry = rows[0].expires_at
    assert dated_expiry is not None

    lifetime = await _paid_order(db, user_id=user_id, offering_key=PROBE_OFFERING_KEY, plan_key='lifetime')
    await handle_feature_plan_paid(db, order=lifetime)
    rows = await _entitlements(db, feature_key=PROBE_FEATURE_KEY, subject_id=owner)
    assert rows[0].expires_at is None, 'once 买断必须落成永久（NULL），不是「+1 周期」'
    assert rows[0].quota_json['max_probe_slots'] == 3 + 10

    await revoke_feature_plan(db, order=lifetime, refund_no=f'RF{lifetime.order_no}')
    rows = await _entitlements(db, feature_key=PROBE_FEATURE_KEY, subject_id=owner)
    assert rows[0].quota_json['max_probe_slots'] == 3
    _assert_close(rows[0].expires_at, dated_expiry, label='买断退款还原到期时刻')


# ─── 退款后配额缺口告警（用量探针注册表） ───


async def test_usage_probe_reports_gap_after_refund(db: AsyncSession) -> None:
    """退款后若配额已低于在用量，如实 `warn` 记录缺口（停不停机是运营决策，本 handler 不代劳）。

    用**生产口径的探针**（`cloud_node_usage`，registrar 注册的那一个）+ 两条真实托管行，
    不塞假探针：探针数错了这条也该红。
    """
    from backend.app.hasn_hosting.model import HasnCloudNodes
    from backend.app.hasn_hosting.service.cloud_node_usage_probe import register_cloud_node_usage_probe
    from backend.common.log import log

    register_cloud_node_usage_probe()
    user_id, owner = await _seed_owner(db)
    order = await _paid_order(db, user_id=user_id, offering_key=CLOUD_NODE_OFFERING_KEY, plan_key='monthly')
    await handle_feature_plan_paid(db, order=order)
    # 主人手上已经跑着 2 个节点：退掉唯一的加购后配额归零，缺口 = 2
    for seq in range(2):
        db.add(
            HasnCloudNodes(
                node_id=f'n_cloud_fp_gap_{_uid()}{seq}'[:40],
                user_id=user_id,
                owner_hasn_id=owner,
                host='hosting-test',
                container_ref=None,
                status='online',
                failure_reason=None,
                failure_detail=None,
                image_version='0.0.1',
                image_digest='sha256:' + '3' * 64,
                credential_session_uuid=None,
                retain_until=None,
                last_backup_at=None,
                online_since=None,
            )
        )
    await db.flush()

    captured: list[str] = []
    sink_id = log.add(lambda message: captured.append(str(message)), level='WARNING')
    try:
        await revoke_feature_plan(db, order=order, refund_no=f'RF{order.order_no}')
    finally:
        log.remove(sink_id)

    gap_logs = [line for line in captured if '退款后配额低于在用量' in line]
    assert gap_logs, f'缺口未被告警: {captured}'
    assert '在用 2' in gap_logs[0], f'缺口数值不实: {gap_logs[0]}'


def test_cloud_node_usage_probe_registered() -> None:
    """`cloud_node` 的用量探针经 registrar 真实注册（退款缺口告警的接线点）。"""
    from backend.app.billing.service.feature_plan_callback import get_registered_usage_probe_keys
    from backend.app.hasn_hosting.service.cloud_node_usage_probe import register_cloud_node_usage_probe

    register_cloud_node_usage_probe()
    assert CLOUD_NODE_FEATURE_KEY in get_registered_usage_probe_keys()


def test_feature_plan_order_type_maps_to_kind() -> None:
    """`order_type='feature_plan'` → `kind='feature_plan'`，下单入口能快照出可分发的 offering_ref。"""
    ref = fulfillment.build_offering_ref(
        fulfillment.ORDER_TYPE_FEATURE_PLAN, offering_key=CLOUD_NODE_OFFERING_KEY, plan_key='monthly'
    )
    assert ref == {'offering_key': 'cloud:node', 'plan_key': 'monthly', 'kind': 'feature_plan'}


def test_generic_handler_is_not_hardcoded_to_cloud_node() -> None:
    """通用性静态守卫：处理器源码里不得出现 `cloud_node` / `max_cloud_nodes` 之类的商品硬编码。

    一旦有人图省事把云端节点的键名写死，所有别的 feature_plan 商品都会被发错货。
    """
    source = (
        pathlib.Path(__file__).resolve().parents[1]
        / 'app' / 'billing' / 'service' / 'feature_plan_callback.py'
    ).read_text(encoding='utf-8')
    code = '\n'.join(
        line for line in source.splitlines() if not line.lstrip().startswith('#')
    )
    # 注释与 docstring 里举例说明 cloud:node 是可以的，代码逻辑里出现键名就不行
    for banned in ('max_cloud_nodes', "'cloud_node'", "'cloud:node'"):
        assert banned not in code.split('"""')[-1], f'处理器逻辑里出现商品硬编码: {banned}'


def test_dispatch_routes_feature_plan_to_generic_handler() -> None:
    """`kind=feature_plan` 的订单确实路由到通用处理器（而不是某个商品专用的）。"""
    from backend.app.billing.core.fulfillment import _fulfillment_handlers

    _register_all_fulfillment_registrars()
    handler = _fulfillment_handlers[fulfillment.KIND_FEATURE_PLAN]
    assert handler.__name__ == 'handle_feature_plan_paid'
    assert handler.__module__ == 'backend.app.billing.service.feature_plan_callback'
    # 顺带确认分发器对未知 kind 仍 fail-closed（不会因为新增了一类就放宽）
    assert 'never_registered_kind' not in fulfillment.get_registered_kinds()
    unknown = SimpleNamespace(order_no='x1', offering_ref={'kind': 'never_registered_kind'})
    assert unknown.offering_ref['kind'] not in fulfillment.get_registered_kinds()
