"""获客四 scope API M3-e 真实 HTTP E2E（零 mock，回滚）。

最小 app 同挂 agent/app/open 三面，override 鉴权与 DB 会话；真实 PG。覆盖：
- 信封 {code,msg,data}；
- agent 与 owner 列表/详情恒脱敏（旧 growth:pii claim 不再授权明文）；
- 触达审批状态机经 HTTP：agent send→pending，owner approve；
- open 落地页表单回流 → 建 inbound_form 客户；
- 跨户隔离（他 owner 的 agent → NotFound）。
需要 export DATABASE_PORT=15432。
"""

from __future__ import annotations

import asyncio
import socket
import uuid

from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path
from types import SimpleNamespace
from typing import TYPE_CHECKING

import httpx
import pytest
import pytest_asyncio
import sqlalchemy as sa
import uvicorn

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

from backend.app.hasn_core import HasnAgents, HasnHumans
from backend.app.hasn_growth.api.v1.agent.growth import router as agent_growth_router
from backend.app.hasn_growth.api.v1.app.growth import router as app_growth_router
from backend.app.hasn_growth.api.v1.open.forms import router as open_forms_router
from backend.app.hasn_growth.model.contact_channel import ContactChannel
from backend.app.hasn_growth.model.contact_private_access_audit import (
    ContactPrivateAccessAudit,
)
from backend.app.hasn_growth.model.contact_private_profile import ContactPrivateProfile
from backend.app.hasn_growth.model.customer import Customer
from backend.app.hasn_growth.model.form_submission import FormSubmission
from backend.app.hasn_growth.model.growth_project import GrowthProject
from backend.app.hasn_growth.model.lead_contact import LeadContact
from backend.app.hasn_growth.model.lead_ref import LeadRef
from backend.app.hasn_growth.service.pii_keyring import require_growth_pii_keyring
from backend.app.hasn_growth.service.project_lead_service import project_lead_service
from backend.app.hasn_growth.service.scope_context import GrowthScope
from backend.app.hasn_project.model.hasn_project import HasnProject
from backend.app.hasn_publish.api.router import internal as publish_internal_router
from backend.app.hasn_publish.api.router import open_meta as publish_open_router
from backend.app.hasn_publish.model.revision import Revision
from backend.app.hasn_publish.model.site import Site
from backend.common.dataclasses import AgentTokenPayload
from backend.common.exception.errors import BaseExceptionError
from backend.common.security.agent_jwt_auth import agent_jwt_auth
from backend.common.security.jwt import DependsJwtAuth
from backend.core.conf import settings
from backend.database.db import SQLALCHEMY_DATABASE_URL, get_db, get_db_transaction

if TYPE_CHECKING:
    from collections.abc import AsyncIterator

pytestmark = pytest.mark.asyncio
_REPO = Path(__file__).resolve().parents[4]
_S6_MIGRATION_SQL = _REPO / 'backend/sql/hasn_growth/migrations/2026-07-29-add-lead-dedupe-keys.sql'
_S7_MIGRATION_SQL = _REPO / 'backend/sql/hasn_growth/migrations/2026-07-29-project-lead-qualification-idempotency.sql'
_S11_MIGRATION_SQL = _REPO / 'backend/sql/hasn_growth/migrations/2026-07-29-growth-review-v8.sql'

_APP = FastAPI()
_APP.include_router(agent_growth_router, prefix='/api/v1/growth/agent')
_APP.include_router(app_growth_router, prefix='/api/v1/growth/app')
_APP.include_router(open_forms_router, prefix='/api/v1/growth/open')
_APP.include_router(publish_open_router)
_APP.include_router(publish_internal_router)


@_APP.exception_handler(BaseExceptionError)
async def _err_handler(_request: Request, exc: BaseExceptionError) -> JSONResponse:  # noqa: RUF029
    return JSONResponse(status_code=exc.code, content={'code': exc.code, 'msg': str(exc.msg), 'data': None})


