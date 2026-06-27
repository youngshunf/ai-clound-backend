"""获客全链路「单客户生命周期」E2E（M8，零 mock，真实 PG，事务末尾回滚）。

把一个客户从 检索(脱敏)→晋级→触达起草→首触达必审→owner edit-then-approve→发送态流转
→客户回复(J3 run_now+防抖)→立商机→推进阶段→成交→漏斗 won_this_month 串成**一条连续业务链**，
并在链路上验合规闸门（首触达不可豁免 / optout 硬闸 / 频控）与 agent 脱敏 ↔ owner 明文 的 PII 边界。

对应施工清单 docs/AI自动获客任务系统/实施/91 的 M8 §3 活体场景 **2/4/5/6/7 的云端业务实质**——
经真实 HTTP（agent/owner 两 scope，鉴权与 DB 会话 override）+ 服务层（record_inbound_reply 无 HTTP 路由）
打真实 PostgreSQL。场景 1（从模板建分身 + 工作台挂载 + tools.list）与 LLM 自主决策属运行时（hermes）
范畴，需活体栈，不在本测试内。

需要 export DATABASE_PORT=15432。
"""

from __future__ import annotations

import uuid

from datetime import UTC, datetime
from types import SimpleNamespace
from typing import TYPE_CHECKING

import httpx
import pytest
import pytest_asyncio

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

from backend.app.hasn_core import HasnHumans
from backend.app.hasn_growth.api.v1.agent.growth import router as agent_growth_router
from backend.app.hasn_growth.api.v1.app.growth import router as app_growth_router
from backend.app.hasn_growth.model.lead_contact import LeadContact
from backend.app.hasn_growth.model.lead_ref import LeadRef
from backend.app.hasn_growth.service.funnel_service import growth_funnel_service
from backend.app.hasn_growth.service.opportunity_flow_service import growth_opportunity_service
from backend.app.hasn_growth.service.outreach_service import growth_outreach_service
from backend.app.hasn_growth.service.report_service import growth_report_service
from backend.common.dataclasses import AgentTokenPayload
from backend.common.exception.errors import BaseExceptionError
from backend.common.security.agent_jwt_auth import agent_jwt_auth
from backend.common.security.jwt import DependsJwtAuth
from backend.database.db import SQLALCHEMY_DATABASE_URL, get_db, get_db_transaction

if TYPE_CHECKING:
    from collections.abc import AsyncIterator

pytestmark = pytest.mark.asyncio

_APP = FastAPI()
_APP.include_router(agent_growth_router, prefix='/api/v1/growth/agent')
_APP.include_router(app_growth_router, prefix='/api/v1/growth/app')


@_APP.exception_handler(BaseExceptionError)
async def _err_handler(_request: Request, exc: BaseExceptionError) -> JSONResponse:  # noqa: RUF029
    return JSONResponse(status_code=exc.code, content={'code': exc.code, 'msg': str(exc.msg), 'data': None})


@pytest_asyncio.fixture
async def e2e() -> AsyncIterator[SimpleNamespace]:
    engine = create_async_engine(SQLALCHEMY_DATABASE_URL, poolclass=NullPool)
    try:
        async with engine.connect() as conn:
            await conn.execute(select(1))
    except Exception as exc:  # noqa: BLE001
        await engine.dispose()
        pytest.skip(f'本地 PostgreSQL 不可达，跳过: {exc!r}')

    session = async_sessionmaker(engine, expire_on_commit=False)()
    tag = uuid.uuid4().hex[:8]
    owner = f'h_e2e_{tag}'
    owner_uid = 970000 + int(uuid.uuid4().int % 9000)
    agent_hasn = f'a_e2e_{tag}'
    task_uuid = f'tk_e2e_{tag}'

    session.add(HasnHumans(hasn_id=owner, star_id=f's_{owner_uid}', user_id=owner_uid, nickname='主人', status='active'))
    lead = LeadContact(
        lead_no=f'L{tag.upper()}', pool_visibility='public', company_name='Acme',
        contact_name='王五', email='wangwu@acme.com', phone='13800138000',
        source_type='firecrawl', status='new', confidence_score=72,
    )
    session.add(lead)
    await session.flush()
    session.add(LeadRef(user_id=owner_uid, lead_contact_id=lead.id, source='collect', status='new'))
    await session.flush()
    # J3 跟进任务（scheduled）——客户回复触发 run_now 的绑定目标
    await session.execute(
        text(
            'INSERT INTO hasn_task.task (owner_id, agent_id, name, prompt, schedule_type, schedule_config, state, task_uuid) '
            "VALUES (:o, :a, :n, :p, 'interval', '{}'::jsonb, 'scheduled', :u)"
        ),
        {'o': owner, 'a': agent_hasn, 'n': '跟进任务', 'p': '跟进客户', 'u': task_uuid},
    )
    await session.flush()

    async def _yield_session() -> AsyncIterator:  # noqa: RUF029
        yield session

    state = SimpleNamespace(scopes=['agent', 'growth:read', 'growth:manage', 'growth:outreach'])

    async def _agent_auth(request: Request) -> AgentTokenPayload:  # noqa: RUF029
        payload = AgentTokenPayload(
            agent_hasn_id=agent_hasn, agent_name=f'agent_{tag}', owner_hasn_id=owner,
            owner_user_id=owner_uid, scopes=state.scopes, session_uuid=f'sess_{tag}',
            expire_time=datetime(2099, 1, 1, tzinfo=UTC),
        )
        request.state.agent = payload
        return payload

    async def _owner_auth(request: Request) -> None:  # noqa: RUF029
        request.scope['user'] = SimpleNamespace(id=owner_uid, hasn_id=owner)

    _APP.dependency_overrides[get_db] = _yield_session
    _APP.dependency_overrides[get_db_transaction] = _yield_session
    _APP.dependency_overrides[agent_jwt_auth] = _agent_auth
    _APP.dependency_overrides[DependsJwtAuth.dependency] = _owner_auth

    client = httpx.AsyncClient(transport=httpx.ASGITransport(app=_APP), base_url='http://e2e')
    try:
        yield SimpleNamespace(
            client=client, session=session, owner=owner, owner_uid=owner_uid,
            agent_hasn=agent_hasn, lead_id=lead.id, task_uuid=task_uuid,
        )
    finally:
        await client.aclose()
        _APP.dependency_overrides.clear()
        await session.rollback()
        await session.close()
        await engine.dispose()


