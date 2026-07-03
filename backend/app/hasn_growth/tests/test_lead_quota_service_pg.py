"""线索领取额度 + 线索付费 真实 PG 验收（doc93 阶段四 4.2·零 mock·回滚）。

覆盖额度闸填实 2.1 接缝（`_check_quota`）+ 交付后扣减（`_consume_quota`·先免费后购买）+
支付到账发放（`grant_purchased_leads` / `handle_lead_pack_paid`·不走积分）+ 免费额度按月重置 +
`request_leads` 端到端 shortfall（超额引导支付）+ `lead_pack` 订单定价守卫。需 export DATABASE_PORT=15432。

真实外部（infra-gated·不在此测）：支付下单 SDK 真实联调（alipay_qr 出二维码）——doc93 §测试策略。
"""

from __future__ import annotations

import uuid

from types import SimpleNamespace

import pytest
import pytest_asyncio

from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

from backend.app.hasn_growth.model import LeadContact, LeadQuota
from backend.app.hasn_growth.service.lead_pool_query_service import lead_pool_query_service
from backend.core.conf import settings
from backend.database.db import SQLALCHEMY_DATABASE_URL

pytestmark = pytest.mark.asyncio


@pytest_asyncio.fixture
async def session():
    engine = create_async_engine(SQLALCHEMY_DATABASE_URL, poolclass=NullPool)
    try:
        async with engine.connect() as conn:
            await conn.execute(select(1))
    except Exception as exc:
        await engine.dispose()
        pytest.skip(f'本地 PostgreSQL 不可达，跳过: {exc!r}')
    sess = async_sessionmaker(engine, expire_on_commit=False)()
    try:
        yield sess
    finally:
        await sess.rollback()
        await sess.close()
        await engine.dispose()


def _uid() -> int:
    return 940000 + int(uuid.uuid4().int % 90000)


async def test_check_quota_within_free_then_consume(session, monkeypatch) -> None:
    """免费额度内：放行 = 请求量；交付后扣减免费额度（剩余下降）。"""
    monkeypatch.setattr(settings, 'GROWTH_FREE_LEADS_PER_MONTH', 3)
    uid = _uid()

    # 全新用户：放行 min(2, 3+0)=2
    assert await lead_pool_query_service._check_quota(session, user_id=uid, requested=2) == 2

    used = await lead_pool_query_service._consume_quota(session, user_id=uid, count=2)
    assert used == {'from_free': 2, 'from_purchased': 0}

    snap = await lead_pool_query_service._quota_snapshot(session, user_id=uid)
    assert snap['free_per_month'] == 3 and snap['free_used'] == 2 and snap['free_remaining'] == 1
    assert snap['purchased_balance'] == 0


async def test_check_quota_caps_and_shortfall(session, monkeypatch) -> None:
    """超免费额度且无购买余额：放行被额度闸压到 free（其余需支付购买）。"""
    monkeypatch.setattr(settings, 'GROWTH_FREE_LEADS_PER_MONTH', 2)
    uid = _uid()
    # 请求 5，仅免费 2 → 放行 2（shortfall=3 由 request_leads 暴露给前端引导购买）
    assert await lead_pool_query_service._check_quota(session, user_id=uid, requested=5) == 2

    # 免费耗尽后放行 0（余额 0）
    await lead_pool_query_service._consume_quota(session, user_id=uid, count=2)
    assert await lead_pool_query_service._check_quota(session, user_id=uid, requested=5) == 0


async def test_grant_purchased_then_consume_free_first(session, monkeypatch) -> None:
    """支付到账增加购买余额；扣减先免费后购买（doc93 §4.2 不走积分）。"""
    monkeypatch.setattr(settings, 'GROWTH_FREE_LEADS_PER_MONTH', 2)
    uid = _uid()

    bal = await lead_pool_query_service.grant_purchased_leads(session, user_id=uid, count=5)
    assert bal == 5

    # 放行 = min(10, free 2 + purchased 5) = 7
    assert await lead_pool_query_service._check_quota(session, user_id=uid, requested=10) == 7

    # 领 3 条：先扣免费 2，再扣购买 1
    used = await lead_pool_query_service._consume_quota(session, user_id=uid, count=3)
    assert used == {'from_free': 2, 'from_purchased': 1}

    snap = await lead_pool_query_service._quota_snapshot(session, user_id=uid)
    assert snap['free_remaining'] == 0 and snap['purchased_balance'] == 4

    row = (await session.execute(select(LeadQuota).where(LeadQuota.user_id == uid))).scalar_one()
    assert row.purchased_total == 5 and row.consumed_total == 3