@pytest_asyncio.fixture
async def e2e() -> AsyncIterator[SimpleNamespace]:
    engine = create_async_engine(SQLALCHEMY_DATABASE_URL, poolclass=NullPool)
    try:
        async with engine.connect() as conn:
            await conn.execute(select(1))
    except Exception as exc:
        await engine.dispose()
        pytest.skip(f'本地 PostgreSQL 不可达，跳过: {exc!r}')

    session = async_sessionmaker(engine, expire_on_commit=False)()
    raw = await (await session.connection()).get_raw_connection()
    connection = raw.driver_connection
    assert connection is not None
    await connection.execute(_S6_MIGRATION_SQL.read_text(encoding='utf-8'))
    await connection.execute(_S7_MIGRATION_SQL.read_text(encoding='utf-8'))
    await connection.execute(_S11_MIGRATION_SQL.read_text(encoding='utf-8'))
    server_socket = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    server_socket.bind(('127.0.0.1', 0))
    server_socket.listen()
    server_socket.setblocking(False)
    server_port = int(server_socket.getsockname()[1])
    server = uvicorn.Server(
        uvicorn.Config(
            _APP,
            host='127.0.0.1',
            port=server_port,
            lifespan='off',
            log_level='error',
        )
    )
    server_task = asyncio.create_task(server.serve(sockets=[server_socket]))
    for _ in range(200):
        if server.started:
            break
        await asyncio.sleep(0.01)
    if not server.started:
        server.should_exit = True
        await server_task
        raise RuntimeError('测试 Publish 内部 HTTP 未能启动')
    previous_publish_internal = (
        settings.PUBLISH_INTERNAL_BASE_URL,
        settings.PUBLISH_INTERNAL_TOKEN,
    )
    settings.PUBLISH_INTERNAL_BASE_URL = f'http://127.0.0.1:{server_port}'
    settings.PUBLISH_INTERNAL_TOKEN = f'publish-test-{uuid.uuid4().hex}'
    tag = uuid.uuid4().hex[:8]
    owner = f'h_grw_{tag}'
    owner_uid = 93_700_000_000 + int(uuid.uuid4().int % 900_000_000)
    other_uid = owner_uid + 1
    agent_hasn = f'a_grw_{tag}'
    publish_ref = f'pg{tag}'[:32]
    unbound_publish_ref = f'px{tag}'[:32]
    enterprise_publish_ref = f'pe{tag}'[:32]

    session.add(
        HasnHumans(
            hasn_id=owner,
            star_id=f's_{owner_uid}',
            user_id=owner_uid,
            nickname=f'主人-{tag}',
            status='active',
        )
    )
    session.add(
        HasnAgents(
            hasn_id=agent_hasn,
            star_id=f'a_{tag}',
            owner_id=owner,
            display_name=f'获客分身-{tag}',
            agent_name=f'agent_{tag}',
            type='cloud',
            role='specialist',
            api_key_hash='test',
            status='active',
            created_via='client',
        )
    )
    session.add(
        HasnHumans(
            hasn_id=f'{owner}o',
            star_id=f's_{other_uid}',
            user_id=other_uid,
            nickname=f'其他主人-{tag}',
            status='active',
        )
    )
    # 采集线索（owner 私有）
    lead = LeadContact(
        lead_no=f'L{tag.upper()}',
        pool_visibility='public',
        company_name='Acme',
        contact_name='王五',
        email='wangwu@acme.com',
        phone='13800138000',
        source_type='firecrawl',
        status='new',
        confidence_score=Decimal(72),
    )
    session.add(lead)
    platform_project = HasnProject(owner_id=owner, name=f'获客项目-{tag}', status='active')
    session.add(platform_project)
    empty_platform_project = HasnProject(
        owner_id=owner,
        name=f'待启用获客-{tag}',
        status='active',
    )
    session.add(empty_platform_project)
    enterprise_platform_project = HasnProject(
        owner_id=owner,
        name=f'企业获客项目-{tag}',
        status='active',
        enterprise_id=uuid.uuid4(),
    )
    session.add(enterprise_platform_project)
    await session.flush()
    # 只有 Growth 权威绑定的落地页允许 open 表单回流；普通公开页面不能伪装成获客表单。
    landing_site = Site(
        owner_id=owner,
        kind='page',
        title='获客落地页',
        slug=publish_ref,
        source_app='growth',
        platform_project_id=platform_project.id,
        status='active',
        visibility='public',
    )
    session.add(landing_site)
    session.add(
        Site(
            owner_id=owner,
            kind='report',
            title='普通公开报告',
            slug=unbound_publish_ref,
            source_app='report',
            status='active',
            visibility='public',
        )
    )
    enterprise_site = Site(
        owner_id=owner,
        kind='page',
        title='企业获客落地页',
        slug=enterprise_publish_ref,
        source_app='growth',
        platform_project_id=enterprise_platform_project.id,
        status='active',
        visibility='public',
    )
    session.add(enterprise_site)
    await session.flush()
    for site, suffix in (
        (landing_site, 'personal'),
        (enterprise_site, 'enterprise'),
    ):
        revision = Revision(
            site_id=site.id,
            owner_id=owner,
            seq=1,
            asset_id=f'asset-{suffix}-{tag}',
            runtime='single-html',
            content_hash=f'hash-{suffix}-{tag}',
            size_bytes=1,
        )
        session.add(revision)
        await session.flush()
        site.current_revision_id = revision.id
    await session.flush()
    personal_growth_project = GrowthProject(
        platform_project_id=platform_project.id,
        user_id=owner_uid,
        owner_hasn_id=owner,
        name=f'获客漏斗-{tag}',
        owner_agent_id=agent_hasn,
        landing_site_ref=f'hasn://publish/sites/{landing_site.id}',
        status='active',
        provision_status='ready',
    )
    session.add(personal_growth_project)
    session.add(
        GrowthProject(
            platform_project_id=enterprise_platform_project.id,
            user_id=owner_uid,
            owner_hasn_id=owner,
            owner_scope='enterprise',
            enterprise_id=98_000_000 + int(uuid.uuid4().int % 1_000_000),
            name=f'企业获客漏斗-{tag}',
            landing_site_ref=f'hasn://publish/sites/{enterprise_site.id}',
            status='active',
            provision_status='ready',
        )
    )
    await session.flush()
    landing_site.source_ref = str(personal_growth_project.id)
    await session.flush()
    session.add(LeadRef(user_id=owner_uid, lead_contact_id=lead.id, source='collect', status='new'))
    await session.flush()

    async def _yield_session() -> AsyncIterator:  # noqa: RUF029
        yield session

    state = SimpleNamespace(owner_uid=owner_uid, scopes=['agent', 'growth:read', 'growth:manage', 'growth:outreach'])

    async def _agent_auth(request: Request) -> AgentTokenPayload:  # noqa: RUF029
        payload = AgentTokenPayload(
            agent_hasn_id=agent_hasn,
            agent_name=f'agent_{tag}',
            owner_hasn_id=owner,
            owner_user_id=state.owner_uid,
            session_uuid=f'sess_{tag}',
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
            client=client,
            session=session,
            owner=owner,
            agent_hasn=agent_hasn,
            owner_uid=owner_uid,
            other_uid=other_uid,
            lead_id=lead.id,
            publish_ref=publish_ref,
            unbound_publish_ref=unbound_publish_ref,
            enterprise_publish_ref=enterprise_publish_ref,
            platform_project_id=platform_project.id,
            growth_project_id=personal_growth_project.id,
            empty_platform_project_id=empty_platform_project.id,
            enterprise_platform_project_id=enterprise_platform_project.id,
            state=state,
        )
    finally:
        await client.aclose()
        _APP.dependency_overrides.clear()
        (
            settings.PUBLISH_INTERNAL_BASE_URL,
            settings.PUBLISH_INTERNAL_TOKEN,
        ) = previous_publish_internal
        server.should_exit = True
        await server_task
        server_socket.close()
        await session.rollback()
        await session.close()
        await engine.dispose()


def _ok(resp: httpx.Response) -> dict:
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert set(body) >= {'code', 'msg', 'data'}, body  # 统一信封铁律
    assert body['code'] == 200, body
    return body['data']


async def test_owner_growth_project_context_http_contract(e2e: SimpleNamespace) -> None:
    owner_api = '/api/v1/growth/app'

    current = _ok(await e2e.client.get(f'{owner_api}/projects/by-platform/{e2e.platform_project_id}'))
    assert current['platform_project']['id'] == str(e2e.platform_project_id)
    assert current['growth_project']['id'] == str(e2e.growth_project_id)

    detail = _ok(await e2e.client.get(f'{owner_api}/projects/{e2e.growth_project_id}'))
    assert detail['platform_project_id'] == str(e2e.platform_project_id)

    before_enable = _ok(await e2e.client.get(f'{owner_api}/projects/by-platform/{e2e.empty_platform_project_id}'))
    assert before_enable['growth_project'] is None
    enabled = _ok(
        await e2e.client.post(
            f'{owner_api}/projects',
            json={
                'platform_project_id': str(e2e.empty_platform_project_id),
                'trace_id': '88888888-8888-4888-8888-888888888888',
                'idempotency_key': 'growth-http-enable-s4',
                'name': 'HTTP 启用漏斗',
            },
        )
    )
    assert enabled['created'] is True
    assert enabled['growth_project']['platform_project_id'] == str(e2e.empty_platform_project_id)

    enterprise = await e2e.client.get(f'{owner_api}/projects/by-platform/{e2e.enterprise_platform_project_id}')
    assert enterprise.status_code == 422


