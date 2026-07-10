"""统一商业化内核 MK-9 全栈自动化 E2E·七场景叙事走查（实施/92 MK-9）——零 mock 真 PG。

不同于 MK-1~MK-7 的「构件级」单测（各测一个 service 方法），本文件把付费墙**从头到尾真实跑通**：
每个场景是一条端到端叙事，串起真实 service（access_service / 发货回调 / 席位指派 / catalog kernel），
证明「这次把付费墙真实跑通」。七场景对齐 doc02 §3 判定矩阵与设计风险清单（§风险与坑）：

  ① 免费应用直通                         —— free / 未配商品的特征非门控放行
  ② 试用发放→trialing→到期→宽限→续费恢复  —— 生命周期时钟推进（风险#3 quota 快照方向连带验证）
  ③ 购买应用→回调发货→三闸门放行          —— apply_app_purchase 真实写权益 → G3 放行
  ④ tier 升级→llm:tier 判定翻转           —— owner 订阅档升级令 tier 门翻转（风险#4：tier 永不落 entitlement 行）
  ⑤ 企业席位购买→分配→成员工具面放行       —— settle 席位 → assign → 统一 resolve_access 放行
  ⑥ quota 快照固化（改价后老权益配额不变）  —— 权益行固化购买时快照，改 plan 配额不穿透（风险#3）
  ⑦ admin 改价→新单新价/老单不变          —— plan 是定价权威（新报价随之变），历史订单金额快照不动（风险#5）

隔离策略：全程仅 flush 不 commit，逐测用 uuid 独立 ID，teardown rollback 即净（不污染 dev 库）。
「时钟推进」以行内 expires_at 位移模拟——resolve_access 的到期/宽限判定直接按 expires_at 求值，
不依赖 sweeper 先跑（sweeper 的 active→expired 翻状态是 MK-5 机制，另见 test_billing_lifecycle_sweep_pg）。

需本地 PostgreSQL :15432。
"""

from __future__ import annotations

import uuid

from datetime import timedelta
from decimal import Decimal
from types import SimpleNamespace
from typing import TYPE_CHECKING

import pytest
import pytest_asyncio
import sqlalchemy as sa

from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

from backend.app.billing.model.billing_offering import BillingOffering
from backend.app.billing.model.billing_plan import BillingPlan
from backend.app.billing.model.pay_order import PayOrder
from backend.app.billing.model.user_subscription import UserSubscription
from backend.app.billing.service import access_service
from backend.app.hasn.model.hasn_app_catalog import HasnAppCatalog
from backend.app.hasn.model.hasn_app_entitlement import HasnAppEntitlement
from backend.app.hasn.model.hasn_enterprise import HasnEnterprise
from backend.app.hasn.model.hasn_enterprise_membership import HasnEnterpriseMembership
from backend.app.hasn.model.hasn_humans import HasnHumans
from backend.app.hasn.service import app_catalog_service
from backend.app.hasn.service.app_purchase_callback import apply_app_purchase
from backend.app.hasn.service.app_seat_purchase_callback import settle_app_seat_purchase
from backend.app.hasn.service.workbench_domain_service import workbench_domain_service
from backend.database.db import SQLALCHEMY_DATABASE_URL
from backend.utils.timezone import timezone

if TYPE_CHECKING:
    from collections.abc import AsyncIterator

    from sqlalchemy.ext.asyncio import AsyncSession


pytestmark = pytest.mark.asyncio


def _uid() -> str:
    return uuid.uuid4().hex[:8]


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


# ============================ 种子工厂（uuid 独立 ID，flush 即可，rollback 净） ============================


async def _seed_human(db: AsyncSession, *, user_id: int, nickname: str) -> str:
    hasn_id = f'h_mk9_{_uid()}{_uid()}'[:38]
    db.add(HasnHumans(hasn_id=hasn_id, star_id=f's{user_id}', user_id=user_id, nickname=nickname, status='active'))
    await db.flush()
    return hasn_id


