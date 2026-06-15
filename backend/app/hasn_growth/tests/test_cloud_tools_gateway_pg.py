"""获客云端工具数据面（gateway_internal）真实 PG 验证（零 mock，事务末尾回滚）。

证明获客整改后的**云端工具模型**端到端可用：`hasn.growth.*` 不再经 hasn-node 本地 hasn-mcp / daemon
Agent 代理，而是经 `ai_native_runtime_gateway._internal_handlers()`（`_dispatch_tool` 在
transport=gateway_internal 时查的同一张注册表）→ `growth_tool_handlers.handle_growth_*` → 进程内直调
hasn_growth service，打真实 PostgreSQL。

本测试驱动的就是真实调用面在通过鉴权/工作区/审计闸门后落到的**那一行** `handler(db, agent, input)`，
与 community/knowledge 云端工具同形。鉴权/工作区/审计闸门是 app 无关的平台层，已由
backend/tests/hasn/test_ai_native_app_platform.py 覆盖，不在此重复。

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

from backend.app.hasn.service.ai_native_runtime_gateway import ai_native_runtime_gateway
from backend.app.hasn_core import HasnHumans
from backend.app.hasn_growth.manifest import GROWTH_AI_NATIVE_MANIFEST
from backend.app.hasn_growth.model.lead_contact import LeadContact
from backend.common.dataclasses import AgentTokenPayload
from backend.common.exception.errors import ForbiddenError
from backend.database.db import SQLALCHEMY_DATABASE_URL

if TYPE_CHECKING:
    from collections.abc import AsyncIterator

pytestmark = pytest.mark.asyncio

# 整改后获客工具走云端 gateway_internal 的同一张 handler 注册表。
_REG = ai_native_runtime_gateway._internal_handlers()


@pytest_asyncio.fixture
async def ctx() -> AsyncIterator[SimpleNamespace]:
    engine = create_async_engine(SQLALCHEMY_DATABASE_URL, poolclass=NullPool)
    try:
        async with engine.connect() as conn:
            await conn.execute(select(1))
    except Exception as exc:  # noqa: BLE001
        await engine.dispose()
        pytest.skip(f'本地 PostgreSQL 不可达，跳过: {exc!r}')

    session = async_sessionmaker(engine, expire_on_commit=False)()
    tag = uuid.uuid4().hex[:8]
    owner = f'h_gtc_{tag}'
    owner_uid = 960000 + int(uuid.uuid4().int % 9000)
    agent_hasn = f'a_gtc_{tag}'

    session.add(HasnHumans(hasn_id=owner, star_id=f's_{owner_uid}', user_id=owner_uid, nickname='主人', status='active'))
    lead = LeadContact(
        lead_no=f'L{tag.upper()}', lead_scope='user', user_id=owner_uid, company_name='Acme',
        contact_name='王五', email='wangwu@acme.com', phone='13800138000',
        source_type='firecrawl', status='valid', confidence_score=72,
    )
    session.add(lead)
    await session.flush()

    def _agent(scopes: list[str]) -> AgentTokenPayload:
        return AgentTokenPayload(
            agent_hasn_id=agent_hasn, agent_name=f'agent_{tag}', owner_hasn_id=owner,
            owner_user_id=owner_uid, scopes=scopes, session_uuid=f'sess_{tag}',
            expire_time=datetime(2099, 1, 1, tzinfo=UTC),
        )

    try:
        yield SimpleNamespace(session=session, owner=owner, owner_uid=owner_uid, agent_hasn=agent_hasn,
                              lead_id=lead.id, agent=_agent)
    finally:
        await session.rollback()
        await session.close()
        await engine.dispose()


async def test_all_18_growth_tools_resolve_in_gateway_registry() -> None:
    """manifest tools[].handler 与网关 handler 注册表零漂移：每条都能 dispatch 到真实 handler。"""
    handlers = [t['handler'] for t in GROWTH_AI_NATIVE_MANIFEST['tools']]
    assert len(handlers) == 18
    for h in handlers:
        assert h.startswith('growth.'), h
        assert h in _REG, f'manifest 声明的工具 handler {h} 未在网关注册表中'
        assert callable(_REG[h])


async def test_growth_cloud_tools_lifecycle_via_gateway_handlers(ctx) -> None:
    """单客户生命周期经云端 handler 链：检索(脱敏)→晋级→列客户(scope)→记活动→触达必审→商机→成交→漏斗。"""
    s, agent = ctx.session, ctx.agent(['agent', 'growth:read', 'growth:manage', 'growth:outreach', 'growth:collect'])

    # 检索默认脱敏（agent scope 无 growth:pii）
    leads = await _REG['growth.lead_search'](s, agent, {'query': 'Acme', 'limit': 10})
    assert leads and leads[0]['email'] == 'w***@acme.com', 'agent 读类默认脱敏'

    # 晋级建客户（写）
    cust = await _REG['growth.lead_qualify'](s, agent, {'lead_contact_id': ctx.lead_id, 'intent_score': 80})
    cid = cust['id']
    assert cust['email'] == 'w***@acme.com'

    # 列客户：返回 items + scope 元数据（个人模式 owner_scope=personal，无企业）
    listed = await _REG['growth.customer_list'](s, agent, {'view': 'team'})
    assert any(it['id'] == cid for it in listed['items'])
    assert listed['scope']['enterprise_id'] is None and listed['scope']['owner_scope'] == 'personal'

    # 客户详情 + 时间线
    got = await _REG['growth.customer_get'](s, agent, {'customer_id': cid})
    assert got['id'] == cid
    tl = await _REG['growth.customer_timeline'](s, agent, {'customer_id': cid})
    assert isinstance(tl, (list, dict))

    # 记一条活动
    await _REG['growth.activity_log'](s, agent, {'customer_id': cid, 'kind': 'note', 'content': '电话沟通良好'})

    # 触达：首触达必 pending_approval（不可豁免）
    sent = await _REG['growth.outreach_send'](s, agent, {
        'customer_id': cid, 'channel': 'manual_assist', 'content': '您好，想聊聊获客', 'intent_note': '破冰',
    })
    assert sent['status'] == 'pending_approval'

    # 商机：立 → 推进 → 成交
    opp = await _REG['growth.opportunity_create'](s, agent, {'customer_id': cid, 'name': '年度 SaaS 订阅', 'amount': 120000})
    oid = opp['id']
    assert opp['stage'] == 'contacted'
    o1 = await _REG['growth.opportunity_update_stage'](s, agent, {'opportunity_id': oid, 'stage': 'proposal', 'note': '发了提案'})
    assert o1['stage'] == 'proposal'
    closed = await _REG['growth.deal_close'](s, agent, {'opportunity_id': oid, 'result': 'won', 'amount': 118000})
    assert closed['stage'] == 'closed_won'

    # 漏斗：本月赢单 ≥1
    funnel = await _REG['growth.report_funnel'](s, agent, {'view': 'team'})
    assert funnel['won_this_month']['count'] >= 1 and funnel['won_this_month']['amount'] >= 118000


async def test_growth_cloud_tools_pii_scope_reveals_plaintext(ctx) -> None:
    """持 growth:pii 增强 scope 的 agent → 读类回明文（默认脱敏，PII 边界经 handler 透传）。"""
    s = ctx.session
    masked = await _REG['growth.lead_search'](s, ctx.agent(['agent', 'growth:read']), {'query': 'Acme'})
    assert masked and masked[0]['email'] == 'w***@acme.com'
    revealed = await _REG['growth.lead_search'](s, ctx.agent(['agent', 'growth:read', 'growth:pii']), {'query': 'Acme'})
    assert revealed and revealed[0]['email'] == 'wangwu@acme.com'


async def test_growth_cloud_tools_reassign_requires_manager(ctx) -> None:
    """customer.reassign 经 handler 落到经理闸门：个人模式（非经理）→ ForbiddenError。"""
    s, agent = ctx.session, ctx.agent(['agent', 'growth:read', 'growth:manage'])
    cust = await _REG['growth.lead_qualify'](s, agent, {'lead_contact_id': ctx.lead_id, 'intent_score': 60})
    with pytest.raises(ForbiddenError):
        await _REG['growth.customer_reassign'](s, agent, {'customer_id': cust['id'], 'assignee': 'h_other'})