async def test_growth_review_suggestion_and_policy_http_contract(e2e: SimpleNamespace) -> None:
    """分身只提交建议，Owner 审阅或显式改策略后才产生新版本。"""
    agent_api = '/api/v1/growth/agent'
    owner_api = '/api/v1/growth/app'
    project_id = str(e2e.growth_project_id)
    payload = {
        'suggestion_kind': 'channel',
        'proposal': {
            'quiet_hours_start': 20,
            'quiet_hours_end': 10,
            'daily_outreach_limit': 12,
            'monthly_budget': '88.00',
            'budget_currency': 'CNY',
        },
        'evidence': {
            'scope': '当前项目最近 30 天触达与回复事件',
            'event_count': 0,
            'insufficient_data': True,
            'limitations': ['当前项目尚无足够的触达回复样本'],
            'guaranteed_outcome': False,
        },
        'idempotency_key': f'http-s11-channel-{uuid.uuid4().hex}',
    }
    created = _ok(
        await e2e.client.post(
            f'{agent_api}/projects/{project_id}/review/suggestions',
            json=payload,
        )
    )
    replayed = _ok(
        await e2e.client.post(
            f'{agent_api}/projects/{project_id}/review/suggestions',
            json=payload,
        )
    )
    assert replayed['id'] == created['id']
    assert created['status'] == 'pending'

    before = _ok(await e2e.client.get(f'{owner_api}/projects/{project_id}/policy'))
    listed = _ok(await e2e.client.get(f'{owner_api}/projects/{project_id}/review/suggestions'))
    assert [item['id'] for item in listed] == [created['id']]
    rejected = _ok(
        await e2e.client.post(
            f'{owner_api}/projects/{project_id}/review/suggestions/{created["id"]}',
            json={'decision': 'reject'},
        )
    )
    assert rejected['status'] == 'rejected'
    assert _ok(await e2e.client.get(f'{owner_api}/projects/{project_id}/policy')) == before

    updated = _ok(
        await e2e.client.put(
            f'{owner_api}/projects/{project_id}/policy',
            json={
                'quiet_hours_start': 22,
                'quiet_hours_end': 8,
                'daily_outreach_limit': 15,
                'monthly_budget': '120.00',
                'budget_currency': 'CNY',
                'expected_policy_version': before['policy_version'],
            },
        )
    )
    assert updated['policy_version'] == before['policy_version'] + 1
    assert updated['quiet_hours_start'] == 22
    stale = await e2e.client.put(
        f'{owner_api}/projects/{project_id}/policy',
        json={
            'quiet_hours_start': 21,
            'quiet_hours_end': 9,
            'daily_outreach_limit': 20,
            'monthly_budget': None,
            'budget_currency': 'CNY',
            'expected_policy_version': before['policy_version'],
        },
    )
    assert stale.status_code == 409

    schedule_before = _ok(
        await e2e.client.get(
            f'{owner_api}/projects/{project_id}/review/schedule',
        )
    )
    assert schedule_before['enabled'] is False
    schedule_enabled = _ok(
        await e2e.client.put(
            f'{owner_api}/projects/{project_id}/review/schedule',
            json={'enabled': True},
        )
    )
    assert schedule_enabled['enabled'] is True
    assert schedule_enabled['schedule_display'] == '每周一 09:00'
    assert (
        _ok(
            await e2e.client.put(
                f'{owner_api}/projects/{project_id}/review/schedule',
                json={'enabled': True},
            )
        )['task_uuid']
        == schedule_enabled['task_uuid']
    )
    schedule_disabled = _ok(
        await e2e.client.put(
            f'{owner_api}/projects/{project_id}/review/schedule',
            json={'enabled': False},
        )
    )
    assert schedule_disabled['enabled'] is False
    assert schedule_disabled['state'] == 'paused'


async def test_owner_project_lead_batch_pagination_and_status_http_contract(
    e2e: SimpleNamespace,
) -> None:
    """Owner API 只读写项目关联行，并逐行返回导入错误与服务端分页。"""
    owner_api = '/api/v1/growth/app'
    project_id = str(e2e.growth_project_id)
    batch_id = f'http-s6-{uuid.uuid4().hex}'
    imported = _ok(
        await e2e.client.post(
            f'{owner_api}/projects/{project_id}/leads/import',
            json={
                'batch_id': batch_id,
                'items': [
                    {
                        'client_ref': 'http-valid',
                        'company_name': 'HTTP 受控样本企业',
                        'domain': f'{uuid.uuid4().hex}.example',
                        'industry': '企业软件',
                        'source_kind': 'controlled_import',
                        'source_ref': f'controlled://http/{batch_id}',
                        'match_score': 92,
                        'scoring_version': 'profile-v1/rules-v1',
                        'score_breakdown': {
                            'industry': {
                                'score': 92,
                                'explanation': '行业与已确认 ICP 一致',
                            }
                        },
                    },
                    {
                        'client_ref': 'http-invalid',
                        'company_name': '',
                        'source_kind': 'controlled_import',
                        'source_ref': f'controlled://http/{batch_id}/invalid',
                    },
                ],
            },
        )
    )
    assert imported['inserted'] == 1
    assert imported['error_count'] == 1
    assert imported['errors'][0]['client_ref'] == 'http-invalid'

    listed = _ok(
        await e2e.client.get(
            f'{owner_api}/projects/{project_id}/leads',
            params={'page': 1, 'size': 1, 'min_score': 90},
        )
    )
    assert listed['total'] == 1
    assert listed['page'] == listed['size'] == 1
    lead = listed['items'][0]
    assert lead['scoring_version'] == 'profile-v1/rules-v1'
    assert lead['evidence_freshness'] == 'unknown'
    assert lead['score_breakdown']['industry']['explanation']

    dismissed = _ok(
        await e2e.client.post(
            f'{owner_api}/projects/{project_id}/leads/{lead["id"]}/status',
            json={'action': 'dismiss', 'reason': '当前批次优先级不匹配'},
        )
    )
    assert dismissed['status'] == 'dismissed'
    dismissed_page = _ok(
        await e2e.client.get(
            f'{owner_api}/projects/{project_id}/leads',
            params={'status': 'dismissed', 'page': 1, 'size': 20},
        )
    )
    assert dismissed_page['total'] == 1
    restored = _ok(
        await e2e.client.post(
            f'{owner_api}/projects/{project_id}/leads/{lead["id"]}/status',
            json={'action': 'restore'},
        )
    )
    assert restored['status'] == 'new'

    personal_assign = await e2e.client.put(
        f'{owner_api}/projects/{project_id}/leads/{lead["id"]}/assignee',
        json={'assignee': e2e.owner},
    )
    assert personal_assign.status_code == 403