async def _seed_app_catalog(
    db: AsyncSession,
    *,
    access_type: str,
    min_tier: str | None = None,
    trial_days: int = 0,
    scope: list[str] | None = None,
    purchasable_by: str = 'both',
) -> HasnAppCatalog:
    cat = HasnAppCatalog(
        app_id=f'mk9_{_uid()}',
        name=f'MK9 应用 {_uid()}',
        status='published',
        access_type=access_type,
        min_tier=min_tier,
        scope=scope if scope is not None else ['personal'],
        purchasable_by=purchasable_by,
        price_amount=Decimal('39.90') if access_type == 'purchase' else None,
        price_unit='cny',
        billing_cycle='month',
        trial_days=trial_days,
    )
    db.add(cat)
    await db.flush()
    return cat


async def _seed_generic_offering(
    db: AsyncSession,
    *,
    feature_key: str,
    price: str = '9.90',
    quota: dict | None = None,
    trial_days: int = 0,
    grace_days: int = 0,
) -> tuple[BillingOffering, BillingPlan]:
    """通用特征商品：offering + 默认 active plan（携 quota/trial/grace 快照）。"""
    off = BillingOffering(
        key=feature_key,
        kind='feature_plan',
        feature_key=feature_key,
        display_name=f'MK9 {feature_key}',
        status='active',
        source='platform',
        sort_order=0,
    )
    plan = BillingPlan(
        offering_key=feature_key,
        plan_key='standard',
        price_amount=Decimal(price),
        price_unit='cny',
        cycle='month',
        quota_json=quota or {},
        trial_json={'enabled': True, 'days': trial_days} if trial_days else {},
        grace_json={'grace_days': grace_days} if grace_days else {},
        status='active',
        sort_order=0,
    )
    db.add_all([off, plan])
    await db.flush()
    return off, plan


def _mk_subscription(*, user_id: int, tier: str, end_delta: timedelta | None) -> UserSubscription:
    """owner 订阅行（app_code=huanxing，owner_effective_tier 读的就是这一条）。"""
    return UserSubscription(
        app_code='huanxing',
        user_id=user_id,
        tier=tier,
        subscription_type='monthly',
        monthly_credits=Decimal(0),
        current_credits=Decimal(0),
        used_credits=Decimal(0),
        purchased_credits=Decimal(0),
        status='active',
        max_agents=1,
        subscription_end_date=(timezone.now() + end_delta) if end_delta is not None else None,
    )


# ============================ ① 免费应用直通 ============================


async def test_scenario_1_free_app_passthrough(db: AsyncSession) -> None:
    """免费应用（access_type=free）任何主人直通；未配商品的通用特征亦非门控放行。"""
    owner = await _seed_human(db, user_id=993_100_000 + int(_uid(), 16) % 100_000, nickname='免费用户')
    cat = await _seed_app_catalog(db, access_type='free')

    d = await access_service.resolve_access(db, feature_key=f'app:{cat.app_id}', subject_id=owner)
    assert d.allowed and d.reason == 'free', f'免费应用应直通: {d.reason}'

    # 未配 offering 的通用特征 → 非门控放行（free）
    d2 = await access_service.resolve_access(db, feature_key='test:mk9_unconfigured_feature', subject_id=owner)
    assert d2.allowed and d2.reason == 'free'


# ============================ ② 试用→trialing→到期→宽限→续费恢复 ============================


