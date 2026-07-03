"""获客「请求线索」派获客分身编排（doc10）——分身自主工具链 找→决策 真实 PG 验证（零 mock）。

被派发的 sales_advisor 在工作会话里跑的就是这条工具链，本测试对真实 PostgreSQL 逐个证明：
  ① 找：`hasn.growth.search_companies`（读穿中台，命中主人线索池即返回，分身无需分辨线索来源）
  ② 决策·加为客户：`hasn.growth.lead.qualify`（晋级线索建客户）
  ③ 决策·继续找商机：`hasn.growth.opportunity.create`（立商机）

LLM 编排是这条链上唯一无法自动化的一环（需活体 runtime + 主人机器），但**它驱动的每个工具**在此
落到的正是被派发分身工具调用穿过网关后执行的那一行 `handler(db, agent, input)`
（与 test_cloud_tools_gateway_pg.py 同一注册表、同一调用面）。

**「找」用纯池命中路径**（唯一公司名 + limit=1 → `from_pool>=1, fetched==0`），故本测试确定性、零 qcc、
零外部依赖、零费用。read-through 不足时经 qcc 补拉的那半段由 GROWTH-QCC-4 的 infra-gated 活体测试覆盖
（那条已实证真打 qcc `fetched>0` 端到端可用）。

需要 export DATABASE_PORT=15432。
"""

from __future__ import annotations

import uuid

from datetime import UTC, datetime
from types import SimpleNamespace
from typing import TYPE_CHECKING

import pytest
import pytest_asyncio

from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

from backend.app.hasn_core import HasnHumans
from backend.app.hasn_core.app_platform import ai_native_runtime_gateway
from backend.app.hasn_growth.model.lead_contact import LeadContact
from backend.app.hasn_growth.model.lead_ref import LeadRef
from backend.common.dataclasses import AgentTokenPayload
from backend.database.db import SQLALCHEMY_DATABASE_URL

if TYPE_CHECKING:
    from collections.abc import AsyncIterator

pytestmark = pytest.mark.asyncio

_REG = ai_native_runtime_gateway._internal_handlers()


@pytest_asyncio.fixture
async def ctx() -> AsyncIterator[SimpleNamespace]:
    engine = create_async_engine(SQLALCHEMY_DATABASE_URL, poolclass=NullPool)
    try:
        async with engine.connect() as conn:
            await conn.execute(select(1))
    except Exception as exc:
        await engine.dispose()
        pytest.skip(f'本地 PostgreSQL 不可达，跳过: {exc!r}')

    session = async_sessionmaker(engine, expire_on_commit=False)()
    tag = uuid.uuid4().hex[:8]
    owner = f'h_disp_{tag}'
    owner_uid = 970000 + int(uuid.uuid4().int % 9000)
    agent_hasn = f'a_disp_{tag}'
    # 唯一公司名（含 tag）：保证 search 关键词只命中本测试造的这一条池线索、且 qcc 对该 gibberish 无结果。
    company = f'唤星测试专用企业_{tag}'

    session.add(
        HasnHumans(hasn_id=owner, star_id=f's_{owner_uid}', user_id=owner_uid, nickname='主人', status='active')
    )
    # 模拟一条**已在主人线索池**的公司线索（此前 read-through / 采集入池的结果）。
    lead = LeadContact(
        lead_no=f'LD{tag.upper()}',
        pool_visibility='public',
        company_name=company,
        contact_name='王传福',
        email='wang@byd.example',
        phone='13900139000',
        source_type='qcc',
        status='new',
        confidence_score=88,
    )
    session.add(lead)
    await session.flush()
    session.add(LeadRef(user_id=owner_uid, lead_contact_id=lead.id, source='collect', status='new'))
    await session.flush()

    def _agent(scopes: list[str]) -> AgentTokenPayload:
        return AgentTokenPayload(
            agent_hasn_id=agent_hasn,
            agent_name=f'sales_advisor_{tag}',
            owner_hasn_id=owner,
            owner_user_id=owner_uid,
            session_uuid=f'sess_{tag}',
            expire_time=datetime(2099, 1, 1, tzinfo=UTC),
        )

    try:
        yield SimpleNamespace(
            session=session, owner=owner, owner_uid=owner_uid, lead_id=lead.id, company=company, tag=tag, agent=_agent
        )
    finally:
        await session.rollback()
        await session.close()
        await engine.dispose()


async def test_dispatch_find_then_decide_tool_chain(ctx: SimpleNamespace) -> None:
    """派发分身自主工具链：找（读穿池命中·零 qcc）→ 决策（qualify 加客户 + opportunity 找商机）。"""
    s = ctx.session
    agent = ctx.agent(['agent', 'growth:read', 'growth:manage'])

    # ① 找：search_companies 用唯一公司名 + limit=1 → 纯池命中（from_pool>=1 且 fetched==0，不触 qcc）。
    #    这正是分身「拿到线索、无需分辨来源」——工具就地把主人池里的线索交回来供分析。
    found = await _REG['growth.search_companies'](s, agent, {'query': ctx.company, 'limit': 1})
    assert found['from_pool'] >= 1, f"读穿应命中主人池: {found}"
    assert found['fetched'] == 0, f"唯一名 + limit=1 应纯池命中、零 qcc 拉取: {found}"
    hit = next((x for x in found['leads'] if x.get('company_name') == ctx.company), None)
    assert hit is not None, f"找到的线索里应含本测试公司: {found['leads']}"
    assert int(hit.get('lead_contact_id') or ctx.lead_id) == ctx.lead_id

    # ② 决策·加为客户：lead.qualify 晋级建客户
    cust = await _REG['growth.lead_qualify'](s, agent, {'lead_contact_id': ctx.lead_id, 'intent_score': 85})
    cid = cust['id']
    assert cid, f"qualify 应建出客户: {cust}"

    # 客户确实落库、可被 customer_list 检出（决策落地的证据）
    listed = await _REG['growth.customer_list'](s, agent, {'view': 'team'})
    assert any(it['id'] == cid for it in listed['items']), '晋级出的客户应在客户列表中'

    # ③ 决策·继续找商机：opportunity.create 立商机
    opp = await _REG['growth.opportunity_create'](
        s, agent, {'customer_id': cid, 'name': f'年度合作_{ctx.tag}', 'amount': 200000}
    )
    assert opp['id'] and opp['stage'] == 'contacted', f"应立出商机: {opp}"


async def test_dispatch_find_returns_lead_contact_id_for_downstream(ctx: SimpleNamespace) -> None:
    """找回的每条线索都带 lead_contact_id —— 分身后续 qualify/enrich/opportunity 的下钻锚点。"""
    s = ctx.session
    agent = ctx.agent(['agent', 'growth:read'])
    found = await _REG['growth.search_companies'](s, agent, {'query': ctx.company, 'limit': 1})
    assert found['leads'], f"应至少命中池里那条: {found}"
    for lead in found['leads']:
        assert lead.get('lead_contact_id'), f"每条找回线索须带 lead_contact_id 供下钻: {lead}"