async def test_four_scope_funnel_flow(e2e: SimpleNamespace) -> None:
    c = e2e.client
    agent_api = '/api/v1/growth/agent'
    owner_api = '/api/v1/growth/app'

    # --- Agent: 检索线索（默认脱敏） ---
    leads = _ok(await c.get(f'{agent_api}/leads', params={'q': 'Acme'}))
    assert leads and leads[0]['email'] == 'w***@acme.com'

    # --- Agent: 旧 growth:pii claim 已退役，仍保持脱敏 ---
    e2e.state.scopes = ['agent', 'growth:read', 'growth:pii']
    leads_pii = _ok(await c.get(f'{agent_api}/leads', params={'q': 'Acme'}))
    assert leads_pii[0]['email'] == 'w***@acme.com'
    e2e.state.scopes = ['agent', 'growth:read', 'growth:manage', 'growth:outreach']

    # --- Agent: qualify → 建客户 ---
    cust = _ok(
        await c.post(
            f'{agent_api}/leads/{e2e.lead_id}/qualify',
            json={'profile': {'pain': '获客'}, 'intent_score': 80},
        )
    )
    cid = cust['id']
    assert cust['source_kind'] == 'outbound_crawl' and cust['email'] == 'w***@acme.com'

    # --- Agent: 发起触达 → 首触达 pending_approval ---
    sent = _ok(
        await c.post(
            f'{agent_api}/outreach',
            json={'customer_id': cid, 'channel': 'manual_assist', 'content': '您好，想聊聊获客', 'intent_note': '破冰'},
        )
    )
    mid = sent['id']
    assert sent['status'] == 'pending_approval'

    # 自有分身向主人请示走主会话汇报卡，不污染通知中心；业务事务登记真实 IM outbox。
    notifications = (
        await e2e.session.execute(
            text("SELECT id FROM hasn_notifications WHERE target_id = :owner AND type = 'growth.outreach.pending'"),
            {'owner': e2e.owner},
        )
    ).all()
    assert notifications == []
    cards = (
        (
            await e2e.session.execute(
                text(
                    'SELECT payload FROM hasn_notification_im_command_outbox '
                    "WHERE payload->'principal'->>'canonical_sender' = :agent "
                    "AND payload->'message'->'content'->'resource'->'metadata'->>'target_kind' "
                    "= 'outreach_message'"
                ),
                {'agent': e2e.agent_hasn},
            )
        )
        .mappings()
        .all()
    )
    assert len(cards) == 1
    assert cards[0]['payload']['message']['content']['metadata']['report'] is True

    # --- Owner: 待审队列含这条，approve（改话术） ---
    pending = _ok(await c.get(f'{owner_api}/outreach/pending'))
    assert any(p['id'] == mid for p in pending)
    approved = _ok(
        await c.post(
            f'{owner_api}/outreach/{mid}/approve',
            json={'edited_content': '您好，约时间聊获客'},
        )
    )
    assert approved['status'] == 'approved' and approved['content'] == '您好，约时间聊获客'

    # --- Owner: 看客户详情仍默认脱敏 ---
    owner_cust = _ok(await c.get(f'{owner_api}/customers/{cid}'))
    assert owner_cust['phone'] == '1380****8000'
    assert owner_cust['email'] == 'w***@acme.com'

    # --- Owner: 漏斗总览 ---
    funnel = _ok(await c.get(f'{owner_api}/report/funnel'))
    assert funnel['following'] >= 1

    # --- Open: 默认门禁关闭时拒绝；开启后 PII 只落私有表 ---
    previous_form_flags = (
        settings.GROWTH_PUBLISH_LANDING_ENABLED,
        settings.GROWTH_PII_NEW_WRITE_ENABLED,
        settings.GROWTH_FORM_PRIVACY_NOTICE_VERSION,
    )
    settings.GROWTH_PUBLISH_LANDING_ENABLED = False
    settings.GROWTH_PII_NEW_WRITE_ENABLED = True
    settings.GROWTH_FORM_PRIVACY_NOTICE_VERSION = 'growth-form-v1'
    try:
        form_token = _ok(
            await c.post(
                f'/api/v1/publish/open/sites/{e2e.publish_ref}/forms/growth-lead-v1/access-token',
                json={},
            )
        )['form_access_token']
        enterprise_form_token = _ok(
            await c.post(
                f'/api/v1/publish/open/sites/{e2e.enterprise_publish_ref}/forms/growth-lead-v1/access-token',
                json={},
            )
        )['form_access_token']

        def form_headers(
            *,
            token: str = form_token,
            key: str | None = None,
            extra: dict[str, str] | None = None,
        ) -> dict[str, str]:
            headers = {
                'Idempotency-Key': key or str(uuid.uuid4()),
                'X-Publish-Form-Token': token,
            }
            headers.update(extra or {})
            return headers

        gate_response = await c.post(
            f'/api/v1/growth/open/forms/{e2e.publish_ref}/submit',
            json={
                'company_name': 'Beta',
                'contact_name': '赵六',
                'email': 'zhaoliu@beta.com',
                'privacy_notice_version': 'growth-form-v1',
                'consent_purpose': 'sales_contact',
                'consent': True,
            },
            headers=form_headers(),
        )
        assert gate_response.status_code == 409

        settings.GROWTH_PUBLISH_LANDING_ENABLED = True
        settings.GROWTH_PII_NEW_WRITE_ENABLED = False
        pii_gate_response = await c.post(
            f'/api/v1/growth/open/forms/{e2e.publish_ref}/submit',
            json={
                'email': 'pii-gate@example.com',
                'privacy_notice_version': 'growth-form-v1',
                'consent_purpose': 'sales_contact',
                'consent': True,
            },
            headers=form_headers(),
        )
        assert pii_gate_response.status_code == 409

        settings.GROWTH_PII_NEW_WRITE_ENABLED = True
        missing_consent = await c.post(
            f'/api/v1/growth/open/forms/{e2e.publish_ref}/submit',
            json={'email': 'no-consent@example.com'},
            headers=form_headers(),
        )
        assert missing_consent.status_code == 422

        unbound_site = await c.post(
            f'/api/v1/growth/open/forms/{e2e.unbound_publish_ref}/submit',
            json={
                'email': 'unbound@example.com',
                'privacy_notice_version': 'growth-form-v1',
                'consent_purpose': 'sales_contact',
                'consent': True,
            },
            headers=form_headers(),
        )
        assert unbound_site.status_code == 404

        enterprise_site = await c.post(
            f'/api/v1/growth/open/forms/{e2e.enterprise_publish_ref}/submit',
            json={
                'email': 'enterprise@example.com',
                'privacy_notice_version': 'growth-form-v1',
                'consent_purpose': 'sales_contact',
                'consent': True,
            },
            headers=form_headers(token=enterprise_form_token),
        )
        assert enterprise_site.status_code == 409

        wrong_notice = await c.post(
            f'/api/v1/growth/open/forms/{e2e.publish_ref}/submit',
            json={
                'email': 'wrong-notice@example.com',
                'privacy_notice_version': 'attacker-controlled',
                'consent_purpose': 'sales_contact',
                'consent': True,
            },
            headers=form_headers(),
        )
        assert wrong_notice.status_code == 400

        form_idempotency_key = str(uuid.uuid4())
        form_payload = {
            'company_name': 'Beta',
            'contact_name': '赵六',
            'email': 'zhaoliu@beta.com',
            'phone': '13812345678',
            'message': '请联系 zhaoliu@beta.com',
            'privacy_notice_version': 'growth-form-v1',
            'consent_purpose': 'sales_contact',
            'consent': True,
            'utm': {
                'source': 'campaign',
                'campaign': '+1 (415) 555-2671',
                'content': 'zhaoliu_wechat',
            },
        }
        form = _ok(
            await c.post(
                f'/api/v1/growth/open/forms/{e2e.publish_ref}/submit',
                json=form_payload,
                headers=form_headers(
                    key=form_idempotency_key,
                    extra={
                        'x-real-ip': '203.0.113.42',
                        'x-forwarded-for': '198.51.100.19',
                        'referer': 'https://zhaoliu@campaign.example/path?email=zhaoliu@beta.com',
                    },
                ),
            )
        )
        assert form['status'] == 'received'
        assert set(form) == {'status', 'receipt_ref'}

        submission = (
            await e2e.session.execute(
                select(FormSubmission).where(FormSubmission.idempotency_key == form_idempotency_key)
            )
        ).scalar_one()
        assert submission is not None
        assert submission.email is None and submission.phone is None and submission.name is None
        assert submission.payload == {'message_received': True}
        assert submission.source_meta == {}
        keyring = require_growth_pii_keyring()
        assert submission.utm_source == (
            f'v{keyring.active_hmac_version}:{keyring.hmac_for("form_utm_source", "campaign")}'
        )
        assert submission.utm_campaign == (
            f'v{keyring.active_hmac_version}:{keyring.hmac_for("form_utm_campaign", "+1 (415) 555-2671")}'
        )
        assert submission.utm_content == (
            f'v{keyring.active_hmac_version}:{keyring.hmac_for("form_utm_content", "zhaoliu_wechat")}'
        )
        assert '+1 (415) 555-2671' not in str(submission)
        assert 'zhaoliu_wechat' not in str(submission)
        assert submission.ip_hmac == (f'v{keyring.active_hmac_version}:{keyring.hmac_for("ip", "203.0.113.42")}')
        assert submission.ip_hmac != (f'v{keyring.active_hmac_version}:{keyring.hmac_for("ip", "198.51.100.19")}')
        assert submission.referrer == 'https://campaign.example'
        assert submission.privacy_notice_version == 'growth-form-v1'
        assert submission.consent_purpose == 'sales_contact'
        assert submission.contact_private_profile_id
        assert submission.contact_channel_ids
        assert 'zhaoliu@beta.com' not in str(submission)

        inbound_customer = await e2e.session.get(Customer, submission.customer_id)
        assert inbound_customer is not None
        assert inbound_customer.contact_name is None
        assert inbound_customer.email is None
        assert inbound_customer.phone is None
        assert inbound_customer.wechat is None
        assert inbound_customer.profile_json == {}
        assert inbound_customer.lead_contact_id == submission.lead_contact_id
        inbound_contact = await e2e.session.get(LeadContact, submission.lead_contact_id)
        assert inbound_contact is not None
        assert inbound_contact.contact_name is None
        assert inbound_contact.email is None
        assert inbound_contact.email_normalized is None
        assert inbound_contact.phone is None
        assert inbound_contact.phone_normalized is None
        assert inbound_contact.meta_data == {}
        private_channels = (
            (
                await e2e.session.execute(
                    select(ContactChannel).where(
                        ContactChannel.id.in_(submission.contact_channel_ids),
                    )
                )
            )
            .scalars()
            .all()
        )
        assert {channel.channel for channel in private_channels} == {'email', 'phone'}
        assert all('zhaoliu@beta.com' not in channel.value_ciphertext for channel in private_channels)
        activity_content = (
            await e2e.session.execute(
                text(
                    'SELECT content FROM hasn_growth.activity '
                    "WHERE ref_table = 'form_submission' AND ref_id = :submission_id"
                ),
                {'submission_id': str(submission.id)},
            )
        ).scalar_one()
        assert activity_content == '落地页留资已进入待处理队列'
        assert submission.project_lead_id is not None
        assert submission.task_id == f'growth:inbound:{submission.id}'
        project_lead = (
            (
                await e2e.session.execute(
                    text(
                        'SELECT growth_project_id, source_kind, status '
                        'FROM hasn_growth.growth_project_lead WHERE id = :id'
                    ),
                    {'id': submission.project_lead_id},
                )
            )
            .mappings()
            .one()
        )
        assert str(project_lead['growth_project_id']) == str(e2e.growth_project_id)
        assert project_lead['source_kind'] == 'inbound_form'
        assert project_lead['status'] == 'new'
        task = (
            (
                await e2e.session.execute(
                    text(
                        'SELECT owner_id, agent_id, project_id, app_id FROM hasn_task.task WHERE task_uuid = :task_uuid'
                    ),
                    {'task_uuid': submission.task_id},
                )
            )
            .mappings()
            .one()
        )
        assert task['owner_id'] == e2e.owner
        assert task['agent_id'] == e2e.agent_hasn
        assert str(task['project_id']) == str(e2e.platform_project_id)
        assert task['app_id'] == 'growth'
        attribution = (
            (
                await e2e.session.execute(
                    text(
                        'SELECT idempotency_key, metadata '
                        'FROM hasn_growth.growth_attribution_event '
                        'WHERE customer_id = :customer_id AND event_type = :event_type '
                        'ORDER BY id'
                    ),
                    {'customer_id': submission.customer_id, 'event_type': 'inbound'},
                )
            )
            .mappings()
            .all()
        )
        assert [row['metadata']['touch_model'] for row in attribution] == [
            'first_touch',
            'last_touch',
        ]
        assert attribution[0]['metadata']['landing_revision_id']
        notification_count = await e2e.session.scalar(
            text(
                'SELECT count(*) FROM hasn_notifications '
                "WHERE target_id = :owner AND type = 'growth.form.received' "
                'AND dedupe_key = :dedupe_key'
            ),
            {
                'owner': e2e.owner,
                'dedupe_key': f'growth.form.received:{submission.id}',
            },
        )
        assert notification_count == 1
        landing_status = _ok(await c.get(f'/api/v1/growth/app/projects/{e2e.growth_project_id}/landing'))
        assert landing_status['attribution_summary']['first_touch_count'] == 1
        assert landing_status['attribution_summary']['last_touch_count'] == 1
        assert landing_status['attribution_summary']['latest_touch_at']
        assert landing_status['recent_submissions'][0]['id'] == submission.id

        profile_before = await e2e.session.get(
            ContactPrivateProfile,
            submission.contact_private_profile_id,
        )
        assert profile_before is not None
        profile_snapshot = (
            profile_before.contact_name_ciphertext,
            profile_before.title_ciphertext,
            profile_before.lawful_basis,
            profile_before.source_ref,
            profile_before.retention_until,
        )
        email_channel = next(channel for channel in private_channels if channel.channel == 'email')
        channel_snapshot = (
            email_channel.value_ciphertext,
            email_channel.lawful_basis,
            email_channel.source_ref,
            email_channel.consent_ref,
            email_channel.retention_until,
        )
        repeat = _ok(
            await c.post(
                f'/api/v1/growth/open/forms/{e2e.publish_ref}/submit',
                json=form_payload,
                headers=form_headers(key=form_idempotency_key),
            )
        )
        assert repeat == form
        idempotent_count = await e2e.session.scalar(
            select(sa.func.count())
            .select_from(FormSubmission)
            .where(FormSubmission.idempotency_key == form_idempotency_key)
        )
        assert idempotent_count == 1
        task_count = await e2e.session.scalar(
            text('SELECT count(*) FROM hasn_task.task WHERE task_uuid = :task_uuid'),
            {'task_uuid': submission.task_id},
        )
        attribution_count = await e2e.session.scalar(
            text(
                'SELECT count(*) FROM hasn_growth.growth_attribution_event '
                'WHERE customer_id = :customer_id AND event_type = :event_type'
            ),
            {'customer_id': submission.customer_id, 'event_type': 'inbound'},
        )
        assert task_count == 1
        assert attribution_count == 2
        idempotency_conflict = await c.post(
            f'/api/v1/growth/open/forms/{e2e.publish_ref}/submit',
            json={**form_payload, 'company_name': '被篡改公司'},
            headers=form_headers(key=form_idempotency_key),
        )
        assert idempotency_conflict.status_code == 409
        await e2e.session.refresh(inbound_contact)
        await e2e.session.refresh(inbound_customer)
        assert inbound_contact.company_name == 'Beta'
        assert inbound_customer.company_name == 'Beta'
        await e2e.session.refresh(profile_before)
        await e2e.session.refresh(email_channel)
        assert (
            profile_before.contact_name_ciphertext,
            profile_before.title_ciphertext,
            profile_before.lawful_basis,
            profile_before.source_ref,
            profile_before.retention_until,
        ) == profile_snapshot
        assert (
            email_channel.value_ciphertext,
            email_channel.lawful_basis,
            email_channel.source_ref,
            email_channel.consent_ref,
            email_channel.retention_until,
        ) == channel_snapshot

        channel_injection = await c.post(
            f'/api/v1/growth/open/forms/{e2e.publish_ref}/submit',
            json={
                'email': 'zhaoliu@beta.com',
                'wechat': 'attacker_wechat',
                'privacy_notice_version': 'growth-form-v1',
                'consent_purpose': 'sales_contact',
                'consent': True,
            },
            headers=form_headers(),
        )
        assert channel_injection.status_code == 409
        injected_channel = (
            await e2e.session.execute(
                select(ContactChannel.id).where(
                    ContactChannel.lead_contact_id == submission.lead_contact_id,
                    ContactChannel.channel == 'wechat',
                )
            )
        ).scalar_one_or_none()
        assert injected_channel is None

        # --- Open: 蜜罐字段 → spam 不进漏斗，也不保留提交的 PII ---
        spam_idempotency_key = str(uuid.uuid4())
        spam = _ok(
            await c.post(
                f'/api/v1/growth/open/forms/{e2e.publish_ref}/submit',
                json={
                    'email': 'bot@x.com',
                    'website_url': 'http://spam',
                    'privacy_notice_version': 'growth-form-v1',
                    'consent_purpose': 'sales_contact',
                    'consent': True,
                },
                headers=form_headers(key=spam_idempotency_key),
            )
        )
        assert spam['status'] == 'received'
        spam_submission = (
            await e2e.session.execute(
                select(FormSubmission).where(FormSubmission.idempotency_key == spam_idempotency_key)
            )
        ).scalar_one()
        assert spam_submission is not None
        assert spam_submission.email is None and spam_submission.phone is None and spam_submission.name is None
        assert 'bot@x.com' not in str(spam_submission.payload)
    finally:
        (
            settings.GROWTH_PUBLISH_LANDING_ENABLED,
            settings.GROWTH_PII_NEW_WRITE_ENABLED,
            settings.GROWTH_FORM_PRIVACY_NOTICE_VERSION,
        ) = previous_form_flags

    # --- 跨户隔离：他 owner 的 agent 看不到本户客户 ---
    e2e.state.owner_uid = e2e.other_uid
    miss = await c.get(f'{agent_api}/customers/{cid}')
    assert miss.status_code == 404, miss.text