async def test_scenario_2_trial_lifecycle_to_grace_and_renewal(db: AsyncSession) -> None:
    """通用特征全生命周期：发放试用→trialing→时钟推进到期→宽限期内可用→超宽限拦→续费恢复 entitled。"""
    owner = await _seed_human(db, user_id=993_200_000 + int(_uid(), 16) % 100_000, nickname='试用用户')
    feature_key = f'test:mk9life_{_uid()}'
    await _seed_generic_offering(db, feature_key=feature_key, quota={'sites': 1}, trial_days=7, grace_days=3)

    # 发放前：有试用可开 → trial_available
    d0 = await access_service.resolve_access(db, feature_key=feature_key, subject_id=owner)
    assert not d0.allowed and d0.reason == 'trial_available' and d0.trial_available is True

    # 发放试用 → trialing（权益行固化 plan 配额快照）
    dt = await access_service.grant_trial(db, feature_key=feature_key, subject_id=owner)
    assert dt.allowed and dt.reason == 'trialing'
    ent = (
        (
            await db.execute(
                sa.select(HasnAppEntitlement).where(
                    HasnAppEntitlement.feature_key == feature_key,
                    HasnAppEntitlement.subject_id == owner,
                    HasnAppEntitlement.source == 'trial',
                )
            )
        )
        .scalars()
        .first()
    )
    assert ent is not None and ent.quota_json == {'sites': 1}

    # 时钟推进：试用刚过期、仍在 3 天宽限内 → expired_in_grace（allowed，可续费恢复）
    ent.expires_at = timezone.now() - timedelta(days=1)
    await db.flush()
    dg = await access_service.resolve_access(db, feature_key=feature_key, subject_id=owner)
    assert dg.allowed and dg.reason == 'expired_in_grace'
    assert dg.grace is not None and dg.grace.recoverable is True

    # 时钟继续推进：超出宽限期 → 拦截（试用已用过，不可再试 → need_purchase）
    ent.expires_at = timezone.now() - timedelta(days=5)
    await db.flush()
    dn = await access_service.resolve_access(db, feature_key=feature_key, subject_id=owner)
    assert not dn.allowed and dn.reason == 'need_purchase'

    # 续费恢复：老试用行让位（= sweeper 到期动作，见 MK-5 测试）+ 落新购买权益 → entitled
    ent.status = 'expired'
    await db.flush()
    db.add(
        HasnAppEntitlement(
            app_id=feature_key,
            feature_key=feature_key,
            subject_type='owner',
            subject_id=owner,
            source='purchase',
            status='active',
            quota_json={'sites': 1},
            granted_at=timezone.now(),
            expires_at=timezone.now() + timedelta(days=30),
        )
    )
    await db.flush()
    dr = await access_service.resolve_access(db, feature_key=feature_key, subject_id=owner)
    assert dr.allowed and dr.reason == 'entitled', f'续费后应恢复 entitled: {dr.reason}'


# ============================ ③ 购买应用→回调发货→三闸门放行 ============================


async def test_scenario_3_purchase_callback_fulfillment_gate_pass(db: AsyncSession) -> None:
    """付费应用：购买前锁定 need_purchase → 支付成功回调真实发货写权益 → 统一 resolve_access 放行 entitled。"""
    user_id = 993_300_000 + int(_uid(), 16) % 100_000
    owner = await _seed_human(db, user_id=user_id, nickname='购买用户')
    cat = await _seed_app_catalog(db, access_type='purchase')
    feature_key = f'app:{cat.app_id}'

    # 购买前：锁定
    d_before = await access_service.resolve_access(db, feature_key=feature_key, subject_id=owner)
    assert not d_before.allowed and d_before.reason == 'need_purchase' and d_before.requires == 'purchase'

    # 支付成功回调（真实发货核心）：写 owner 维度 active 权益
    order = SimpleNamespace(
        order_no=f'MK9BUY{_uid()}',
        user_id=user_id,
        billing_cycle='month',
        extra_data={'app_id': cat.app_id},
    )
    granted = await apply_app_purchase(db, order=order)
    assert granted is not None and granted.source == 'purchase' and granted.feature_key == feature_key

    # 购买后：G3 应用权益门放行（统一 resolve_access = 工具闸/工作台共用入口）
    d_after = await access_service.resolve_access(db, feature_key=feature_key, subject_id=owner)
    assert d_after.allowed and d_after.reason == 'entitled', f'发货后应放行: {d_after.reason}'


# ============================ ④ tier 升级→llm:tier 判定翻转 ============================


