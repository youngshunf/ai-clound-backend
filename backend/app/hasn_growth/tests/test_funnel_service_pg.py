"""获客漏斗服务 M3-a 真实 PG 验收（零 mock，事务末尾回滚不污染库）。

覆盖：线索检索/详情（PII 脱敏 vs reveal）、qualify（建客户+回写线索态+qualify 活动+幂等）、
客户列表/详情/时间线、画像更新（silent 累计）、活动记录、dismiss、跨户隔离。
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
from backend.app.hasn_growth.service.funnel_service import growth_funnel_service
from backend.common.exception import errors
from backend.database.db import SQLALCHEMY_DATABASE_URL

pytestmark = pytest.mark.asyncio


@pytest_asyncio.fixture
async def session():
    engine = create_async_engine(SQLALCHEMY_DATABASE_URL, poolclass=NullPool)
    try:
        async with engine.connect() as conn:
            await conn.execute(select(1))
    except Exception as exc:  # noqa: BLE001
        await engine.dispose()
        pytest.skip(f'本地 PostgreSQL 不可达，跳过: {exc!r}')
    sess = async_sessionmaker(engine, expire_on_commit=False)()
    try:
        yield sess
    finally:
        await sess.rollback()
        await sess.close()
        await engine.dispose()


async def _seed_lead(sess, *, user_id: int, email: str, phone: str, company: str) -> LeadContact:
    lead = LeadContact(
        lead_no=f'L{uuid.uuid4().hex[:10].upper()}',
        lead_scope='user',  # ck_lead_contact_scope: user 作用域必须 user_id 非空
        user_id=user_id,
        company_name=company,
        contact_name='张三',
        email=email,
        phone=phone,
        industry='SaaS',
        source_type='firecrawl',
        keyword='CRM',
        status='valid',
        confidence_score=78.5,
    )
    sess.add(lead)
    await sess.flush()
    return lead


async def test_funnel_full_flow(session) -> None:
    uid = 970000 + int(uuid.uuid4().int % 9000)
    other_uid = uid + 1
    lead = await _seed_lead(session, user_id=uid, email='zhangsan@acme.com', phone='13800138000', company='Acme')

    # 1) 检索：默认脱敏
    found = await growth_funnel_service.search_leads(session, user_id=uid, query='Acme', limit=10)
    assert any(r['lead_contact_id'] == lead.id for r in found)
    hit = next(r for r in found if r['lead_contact_id'] == lead.id)
    assert hit['email'] == 'z***@acme.com'
    assert hit['phone'] == '1380****8000'

    # 2) 详情 reveal=True 回明文
    revealed = await growth_funnel_service.get_lead(session, user_id=uid, lead_contact_id=lead.id, reveal_pii=True)
    assert revealed['email'] == 'zhangsan@acme.com'
    assert revealed['phone'] == '13800138000'

    # 3) qualify → 建客户 + 回写线索态 + qualify 活动
    cust = await growth_funnel_service.qualify_lead(
        session, user_id=uid, lead_contact_id=lead.id, profile={'pain': '获客难'}, intent_score=80, owner_agent_id='a_x1'
    )
    assert cust['source_kind'] == 'outbound_crawl'
    assert cust['email'] == 'z***@acme.com'  # 出参脱敏
    assert cust['lifecycle_status'] == 'active'
    customer_id = cust['id']
    refreshed_lead = (await session.execute(select(LeadContact).where(LeadContact.id == lead.id))).scalar_one()
    assert refreshed_lead.status == 'qualified'

    # 4) qualify 幂等
    cust2 = await growth_funnel_service.qualify_lead(session, user_id=uid, lead_contact_id=lead.id)
    assert cust2['id'] == customer_id and cust2['customer_no'] == cust['customer_no']

    # 5) 列表 + 详情
    customers = await growth_funnel_service.list_customers(session, user_id=uid, limit=10)
    assert any(c['id'] == customer_id for c in customers)
    got = await growth_funnel_service.get_customer(session, user_id=uid, customer_id=customer_id, reveal_pii=False)
    assert got['phone'] == '1380****8000'

    # 6) 画像更新：silent → silent_round_count 累计
    await growth_funnel_service.update_customer_profile(
        session, user_id=uid, customer_id=customer_id, intent_score=85, lifecycle_status='silent'
    )
    again = await growth_funnel_service.get_customer(session, user_id=uid, customer_id=customer_id)
    assert again['lifecycle_status'] == 'silent'
    assert again['silent_round_count'] == 1
    assert again['intent_score'] == 85.0

    # 7) 记活动 + 时间线（qualify + note）
    await growth_funnel_service.log_activity(
        session, user_id=uid, customer_id=customer_id, kind='note', content='电话沟通，对方有预算', actor_kind='agent', actor_id='a_x1'
    )
    timeline = await growth_funnel_service.customer_timeline(session, user_id=uid, customer_id=customer_id)
    kinds = {t['kind'] for t in timeline}
    assert 'qualify' in kinds and 'note' in kinds

    # 8) dismiss 另一条线索
    lead2 = await _seed_lead(session, user_id=uid, email='b@b.com', phone='13900139000', company='Beta')
    res = await growth_funnel_service.dismiss_lead(session, user_id=uid, lead_contact_id=lead2.id, reason='行业不符')
    assert res['status'] == 'rejected'
    # dismiss 后不再出现在检索
    after = await growth_funnel_service.search_leads(session, user_id=uid, query='Beta', limit=10)
    assert all(r['lead_contact_id'] != lead2.id for r in after)

    # 9) 跨户隔离：他人查本户客户 → NotFound
    with pytest.raises(errors.NotFoundError):
        await growth_funnel_service.get_customer(session, user_id=other_uid, customer_id=customer_id)
    # 他人检索看不到本户线索（user 作用域私有）
    other_found = await growth_funnel_service.search_leads(session, user_id=other_uid, query='Acme', limit=10)
    assert all(r['lead_contact_id'] != lead.id for r in other_found)