async def test_open_form_security_cleaning_and_rate_limit(e2e: SimpleNamespace) -> None:
    """公开表单拒绝伪造项目/无令牌/超长输入，清洗 HTML，并返回带 Retry-After 的 429。"""
    previous = (
        settings.GROWTH_PUBLISH_LANDING_ENABLED,
        settings.GROWTH_PII_NEW_WRITE_ENABLED,
        settings.GROWTH_FORM_PRIVACY_NOTICE_VERSION,
        settings.GROWTH_FORM_RATE_IP_MAX,
        settings.GROWTH_FORM_RATE_IDENTITY_MAX,
    )
    settings.GROWTH_PUBLISH_LANDING_ENABLED = True
    settings.GROWTH_PII_NEW_WRITE_ENABLED = True
    settings.GROWTH_FORM_PRIVACY_NOTICE_VERSION = 'growth-form-v1'
    try:
        token = _ok(
            await e2e.client.post(
                f'/api/v1/publish/open/sites/{e2e.publish_ref}/forms/growth-lead-v1/access-token',
                json={},
            )
        )['form_access_token']
        base_payload = {
            'company_name': '<b>Gamma</b>\u0000',
            'contact_name': '<script>甲</script>',
            'email': 'security@example.com',
            'privacy_notice_version': 'growth-form-v1',
            'consent_purpose': 'sales_contact',
            'consent': True,
        }
        missing_token = await e2e.client.post(
            f'/api/v1/growth/open/forms/{e2e.publish_ref}/submit',
            json=base_payload,
            headers={'Idempotency-Key': str(uuid.uuid4())},
        )
        assert missing_token.status_code == 422
        invalid_token = await e2e.client.post(
            f'/api/v1/growth/open/forms/{e2e.publish_ref}/submit',
            json=base_payload,
            headers={
                'Idempotency-Key': str(uuid.uuid4()),
                'X-Publish-Form-Token': 'invalid-token',
            },
        )
        assert invalid_token.status_code == 403
        forged_project = await e2e.client.post(
            f'/api/v1/growth/open/forms/{e2e.publish_ref}/submit',
            json={**base_payload, 'platform_project_id': str(uuid.uuid4())},
            headers={
                'Idempotency-Key': str(uuid.uuid4()),
                'X-Publish-Form-Token': token,
            },
        )
        assert forged_project.status_code == 422
        too_long = await e2e.client.post(
            f'/api/v1/growth/open/forms/{e2e.publish_ref}/submit',
            json={**base_payload, 'message': 'x' * 2001},
            headers={
                'Idempotency-Key': str(uuid.uuid4()),
                'X-Publish-Form-Token': token,
            },
        )
        assert too_long.status_code == 422

        clean_key = str(uuid.uuid4())
        cleaned = _ok(
            await e2e.client.post(
                f'/api/v1/growth/open/forms/{e2e.publish_ref}/submit',
                json=base_payload,
                headers={
                    'Idempotency-Key': clean_key,
                    'X-Publish-Form-Token': token,
                },
            )
        )
        assert cleaned['status'] == 'received'
        cleaned_submission = (
            await e2e.session.execute(select(FormSubmission).where(FormSubmission.idempotency_key == clean_key))
        ).scalar_one()
        assert cleaned_submission.company == 'Gamma'
        assert '<script>' not in str(cleaned_submission)

        settings.GROWTH_FORM_RATE_IP_MAX = 1
        settings.GROWTH_FORM_RATE_IDENTITY_MAX = 100
        shared_ip = '203.0.113.200'
        first = await e2e.client.post(
            f'/api/v1/growth/open/forms/{e2e.publish_ref}/submit',
            json={**base_payload, 'email': 'rate-one@example.com'},
            headers={
                'Idempotency-Key': str(uuid.uuid4()),
                'X-Publish-Form-Token': token,
                'x-real-ip': shared_ip,
            },
        )
        assert first.status_code == 200
        limited = await e2e.client.post(
            f'/api/v1/growth/open/forms/{e2e.publish_ref}/submit',
            json={**base_payload, 'email': 'rate-two@example.com'},
            headers={
                'Idempotency-Key': str(uuid.uuid4()),
                'X-Publish-Form-Token': token,
                'x-real-ip': shared_ip,
            },
        )
        assert limited.status_code == 429
        assert int(limited.headers['Retry-After']) >= 1
    finally:
        (
            settings.GROWTH_PUBLISH_LANDING_ENABLED,
            settings.GROWTH_PII_NEW_WRITE_ENABLED,
            settings.GROWTH_FORM_PRIVACY_NOTICE_VERSION,
            settings.GROWTH_FORM_RATE_IP_MAX,
            settings.GROWTH_FORM_RATE_IDENTITY_MAX,
        ) = previous