async def test_scenario_4_tier_upgrade_flips_gate(db: AsyncSession) -> None:
    """订阅制应用：owner free 档 → need_upgrade；升级 pro 档 → tier_ok。且 tier 永不落 entitlement 行（风险#4）。"""
    user_id = 993_400_000 + int(_uid(), 16) % 100_000
    owner = await _seed_human(db, user_id=user_id, nickname='升级用户')
    cat = await _seed_app_catalog(db, access_type='tier', min_tier='pro')
    feature_key = f'app:{cat.app_id}'

    # 升级前（free 档，无订阅行）：档位不足 → need_upgrade
    d_before = await access_service.resolve_access(db, feature_key=feature_key, subject_id=owner)
    assert not d_before.allowed and d_before.reason == 'need_upgrade' and d_before.requires == 'upgrade'

    async def _llm_tier_entitlement_count() -> int:
        return (
            await db.execute(
                sa
                .select(sa.func.count())
                .select_from(HasnAppEntitlement)
                .where(
                    HasnAppEntitlement.feature_key == 'llm:tier',
                    HasnAppEntitlement.subject_id == owner,
                )
            )
        ).scalar_one()

    assert await _llm_tier_entitlement_count() == 0, 'tier 判定前不应有 llm:tier 权益行'

    # 升级：写 owner 订阅档 pro（订阅制，不落 entitlement 行）
    db.add(_mk_subscription(user_id=user_id, tier='pro', end_delta=timedelta(days=30)))
    await db.flush()

    # 升级后：档位达标 → tier_ok
    d_after = await access_service.resolve_access(db, feature_key=feature_key, subject_id=owner)
    assert d_after.allowed and d_after.reason == 'tier_ok', f'升级后应放行: {d_after.reason}'

    # 风险#4 守卫：tier 升级绝不把 llm:tier 写进 entitlement 表
    assert await _llm_tier_entitlement_count() == 0, 'llm:tier 永不落 entitlement 行（doc16 哲学）'


# ============================ ⑤ 企业席位购买→分配→成员工具面放行 ============================


async def test_scenario_5_enterprise_seat_purchase_assign_gate(db: AsyncSession) -> None:
    """企业买席位（回调结算）→ 指派成员 → 统一 resolve_access（企业空间）成员工具面放行。"""
    boss_uid = 993_500_000 + int(_uid(), 16) % 100_000
    await _seed_human(db, user_id=boss_uid, nickname='企业老板')  # assign 需要 operator owner 映射
    ent_biz = HasnEnterprise(name=f'MK9企业 {_uid()}', slug=f'mk9e2e-{_uid()}', owner_user_id=boss_uid)
    db.add(ent_biz)
    await db.flush()
    enterprise_id = ent_biz.id

    cat = await _seed_app_catalog(db, access_type='purchase', scope=['personal', 'enterprise'])
    app_id = cat.app_id
    feature_key = f'app:{app_id}'

    member_uid = 993_550_000 + int(_uid(), 16) % 100_000
    member = await _seed_human(db, user_id=member_uid, nickname='企业成员')
    db.add(HasnEnterpriseMembership(enterprise_id=enterprise_id, user_id=member_uid, role='member', status='approved'))
    await db.flush()

    # 企业买 3 席（席位购买回调真实结算）
    seat_order = SimpleNamespace(
        order_no=f'MK9SEAT{_uid()}',
        billing_cycle='month',
        extra_data={'app_id': app_id, 'enterprise_id': enterprise_id, 'seats': 3},
    )
    seat_ent = await settle_app_seat_purchase(db, order=seat_order)
    assert seat_ent is not None and seat_ent.seats_total == 3

    # 分配前：成员在企业空间仍 need_seat_assignment（席位闸）
    acc_pre = await app_catalog_service.resolve_app_access(
        db,
        catalog=cat,
        owner_hasn_id=member,
        subject_type='enterprise',
        subject_id=str(enterprise_id),
        member_hasn_id=member,
    )
    assert not acc_pre['allowed'] and acc_pre['reason'] == 'need_seat_assignment'

    # 指派席位（真实指派路径，含 hasn_id 翻译 + FOR UPDATE）
    seat = await workbench_domain_service.assign_app_seat(
        db, enterprise_id=enterprise_id, app_id=app_id, member_hasn_id=member, operator_user_id=boss_uid
    )
    assert seat['status'] == 'assigned'

    # 分配后：企业维度放行
    acc_post = await app_catalog_service.resolve_app_access(
        db,
        catalog=cat,
        owner_hasn_id=member,
        subject_type='enterprise',
        subject_id=str(enterprise_id),
        member_hasn_id=member,
    )
    assert acc_post['allowed'] and acc_post['reason'] == 'entitled'

    # 统一入口（成员在企业空间调工具面）：resolve_access 叠加企业维度 → 放行
    d_unified = await access_service.resolve_access(
        db, feature_key=feature_key, subject_id=member, active_enterprise_id=enterprise_id
    )
    assert d_unified.allowed, f'成员工具面应放行（企业席位）: {d_unified.reason}'