def _ok(resp: httpx.Response) -> dict:
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert set(body) >= {'code', 'msg', 'data'}, body  # 统一信封铁律
    assert body['code'] == 200, body
    return body['data']


async def test_fullchain_single_customer_lifecycle(e2e) -> None:
    """场景 2/4/5/6 串成一条链：检索(脱敏)→晋级→触达必审→批准→发送→回复(J3)→商机→成交→漏斗。"""
    c = e2e.client
    A = '/api/v1/growth/agent'
    O = '/api/v1/growth/app'

    # --- 场景2：agent 检索线索默认脱敏 → 晋级建客户 ---
    leads = _ok(await c.get(f'{A}/leads', params={'q': 'Acme'}))
    assert leads and leads[0]['email'] == 'w***@acme.com', 'agent scope 出参必脱敏'
    cust = _ok(await c.post(f'{A}/leads/{e2e.lead_id}/qualify', json={'intent_score': 80}))
    cid = cust['id']
    assert cust['email'] == 'w***@acme.com'
    # owner 看自己客户 → 明文（PII 边界）
    owner_cust = _ok(await c.get(f'{O}/customers/{cid}'))
    assert owner_cust['phone'] == '13800138000' and owner_cust['email'] == 'wangwu@acme.com'
    # 绑定 J3 跟进任务（复用客户画像 PATCH followup_task_id）
    await growth_funnel_service.update_customer_profile(
        e2e.session, user_id=e2e.owner_uid, customer_id=cid, followup_task_id=e2e.task_uuid
    )

    # --- 场景4：首触达起草 → 必 pending_approval（不可豁免）→ owner edit-then-approve → 发送态流转 ---
    sent = _ok(await c.post(f'{A}/outreach', json={
        'customer_id': cid, 'channel': 'manual_assist', 'content': '您好，想聊聊获客', 'intent_note': '破冰',
    }))
    mid = sent['id']
    assert sent['status'] == 'pending_approval', '首触达必审，不可豁免'
    pending = _ok(await c.get(f'{O}/outreach/pending'))
    assert any(p['id'] == mid for p in pending)
    approved = _ok(await c.post(f'{O}/outreach/{mid}/approve', json={'edited_content': '您好，约时间聊获客'}))
    assert approved['status'] == 'approved' and approved['content'] == '您好，约时间聊获客'
    # manual_assist：复制发送素材包回明文（仅此处渲染）+ 落 pii_read 审计
    packet = await growth_outreach_service.build_send_material(
        e2e.session, user_id=e2e.owner_uid, message_id=mid, actor_user_id=e2e.owner_uid
    )
    assert packet['contact']['phone'] == '13800138000'
    # 标记已发 → sent
    await growth_outreach_service.mark_sending(e2e.session, user_id=e2e.owner_uid, message_id=mid)
    s = await growth_outreach_service.mark_sent(e2e.session, user_id=e2e.owner_uid, message_id=mid, channel_actual='wechat')
    assert s['status'] == 'sent'

    # --- 场景5（J3）：客户回复 inbound → run_now 触发；10 分钟窗口内第二条防抖合并 ---
    r1 = await growth_outreach_service.record_inbound_reply(
        e2e.session, user_id=e2e.owner_uid, customer_id=cid, channel='wechat', content='可以，周三下午方便'
    )
    assert r1['followup_trigger'] == {'triggered': True, 'reason': 'run_now'}
    # run_now 真生效：任务 next_run_at 被置（云端置位，节点本地 tick 拾取）
    nra = (
        await e2e.session.execute(
            text('SELECT next_run_at FROM hasn_task.task WHERE owner_id = :o AND task_uuid = :u'),
            {'o': e2e.owner, 'u': e2e.task_uuid},
        )
    ).scalar_one()
    assert nra is not None
    r2 = await growth_outreach_service.record_inbound_reply(
        e2e.session, user_id=e2e.owner_uid, customer_id=cid, channel='wechat', content='你那边方便吗'
    )
    assert r2['followup_trigger'] == {'triggered': False, 'reason': 'debounced'}
    # 回复后客户态 engaged
    cust_after = _ok(await c.get(f'{O}/customers/{cid}'))
    assert cust_after['lifecycle_status'] == 'engaged'

    # --- 场景6：agent 立商机 → 推进阶段 → 成交登记 → owner 漏斗 won_this_month ---
    opp = _ok(await c.post(f'{A}/opportunities', json={'customer_id': cid, 'name': '年度 SaaS 订阅', 'amount': 120000}))
    oid = opp['id']
    assert opp['stage'] == 'contacted'
    o1 = _ok(await c.patch(f'{A}/opportunities/{oid}/stage', json={'stage': 'proposal', 'note': '发了提案'}))
    assert o1['stage'] == 'proposal'
    closed = _ok(await c.post(f'{A}/opportunities/{oid}/close', json={'result': 'won', 'amount': 118000}))
    assert closed['stage'] == 'closed_won'
    funnel = _ok(await c.get(f'{O}/report/funnel'))
    assert funnel['won_this_month']['count'] >= 1 and funnel['won_this_month']['amount'] >= 118000
    # 客户态终为 won
    cust_won = _ok(await c.get(f'{O}/customers/{cid}'))
    assert cust_won['lifecycle_status'] == 'won'