async def test_growth_landing_status_and_reconcile_use_publish_provider(
    e2e: SimpleNamespace,
) -> None:
    """Owner 状态面通过真实 Publish 内部 HTTP 找站并显式对账，不直接信任客户端站点 ID。"""
    previous_enabled = settings.GROWTH_PUBLISH_LANDING_ENABLED
    settings.GROWTH_PUBLISH_LANDING_ENABLED = True
    try:
        growth = await e2e.session.get(GrowthProject, e2e.growth_project_id)
        assert growth is not None
        growth.landing_site_ref = None
        await e2e.session.flush()

        before = _ok(await e2e.client.get(f'/api/v1/growth/app/projects/{e2e.growth_project_id}/landing'))
        assert before['dependency']['status'] == 'ready'
        assert before['site_state'] == 'published'
        assert before['site']['platform_project_id'] == str(e2e.platform_project_id)
        assert before['site']['form_ref'] == 'growth-lead-v1'
        assert before['attribution_summary'] == {
            'first_touch_count': 0,
            'last_touch_count': 0,
            'latest_touch_at': None,
        }
        assert before['binding'] == {'resource_uri': None, 'in_sync': False}

        reconciled = _ok(
            await e2e.client.post(
                f'/api/v1/growth/app/projects/{e2e.growth_project_id}/landing/reconcile',
                json={},
            )
        )
        assert reconciled['binding']['in_sync'] is True
        assert reconciled['binding']['resource_uri'].startswith('hasn://publish/sites/')
        await e2e.session.refresh(growth)
        assert growth.landing_site_ref == reconciled['binding']['resource_uri']

        settings.GROWTH_PUBLISH_LANDING_ENABLED = False
        disabled = _ok(await e2e.client.get(f'/api/v1/growth/app/projects/{e2e.growth_project_id}/landing'))
        assert disabled['dependency']['status'] == 'disabled'
        assert disabled['dependency']['error_code'] == 'GROWTH_PUBLISH_LANDING_DISABLED'
    finally:
        settings.GROWTH_PUBLISH_LANDING_ENABLED = previous_enabled