async def test_period_reset_zeroes_free_used(session, monkeypatch) -> None:
    """跨月：免费额度归零（free_used 重置），购买余额不动（永不过期）。"""
    monkeypatch.setattr(settings, 'GROWTH_FREE_LEADS_PER_MONTH', 5)
    uid = _uid()
    # 预置一条上个时代的账本行（period 远早于当前月，free_used 已满）
    session.add(LeadQuota(user_id=uid, period_key='2000-01', free_used=5, purchased_balance=7))
    await session.flush()

    # 只读快照：period 不匹配 → free_used 视为 0 → 免费剩余满额
    snap = await lead_pool_query_service._quota_snapshot(session, user_id=uid)
    assert snap['free_remaining'] == 5 and snap['purchased_balance'] == 7

    # 扣减触发归零 + period 推进到当前月；购买余额不受影响
    used = await lead_pool_query_service._consume_quota(session, user_id=uid, count=1)
    assert used == {'from_free': 1, 'from_purchased': 0}
    row = (await session.execute(select(LeadQuota).where(LeadQuota.user_id == uid))).scalar_one()
    assert row.period_key == lead_pool_query_service._current_period()
    assert row.free_used == 1 and row.purchased_balance == 7


async def test_handle_lead_pack_paid_grants_real(monkeypatch) -> None:
    """线索购买支付回调真实写库（独立 session·提交）→ 余额增加；用完即清理。"""
    from backend.app.hasn_growth.service.lead_pack_callback import handle_lead_pack_paid
    from backend.database.db import async_db_session, async_engine

    # 本测试用全局 async_db_session（验真实回调的提交路径），其连接池绑定到首次使用的事件循环；
    # pytest-asyncio 每个测试一个新循环，全套运行时池里残留上个测试循环的连接 → "got Future
    # attached to a different loop"。先 dispose 强制在当前循环重建连接，令本测试与运行顺序无关。
    await async_engine.dispose()

    uid = _uid()
    order = SimpleNamespace(
        user_id=uid, order_no=f'HXLEADTEST{uuid.uuid4().hex[:8]}',
        pay_amount=1000, extra_data={'app_code': 'huanxing', 'lead_count': 10},
    )
    try:
        await handle_lead_pack_paid(order)
        # 用独立 session 读回真实落库的余额
        async with async_db_session() as db:
            row = (await db.execute(select(LeadQuota).where(LeadQuota.user_id == uid))).scalar_one()
            assert row.purchased_balance == 10 and row.purchased_total == 10
        # 缺 lead_count 的订单不发放（防御）
        bad = SimpleNamespace(user_id=uid, order_no='X', pay_amount=0, extra_data={})
        await handle_lead_pack_paid(bad)
        async with async_db_session() as db:
            row = (await db.execute(select(LeadQuota).where(LeadQuota.user_id == uid))).scalar_one()
            assert row.purchased_balance == 10  # 未变
    finally:
        async with async_db_session.begin() as db:
            await db.execute(delete(LeadQuota).where(LeadQuota.user_id == uid))


async def test_request_leads_shortfall_and_consume(session, monkeypatch) -> None:
    """端到端：先查池→额度闸压量→交付即扣减→返回 shortfall+quota（超额引导购买）。"""
    monkeypatch.setattr(settings, 'GROWTH_FREE_LEADS_PER_MONTH', 2)
    monkeypatch.setattr(settings, 'GROWTH_LEAD_UNIT_PRICE_FEN', 100)
    uid = _uid()
    tag = uuid.uuid4().hex[:8]
    uniq = f'额度查询词{tag}'
    # 公共池播种 3 条命中（≥ 放行量 2，无缺口不补爬）
    for i in range(3):
        session.add(
            LeadContact(
                lead_no=f'LQ{tag.upper()}{i}', company_name=uniq,
                contact_name='池主', email=f'p{i}@uniq.com', source_type='firecrawl',
                status='valid', confidence_score=80,
            )
        )
    await session.flush()

    res = await lead_pool_query_service.request_leads(session, user_id=uid, limit=5, keyword=uniq, reveal_pii=True)
    # 请求 5 但免费仅 2 → 放行 2、交付 2、shortfall 3（引导支付购买）
    assert res['requested'] == 5 and res['allowed'] == 2 and res['delivered'] == 2
    assert res['shortfall'] == 3 and res['from_pool'] == 2
    assert res['backfill_job_id'] is None  # 池足量无缺口
    assert res['quota']['free_remaining'] == 0 and res['quota']['unit_price_fen'] == 100

    # 免费耗尽：再请求 → 放行 0、交付 0、全额 shortfall（必须购买）
    res2 = await lead_pool_query_service.request_leads(session, user_id=uid, limit=3, keyword=uniq, reveal_pii=True)
    assert res2['allowed'] == 0 and res2['delivered'] == 0 and res2['shortfall'] == 3


async def test_lead_pack_order_pricing_guards(session, monkeypatch) -> None:
    """lead_pack 订单定价守卫：条数 ≤0 或单价 ≤0 直接拒（不触达支付 SDK·零 mock）。"""
    from backend.app.billing.schema.pay_order import CreatePayOrderParam
    from backend.app.billing.service.pay_order_service import PayOrderService
    from backend.common.exception import errors

    monkeypatch.setattr(settings, 'GROWTH_LEAD_UNIT_PRICE_FEN', 0)  # 单价未配置
    obj = CreatePayOrderParam(order_type='lead_pack', channel_code='alipay_qr', lead_count=5)
    with pytest.raises(errors.RequestError):
        await PayOrderService._create_lead_pack_order(
            db=session, user_id=_uid(), obj=obj, user_ip=None, app_code='huanxing'
        )