async def test_fullchain_compliance_gates(e2e) -> None:
    """场景 7：合规闸门——首触达必审（上一测试已证）+ 频控第 3 条拦 + optout 硬闸全渠道拦。"""
    c = e2e.client
    A = '/api/v1/growth/agent'

    # 频控：同客户同渠道一周内 ≤2，第三条被拦（用 agent HTTP 真实链路）
    cust = _ok(await c.post(f'{A}/leads/{e2e.lead_id}/qualify', json={'intent_score': 60}))
    cid = cust['id']
    f1 = _ok(await c.post(f'{A}/outreach', json={'customer_id': cid, 'channel': 'email', 'content': '第一封跟进'}))
    f2 = _ok(await c.post(f'{A}/outreach', json={'customer_id': cid, 'channel': 'email', 'content': '第二封跟进'}))
    f3 = _ok(await c.post(f'{A}/outreach', json={'customer_id': cid, 'channel': 'email', 'content': '第三封跟进'}))
    assert f1['status'] == 'pending_approval' and f2['status'] == 'pending_approval'
    assert f3['status'] == 'blocked_compliance' and f3['compliance_check']['freq_exceeded'] is True

    # optout 硬闸：owner 登记客户退订(all) → agent 任意渠道触达被拦 blocked_optout
    await growth_outreach_service.register_optout(
        e2e.session, user_id=e2e.owner_uid, channel='all', address='wangwu@acme.com', customer_id=cid, reason='客户明示拒绝'
    )
    blocked = _ok(await c.post(f'{A}/outreach', json={'customer_id': cid, 'channel': 'wechat', 'content': '在吗'}))
    assert blocked['status'] == 'blocked_optout'


async def test_fullchain_cross_owner_isolation_and_pii(e2e) -> None:
    """跨户隔离 + PII 边界：本户 agent 可见自己客户；他户漏斗全 0。

    （scope→脱敏 的 growth:pii 明文回参由 test_growth_api_pg.py 的专项用例覆盖，
    本测试聚焦「同一条链上」的跨户数据隔离与漏斗归属。）
    """
    c = e2e.client
    A = '/api/v1/growth/agent'

    cust = _ok(await c.post(f'{A}/leads/{e2e.lead_id}/qualify', json={'intent_score': 70}))
    cid = cust['id']

    # 本户 agent 可见自己客户（出参脱敏）
    mine_cust = _ok(await c.get(f'{A}/customers/{cid}'))
    assert mine_cust['id'] == cid and mine_cust['email'] == 'w***@acme.com'

    # 跨户隔离：本户漏斗有 1 个 following（刚晋级），他户漏斗全 0
    mine = await growth_report_service.funnel_overview(e2e.session, user_id=e2e.owner_uid)
    assert mine['following'] >= 1
    empty = await growth_report_service.funnel_overview(e2e.session, user_id=e2e.owner_uid + 12345)
    assert empty['following'] == 0 and empty['won_this_month']['count'] == 0