async def test_agent_collect_and_outreach_status(e2e: SimpleNamespace) -> None:
    """M4 接缝：collect.start/status（采集子域包装）+ outreach.status（按客户查触达）。"""
    c = e2e.client
    agent_api = '/api/v1/growth/agent'

    # --- collect.start：发起采集 → 恒落主人私有池 ---
    job = _ok(await c.post(f'{agent_api}/collect', json={'keyword': 'SaaS 获客', 'max_pages': 3}))
    assert job['status'] == 'pending' and job['user_id'] == e2e.owner_uid
    job_id = job['id']

    # --- collect.status：查同一任务 ---
    status = _ok(await c.get(f'{agent_api}/collect/{job_id}'))
    assert status['id'] == job_id and status['keyword'] == 'SaaS 获客'

    # --- outreach.status：qualify→send 后按客户查到该触达 ---
    cust = _ok(
        await c.post(
            f'{agent_api}/leads/{e2e.lead_id}/qualify',
            json={'qualify_reason': '高意向'},
        )
    )
    cid = cust['id']
    sent = _ok(
        await c.post(
            f'{agent_api}/outreach',
            json={'customer_id': cid, 'channel': 'manual_assist', 'content': '您好', 'intent_note': '破冰'},
        )
    )
    msgs = _ok(await c.get(f'{agent_api}/outreach', params={'customer_id': cid}))
    assert any(m['id'] == sent['id'] and m['status'] == 'pending_approval' for m in msgs)

    # --- 跨户：他 owner 查本户采集任务 → 403 Forbidden ---
    e2e.state.owner_uid = e2e.other_uid
    miss = await c.get(f'{agent_api}/collect/{job_id}')
    assert miss.status_code == 403, miss.text


async def test_owner_create_lead_via_http(e2e: SimpleNamespace) -> None:
    """M-UI：主人在 UI 手动建线索（AI-native 宗旨：UI 给人操作）。

    owner 私有池、source_type=manual、status=new（用户级状态来自 lead_ref），响应默认脱敏；
    建后出现在线索池检索；公司名与联系人名都空 → 400（线索无意义）。
    """
    c = e2e.client
    owner_api = '/api/v1/growth/app'

    # --- 建线索：公司名 + 联系方式 ---
    created = _ok(
        await c.post(
            f'{owner_api}/leads',
            json={
                'company_name': '星尘科技',
                'contact_name': '李雷',
                'email': 'lilei@xingchen.com',
                'phone': '13900139000',
                'industry': 'SaaS',
                'city': '深圳',
                'note': '展会认识',
            },
        )
    )
    assert created['lead_contact_id'] and created['source_type'] == 'manual'
    assert created['status'] == 'new'  # 用户级状态来自 lead_ref（新建即 new）
    assert created['company_name'] == '星尘科技'
    assert created['email'] == 'l***@xingchen.com'
    assert created['phone'] == '+861****9000'
    public_contact = await e2e.session.get(LeadContact, created['lead_contact_id'])
    assert public_contact is not None
    assert public_contact.contact_name is None
    assert public_contact.email is None and public_contact.email_normalized is None
    assert public_contact.phone is None and public_contact.phone_normalized is None
    private_profile = (
        await e2e.session.execute(
            select(ContactPrivateProfile).where(
                ContactPrivateProfile.user_id == e2e.owner_uid,
                ContactPrivateProfile.lead_contact_id == created['lead_contact_id'],
            )
        )
    ).scalar_one()
    private_channels = (
        (
            await e2e.session.execute(
                select(ContactChannel).where(
                    ContactChannel.private_profile_id == private_profile.id,
                )
            )
        )
        .scalars()
        .all()
    )
    assert {channel.channel for channel in private_channels} == {'email', 'phone'}
    assert all(channel.value_ciphertext not in {'lilei@xingchen.com', '13900139000'} for channel in private_channels)

    # --- 建的线索出现在 owner 线索池检索 ---
    leads = _ok(await c.get(f'{owner_api}/leads', params={'q': '星尘'}))
    assert any(item['lead_contact_id'] == created['lead_contact_id'] for item in leads)

    # --- 校验：公司名与联系人名都空 → 400 ---
    bad = await c.post(f'{owner_api}/leads', json={'email': 'noname@x.com'})
    assert bad.status_code == 400, bad.text


async def test_owner_request_leads_via_http(e2e: SimpleNamespace) -> None:
    """主人「请求线索」轻入口只查公共池并交付脱敏摘要，不再暗启旧爬虫补缺。

    找新线索改由获客分身通过读穿工具完成；本端点保留池内快速领取语义，
    因此池中不足 N 时只交付 M 条，backfill_job_id 恒为空。
    """
    c = e2e.client
    owner_api = '/api/v1/growth/app'

    # 公共池播种一条唯一关键词线索（query_pool 不限 lead_scope = 公共池语义）。
    tag = uuid.uuid4().hex[:8]
    uniq = f'唯一查询词{tag}'
    e2e.session.add(
        LeadContact(
            lead_no=f'LP{tag.upper()}',
            pool_visibility='public',
            company_name=uniq,
            contact_name='池主',
            email='pool@uniq.com',
            phone='13700137000',
            industry='SaaS',
            city='北京',
            source_type='firecrawl',
            status='valid',
            confidence_score=Decimal(88),
        )
    )
    await e2e.session.flush()

    # --- 请求 1 条 → 命中即交付脱敏摘要，无缺口不补爬 ---
    one = _ok(await c.post(f'{owner_api}/leads/request', json={'keyword': uniq, 'limit': 1}))
    assert one['delivered'] == 1 and one['from_pool'] == 1
    assert one['backfill_job_id'] is None
    assert one['leads'][0]['email'] == 'p***@uniq.com'
    assert one['leads'][0]['phone'] == '1370****7000'

    # --- 请求 5 条但池中仅 1 条命中 → 只交付 1 条，不暗启旧爬虫 ---
    gap = _ok(await c.post(f'{owner_api}/leads/request', json={'keyword': uniq, 'limit': 5}))
    assert gap['delivered'] == 1 and gap['requested'] == 5
    assert gap['backfill_job_id'] is None


