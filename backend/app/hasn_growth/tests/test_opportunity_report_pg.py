"""获客商机/成交 + 漏斗报表 M3-c 真实 PG 验收（零 mock，事务末尾回滚）。

覆盖 §9 立商机/阶段推进(允许倒退)/成交·流失登记 + 客户态联动，§11 漏斗总览/分布统计 + 跨户隔离。
需要 export DATABASE_PORT=15432。
"""

from __future__ import annotations

import uuid

import pytest
import pytest_asyncio

from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

from backend.app.hasn_growth.model.lead_contact import LeadContact
from backend.app.hasn_growth.model.lead_ref import LeadRef
from backend.app.hasn_growth.service.funnel_service import growth_funnel_service
from backend.app.hasn_growth.service.opportunity_flow_service import growth_opportunity_service
from backend.app.hasn_growth.service.report_service import growth_report_service
from backend.common.exception import errors
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


async def _qualified_customer(sess, *, user_id: int, company: str) -> int:
    lead = LeadContact(
        lead_no=f'L{uuid.uuid4().hex[:10].upper()}',
        pool_visibility='public',
        company_name=company,
        email=f'{uuid.uuid4().hex[:6]}@{company.lower()}.com',
        source_type='firecrawl',
        status='new',
        confidence_score=60,
    )
    sess.add(lead)
    await sess.flush()
    sess.add(LeadRef(user_id=user_id, lead_contact_id=lead.id, source='collect', status='new'))
    await sess.flush()
    cust = await growth_funnel_service.qualify_lead(sess, user_id=user_id, lead_contact_id=lead.id)
    return cust['id']


async def test_opportunity_lifecycle(session) -> None:
    uid = 990000 + int(uuid.uuid4().int % 9000)
    other_uid = uid + 1
    cid = await _qualified_customer(session, user_id=uid, company='Zeta')

    # 立商机 → 客户态 opportunity + stage_change 活动
    opp = await growth_opportunity_service.create_opportunity(
        session, user_id=uid, customer_id=cid, name='年度 SaaS 订阅', amount=120000, currency='CNY',
        created_by_kind='agent', actor_id='a_z1',
    )
    assert opp['stage'] == 'contacted' and opp['opportunity_no'].startswith('OPP')
    cust = await growth_funnel_service.get_customer(session, user_id=uid, customer_id=cid)
    assert cust['lifecycle_status'] == 'opportunity'

    # 阶段推进 + 允许倒退
    o1 = await growth_opportunity_service.update_stage(session, user_id=uid, opportunity_id=opp['id'], stage='proposal', note='发了提案')
    assert o1['stage'] == 'proposal'
    o2 = await growth_opportunity_service.update_stage(session, user_id=uid, opportunity_id=opp['id'], stage='replied', note='客户要再看看')
    assert o2['stage'] == 'replied'  # 倒退允许
    timeline = await growth_funnel_service.customer_timeline(session, user_id=uid, customer_id=cid)
    assert sum(1 for t in timeline if t['kind'] == 'stage_change') >= 3  # create + 2 推进

    # close_deal 非法 result
    with pytest.raises(errors.RequestError):
        await growth_opportunity_service.close_deal(session, user_id=uid, opportunity_id=opp['id'], result='maybe')

    # 成交登记 → closed_won + 客户态 won + close 活动
    closed = await growth_opportunity_service.close_deal(
        session, user_id=uid, opportunity_id=opp['id'], result='won', amount=118000, close_note='季付转年付',
        actor_kind='owner', actor_id=str(uid),
    )
    assert closed['stage'] == 'closed_won' and closed['won_at'] is not None and closed['amount'] == 118000.0
    cust2 = await growth_funnel_service.get_customer(session, user_id=uid, customer_id=cid)
    assert cust2['lifecycle_status'] == 'won'

    # 收口后不可再改阶段/重复登记
    with pytest.raises(errors.ForbiddenError):
        await growth_opportunity_service.update_stage(session, user_id=uid, opportunity_id=opp['id'], stage='negotiation')
    with pytest.raises(errors.ForbiddenError):
        await growth_opportunity_service.close_deal(session, user_id=uid, opportunity_id=opp['id'], result='lost')

    # 跨户隔离
    with pytest.raises(errors.NotFoundError):
        await growth_opportunity_service.get_opportunity(session, user_id=other_uid, opportunity_id=opp['id'])


async def test_funnel_report(session) -> None:
    uid = 992000 + int(uuid.uuid4().int % 9000)

    # 两客户：一个开商机后成交，一个保持跟进
    c_won = await _qualified_customer(session, user_id=uid, company='Won')
    c_follow = await _qualified_customer(session, user_id=uid, company='Follow')

    opp = await growth_opportunity_service.create_opportunity(
        session, user_id=uid, customer_id=c_won, name='成交单', amount=50000,
    )
    # 一个开着的商机（用第二个客户）
    await growth_opportunity_service.create_opportunity(
        session, user_id=uid, customer_id=c_follow, name='进行中', amount=30000,
    )
    await growth_opportunity_service.close_deal(session, user_id=uid, opportunity_id=opp['id'], result='won', amount=50000)

    overview = await growth_report_service.funnel_overview(session, user_id=uid)
    assert overview['following'] >= 1  # c_follow 仍 opportunity（属跟进中）
    assert overview['opportunities']['count'] >= 1 and overview['opportunities']['amount'] >= 30000
    assert overview['won_this_month']['count'] >= 1 and overview['won_this_month']['amount'] >= 50000

    stages = await growth_report_service.stage_distribution(session, user_id=uid)
    assert stages.get('closed_won', 0) >= 1 and stages.get('contacted', 0) >= 1

    lifecycles = await growth_report_service.lifecycle_distribution(session, user_id=uid)
    assert lifecycles.get('won', 0) >= 1 and lifecycles.get('opportunity', 0) >= 1

    # 跨户：他人看本户漏斗全 0
    empty = await growth_report_service.funnel_overview(session, user_id=uid + 777)
    assert empty['following'] == 0 and empty['won_this_month']['count'] == 0
