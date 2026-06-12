"""获客四 scope API M3-e 真实 HTTP E2E（零 mock，回滚）。

最小 app 同挂 agent/app/open 三面，override 鉴权与 DB 会话；真实 PG。覆盖：
- 信封 {code,msg,data}；
- agent 默认脱敏 / 持 growth:pii 回明文；owner(app) 看自己数据回明文；
- 触达审批状态机经 HTTP：agent send→pending，owner approve；
- open 落地页表单回流 → 建 inbound_form 客户；
- 跨户隔离（他 owner 的 agent → NotFound）。
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
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

from backend.app.hasn.model.hasn_humans import HasnHumans
from backend.app.hasn_growth.api.v1.agent.growth import router as agent_growth_router
from backend.app.hasn_growth.api.v1.app.growth import router as app_growth_router
from backend.app.hasn_growth.api.v1.open.forms import router as open_forms_router
from backend.app.hasn_growth.model.lead_contact import LeadContact
from backend.app.publish.model.site import Site
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
_APP.include_router(open_forms_router, prefix='/api/v1/growth/open')


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
    owner = f'h_grw_{tag}'
    owner_uid = 940000 + int(uuid.uuid4().int % 9000)
    other_uid = owner_uid + 1
    agent_hasn = f'a_grw_{tag}'
    publish_ref = f'pg{tag}'[:32]

    session.add(HasnHumans(hasn_id=owner, star_id=f's_{owner_uid}', user_id=owner_uid, nickname='主人', status='active'))
    session.add(HasnHumans(hasn_id=f'{owner}o', star_id=f's_{other_uid}', user_id=other_uid, nickname='他人', status='active'))
    # 采集线索（owner 私有）
    lead = LeadContact(
        lead_no=f'L{tag.upper()}', lead_scope='user', user_id=owner_uid, company_name='Acme',
        contact_name='王五', email='wangwu@acme.com', phone='13800138000',
        source_type='firecrawl', status='valid', confidence_score=72,
    )
    session.add(lead)
    # 落地页（供 open 表单回流解析 owner）
    session.add(Site(owner_id=owner, kind='page', title='获客落地页', slug=publish_ref, status='active', visibility='public'))
    await session.flush()

    async def _yield_session() -> AsyncIterator:  # noqa: RUF029
        yield session

    state = SimpleNamespace(owner_uid=owner_uid, scopes=['agent', 'growth:read', 'growth:manage', 'growth:outreach'])

    async def _agent_auth(request: Request) -> AgentTokenPayload:  # noqa: RUF029
        payload = AgentTokenPayload(
            agent_hasn_id=agent_hasn, agent_name=f'agent_{tag}', owner_hasn_id=owner,
            owner_user_id=state.owner_uid, scopes=state.scopes, session_uuid=f'sess_{tag}',
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
            client=client, session=session, owner_uid=owner_uid, other_uid=other_uid,
            lead_id=lead.id, publish_ref=publish_ref, state=state,
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


async def test_four_scope_funnel_flow(e2e) -> None:
    c = e2e.client
    A = '/api/v1/growth/agent'
    O = '/api/v1/growth/app'

    # --- Agent: 检索线索（默认脱敏） ---
    leads = _ok(await c.get(f'{A}/leads', params={'q': 'Acme'}))
    assert leads and leads[0]['email'] == 'w***@acme.com'

    # --- Agent: 持 growth:pii 时回明文 ---
    e2e.state.scopes = ['agent', 'growth:read', 'growth:pii']
    leads_pii = _ok(await c.get(f'{A}/leads', params={'q': 'Acme'}))
    assert leads_pii[0]['email'] == 'wangwu@acme.com'
    e2e.state.scopes = ['agent', 'growth:read', 'growth:manage', 'growth:outreach']

    # --- Agent: qualify → 建客户 ---
    cust = _ok(await c.post(f'{A}/leads/{e2e.lead_id}/qualify', json={'profile': {'pain': '获客'}, 'intent_score': 80}))
    cid = cust['id']
    assert cust['source_kind'] == 'outbound_crawl' and cust['email'] == 'w***@acme.com'

    # --- Agent: 发起触达 → 首触达 pending_approval ---
    sent = _ok(await c.post(f'{A}/outreach', json={'customer_id': cid, 'channel': 'manual_assist', 'content': '您好，想聊聊获客', 'intent_note': '破冰'}))
    mid = sent['id']
    assert sent['status'] == 'pending_approval'

    # --- Owner: 待审队列含这条，approve（改话术） ---
    pending = _ok(await c.get(f'{O}/outreach/pending'))
    assert any(p['id'] == mid for p in pending)
    approved = _ok(await c.post(f'{O}/outreach/{mid}/approve', json={'edited_content': '您好，约时间聊获客'}))
    assert approved['status'] == 'approved' and approved['content'] == '您好，约时间聊获客'

    # --- Owner: 看客户详情回明文 ---
    owner_cust = _ok(await c.get(f'{O}/customers/{cid}'))
    assert owner_cust['phone'] == '13800138000'

    # --- Owner: 漏斗总览 ---
    funnel = _ok(await c.get(f'{O}/report/funnel'))
    assert funnel['following'] >= 1

    # --- Open: 落地页表单回流 → 建 inbound_form 客户 ---
    form = _ok(await c.post(
        f'/api/v1/growth/open/forms/{e2e.publish_ref}/submit',
        json={'company_name': 'Beta', 'contact_name': '赵六', 'email': 'zhaoliu@beta.com', 'message': '想了解'},
    ))
    assert form['status'] == 'converted' and form['customer_id']

    # --- Open: 蜜罐字段 → spam 不进漏斗 ---
    spam = _ok(await c.post(
        f'/api/v1/growth/open/forms/{e2e.publish_ref}/submit',
        json={'email': 'bot@x.com', 'website_url': 'http://spam'},
    ))
    assert spam['status'] == 'spam' and spam['customer_id'] is None

    # --- 跨户隔离：他 owner 的 agent 看不到本户客户 ---
    e2e.state.owner_uid = e2e.other_uid
    miss = await c.get(f'{A}/customers/{cid}')
    assert miss.status_code == 404, miss.text


async def test_agent_collect_and_outreach_status(e2e) -> None:
    """M4 接缝：collect.start/status（采集子域包装）+ outreach.status（按客户查触达）。"""
    c = e2e.client
    A = '/api/v1/growth/agent'

    # --- collect.start：发起采集 → 恒落主人私有池 ---
    job = _ok(await c.post(f'{A}/collect', json={'keyword': 'SaaS 获客', 'max_pages': 3}))
    assert job['status'] == 'pending' and job['lead_scope'] == 'user' and job['user_id'] == e2e.owner_uid
    job_id = job['id']

    # --- collect.status：查同一任务 ---
    status = _ok(await c.get(f'{A}/collect/{job_id}'))
    assert status['id'] == job_id and status['keyword'] == 'SaaS 获客'

    # --- outreach.status：qualify→send 后按客户查到该触达 ---
    cust = _ok(await c.post(f'{A}/leads/{e2e.lead_id}/qualify', json={'qualify_reason': '高意向'}))
    cid = cust['id']
    sent = _ok(await c.post(f'{A}/outreach', json={'customer_id': cid, 'channel': 'manual_assist', 'content': '您好', 'intent_note': '破冰'}))
    msgs = _ok(await c.get(f'{A}/outreach', params={'customer_id': cid}))
    assert any(m['id'] == sent['id'] and m['status'] == 'pending_approval' for m in msgs)

    # --- 跨户：他 owner 查本户采集任务 → 403 Forbidden ---
    e2e.state.owner_uid = e2e.other_uid
    miss = await c.get(f'{A}/collect/{job_id}')
    assert miss.status_code == 403, miss.text