async def test_project_customer_owner_and_agent_reads_are_masked_and_reveal_is_audited(
    e2e: SimpleNamespace,
) -> None:
    """项目客户 API 默认脱敏；Owner 单渠道 reveal 不缓存且留下可追溯审计。"""
    scope = GrowthScope(user_id=e2e.owner_uid, owner_hasn_id=e2e.owner)
    imported = await project_lead_service.ingest_batch(
        e2e.session,
        growth_project_id=e2e.growth_project_id,
        batch_id=f's7-api-{uuid.uuid4()}',
        items=[
            {
                'client_ref': 's7-api-private',
                'company_name': '隐私边界科技',
                'website': 'https://privacy-boundary.example/about',
                'industry': '企业软件',
                'source_kind': 'controlled_import',
                'source_tool': 'integration_test',
                'source_ref': 'controlled://growth-s7/private',
                'match_score': 92,
                'scoring_version': 'profile-v2/rules-v1',
                'evidence_fresh_at': datetime.now(UTC).isoformat(),
                'private_contact': {
                    'contact_name': '赵敏',
                    'title': '销售负责人',
                    'lawful_basis': 'public_business_contact',
                    'source_ref': 'controlled://growth-s7/private/profile',
                    'retention_until': (datetime.now(UTC) + timedelta(days=30)).isoformat(),
                    'channels': [
                        {
                            'channel': 'email',
                            'value': 'zhaomin@privacy-boundary.example',
                            'lawful_basis': 'public_business_contact',
                            'source_ref': 'controlled://growth-s7/private/email',
                        }
                    ],
                },
            }
        ],
        scope=scope,
        actor_kind='owner',
        actor_id=e2e.owner,
    )
    qualified = await project_lead_service.qualify_project_lead(
        e2e.session,
        growth_project_id=e2e.growth_project_id,
        project_lead_id=imported['items'][0]['project_lead_id'],
        scope=scope,
        profile={'需求': '统一项目跟进'},
        intent_score=90,
        actor_kind='owner',
        actor_id=e2e.owner,
    )
    customer_id = qualified['id']
    owner_api = '/api/v1/growth/app'
    agent_api = '/api/v1/growth/agent'

    owner_page = _ok(
        await e2e.client.get(
            f'{owner_api}/projects/{e2e.growth_project_id}/customers',
        )
    )
    owner_detail = _ok(
        await e2e.client.get(
            f'{owner_api}/projects/{e2e.growth_project_id}/customers/{customer_id}/detail',
        )
    )
    agent_detail = _ok(
        await e2e.client.get(
            f'{agent_api}/projects/{e2e.growth_project_id}/customers/{customer_id}/detail',
        )
    )
    assert owner_page['total'] == 1
    assert owner_detail['customer']['email'] == 'z***@privacy-boundary.example'
    assert agent_detail['customer']['email'] == 'z***@privacy-boundary.example'
    assert owner_detail['followup_tasks'][0]['task_uuid'] == qualified['followup_task_id']
    assert 'zhaomin@privacy-boundary.example' not in str(owner_page)
    assert 'zhaomin@privacy-boundary.example' not in str(owner_detail)
    assert 'zhaomin@privacy-boundary.example' not in str(agent_detail)

    channel_id = owner_detail['customer']['channels'][0]['id']
    revealed_response = await e2e.client.post(
        f'{owner_api}/contacts/channels/{channel_id}/reveal',
        json={'purpose': 'contact_verification'},
    )
    revealed = _ok(revealed_response)
    assert revealed == {
        'channel': 'email',
        'value': 'zhaomin@privacy-boundary.example',
        'expires_in_seconds': 30,
    }
    assert revealed_response.headers['cache-control'] == 'no-store, max-age=0'
    audit = (
        (
            await e2e.session.execute(
                select(ContactPrivateAccessAudit)
                .where(
                    ContactPrivateAccessAudit.resource_id == str(channel_id),
                    ContactPrivateAccessAudit.action == 'reveal',
                )
                .order_by(ContactPrivateAccessAudit.id.desc())
                .limit(1)
            )
        )
        .scalars()
        .one()
    )
    assert audit.actor_type == 'owner'
    assert audit.actor_id == e2e.owner
    assert audit.purpose == 'contact_verification'
    assert audit.result == 'allowed'
    assert audit.trace_id
    assert 'zhaomin@privacy-boundary.example' not in str(audit.request_metadata)

    e2e.state.owner_uid = e2e.other_uid
    denied = await e2e.client.get(
        f'{agent_api}/projects/{e2e.growth_project_id}/customers/{customer_id}/detail',
    )
    assert denied.status_code == 404


async def test_project_outreach_draft_submit_versioned_approval_and_manual_attestation_http(
    e2e: SimpleNamespace,
) -> None:
    """S8 Agent/Owner HTTP 共用项目状态机，409 明确拒绝旧内容版本。"""
    customer = Customer(
        customer_no=f'C-S8-{uuid.uuid4().hex[:8]}',
        user_id=e2e.owner_uid,
        growth_project_id=e2e.growth_project_id,
        source_kind='controlled_import',
        company_name='S8 受控审批客户',
        contact_name='周女士',
        email='s8-http-private@example.com',
        lifecycle_status='active',
        owner_agent_id=e2e.agent_hasn,
        owner_scope='personal',
    )
    e2e.session.add(customer)
    await e2e.session.flush()
    agent_api = '/api/v1/growth/agent'
    owner_api = '/api/v1/growth/app'
    project_path = f'/projects/{e2e.growth_project_id}/outreach'

    draft_payload = {
        'customer_id': customer.id,
        'channel': 'manual_assist',
        'content': '您好，想沟通贵司客户增长目标',
        'intent_note': '首次触达，确认增长计划',
        'content_assets': {
            'attachments': ['hasn://asset/s8-http-controlled'],
        },
        'idempotency_key': 's8-http-draft-1',
    }
    drafted = _ok(
        await e2e.client.post(
            f'{agent_api}{project_path}/drafts',
            json=draft_payload,
        )
    )
    replayed = _ok(
        await e2e.client.post(
            f'{agent_api}{project_path}/drafts',
            json={**draft_payload, 'content': '重放不得覆盖'},
        )
    )
    assert drafted['id'] == replayed['id']
    assert drafted['approval_status'] == 'draft'

    submitted = _ok(
        await e2e.client.post(
            f'{agent_api}{project_path}/{drafted["id"]}/submit',
            json={
                'expected_content_version': 1,
                'idempotency_key': 's8-http-submit-1',
            },
        )
    )
    assert submitted['approval_status'] == 'pending_approval'
    pending = _ok(await e2e.client.get(f'{owner_api}{project_path}/pending'))
    card = next(item for item in pending if item['id'] == drafted['id'])
    assert card['target_customer']['email'] == 's***@example.com'
    assert 's8-http-private@example.com' not in str(card)
    assert [event['event_type'] for event in card['events']] == [
        'drafted',
        'approval_requested',
    ]
    missing_version = await e2e.client.post(
        f'{owner_api}{project_path}/{drafted["id"]}/approve',
        json={},
    )
    assert missing_version.status_code == 422

    edited = _ok(
        await e2e.client.patch(
            f'{owner_api}{project_path}/{drafted["id"]}',
            json={
                'expected_content_version': 1,
                'content': '您好，想请教贵司今年的客户增长目标',
                'content_assets': {
                    'attachments': ['hasn://asset/s8-http-controlled-v2'],
                },
            },
        )
    )
    assert edited['content_version'] == 2
    stale = await e2e.client.post(
        f'{owner_api}{project_path}/{drafted["id"]}/approve',
        json={'expected_content_version': 1},
    )
    assert stale.status_code == 409
    assert '内容已变化' in stale.json()['msg']

    approved = _ok(
        await e2e.client.post(
            f'{owner_api}{project_path}/{drafted["id"]}/approve',
            json={'expected_content_version': 2},
        )
    )
    assert approved['approval_status'] == 'approved'
    assert approved['approval_version'] == approved['content_version'] == 2
    material = _ok(
        await e2e.client.get(
            f'{owner_api}{project_path}/{drafted["id"]}/send-material',
            params={'expected_content_version': 2},
        )
    )
    assert material['content'] == '您好，想请教贵司今年的客户增长目标'
    assert material['content_assets']['attachments'] == ['hasn://asset/s8-http-controlled-v2']
    assert 's8-http-private@example.com' not in str(material)

    attested = _ok(
        await e2e.client.post(
            f'{owner_api}{project_path}/{drafted["id"]}/manual-attest',
            json={
                'expected_content_version': 2,
                'channel_actual': 'email',
                'idempotency_key': 's8-http-manual-proof-1',
                'proof': {
                    'method': 'owner_checkbox',
                    'note': '已在企业邮箱客户端人工发送',
                },
            },
        )
    )
    assert attested['manual_attested_at'] is not None
    assert attested['delivery_status'] == 'not_queued'
    assert attested['sent_at'] is None