# ============================ ⑥ quota 快照固化（改价/改配额后老权益不变） ============================


async def test_scenario_6_quota_snapshot_frozen_on_reprice(db: AsyncSession) -> None:
    """权益行固化购买时 plan 配额快照；事后改 plan 配额，老权益按行内快照判定，不被穿透（风险#3）。"""
    owner = await _seed_human(db, user_id=993_600_000 + int(_uid(), 16) % 100_000, nickname='配额用户')
    feature_key = f'test:mk9quota_{_uid()}'
    _off, plan = await _seed_generic_offering(db, feature_key=feature_key, quota={'sites': 1}, trial_days=7)

    # 发放试用 → 权益行从 plan 复制固化配额快照 {sites:1}（证明「快照 = 授予时拷贝」的机制本身）
    await access_service.grant_trial(db, feature_key=feature_key, subject_id=owner)
    d1 = await access_service.resolve_access(db, feature_key=feature_key, subject_id=owner, usage={'sites': 1})
    assert d1.allowed and d1.reason in ('trialing', 'entitled')
    assert d1.quota is not None and d1.quota.snapshot == {'sites': 1}

    # admin 事后把 plan 配额放大到 99
    plan.quota_json = {'sites': 99}
    await db.flush()

    # 老权益仍按行内固化快照 {sites:1} 判定（不回读 plan）——用量 5 超老配额 → quota_exceeded
    d2 = await access_service.resolve_access(db, feature_key=feature_key, subject_id=owner, usage={'sites': 5})
    assert d2.quota is not None and d2.quota.snapshot == {'sites': 1}, f'快照被穿透: {d2.quota}'
    assert not d2.allowed and d2.reason == 'quota_exceeded', '老权益配额应固化不变'


# ============================ ⑦ admin 改价→新单新价/老单不变 ============================


async def test_scenario_7_reprice_new_quote_changes_old_order_frozen(db: AsyncSession) -> None:
    """plan 是定价权威：改价后新报价随之变（新单新价）；此前已下订单金额快照不动（老单不变）（风险#5）。"""
    user_id = 993_700_000 + int(_uid(), 16) % 100_000
    owner = await _seed_human(db, user_id=user_id, nickname='改价用户')
    feature_key = f'test:mk9price_{_uid()}'
    _off, plan = await _seed_generic_offering(db, feature_key=feature_key, price='9.90')

    # 改价前报价 9.90
    d_before = await access_service.resolve_access(db, feature_key=feature_key, subject_id=owner)
    assert d_before.offer is not None and d_before.offer.price == pytest.approx(9.90)

    # 一张历史订单（下单即固化金额快照 990 分）
    old_order = PayOrder(
        order_no=f'MK9OLD{_uid()}',
        user_id=user_id,
        order_type='subscribe',
        subject='MK9 老单',
        amount=990,
        pay_amount=990,
        expire_time=timezone.now() + timedelta(hours=1),
        billing_cycle='month',
        offering_ref={'offering_key': feature_key, 'plan_key': 'standard', 'kind': 'feature_plan'},
        status=1,
    )
    db.add(old_order)
    await db.flush()

    # admin 改价到 19.90
    plan.price_amount = Decimal('19.90')
    await db.flush()

    # 新单新价：新报价随 plan 走
    d_after = await access_service.resolve_access(db, feature_key=feature_key, subject_id=owner)
    assert d_after.offer is not None and d_after.offer.price == pytest.approx(19.90), '改价后新报价应更新'

    # 老单不变：历史订单金额快照不被改价穿透
    await db.refresh(old_order)
    assert old_order.amount == 990 and old_order.pay_amount == 990, '历史订单金额快照应固化不变'
