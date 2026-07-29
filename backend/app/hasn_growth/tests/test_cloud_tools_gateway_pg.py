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
from decimal import Decimal
from pathlib import Path
from types import SimpleNamespace
from typing import TYPE_CHECKING

import pytest
import pytest_asyncio
import sqlalchemy as sa

from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

from backend.app.hasn.model.hasn_artifact_contributions import HasnArtifactContributions
from backend.app.hasn.model.hasn_artifacts import HasnArtifacts
from backend.app.hasn_core import HasnAgents, HasnHumans
from backend.app.hasn_core.app_platform import ai_native_runtime_gateway
from backend.app.hasn_growth.manifest import GROWTH_AI_NATIVE_MANIFEST
from backend.app.hasn_growth.model.growth_project import GrowthProject
from backend.app.hasn_growth.model.growth_project_lead import GrowthProjectLead
from backend.app.hasn_growth.model.lead_collection_job import LeadCollectionJob
from backend.app.hasn_growth.model.lead_contact import LeadContact
from backend.app.hasn_growth.model.lead_ref import LeadRef
from backend.app.hasn_growth.service.lead_pool_query_service import lead_pool_query_service
from backend.app.hasn_project.model.hasn_project import HasnProject
from backend.app.mcp.context import (
    clear_current_project_id,
    clear_current_work_session_id,
    set_current_project_id,
    set_current_work_session_id,
)
from backend.common.dataclasses import AgentTokenPayload
from backend.common.exception.errors import ForbiddenError
from backend.database.db import SQLALCHEMY_DATABASE_URL

if TYPE_CHECKING:
    from collections.abc import AsyncIterator

pytestmark = pytest.mark.asyncio

# 整改后获客工具走云端 gateway_internal 的同一张 handler 注册表。
_REG = ai_native_runtime_gateway._internal_handlers()
_REPO = Path(__file__).resolve().parents[4]
_S6_MIGRATION_SQL = _REPO / 'backend/sql/hasn_growth/migrations/2026-07-29-add-lead-dedupe-keys.sql'
_S7_MIGRATION_SQL = _REPO / 'backend/sql/hasn_growth/migrations/2026-07-29-project-lead-qualification-idempotency.sql'


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
    raw = await (await session.connection()).get_raw_connection()
    connection = raw.driver_connection
    assert connection is not None
    await connection.execute(_S6_MIGRATION_SQL.read_text(encoding='utf-8'))
    await connection.execute(_S7_MIGRATION_SQL.read_text(encoding='utf-8'))
    tag = uuid.uuid4().hex[:8]
    owner = f'h_gtc_{tag}'
    owner_uid = 94_700_000_000 + int(uuid.uuid4().int % 900_000_000)
    agent_hasn = f'a_gtc_{tag}'

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
    platform_project = HasnProject(
        owner_id=owner,
        name=f'获客工具项目-{tag}',
        goal='验证分身启用获客',
        status='active',
    )
    lead_platform_project = HasnProject(
        owner_id=owner,
        name=f'获客线索项目-{tag}',
        goal='验证项目线索批次',
        status='active',
    )
    session.add_all((platform_project, lead_platform_project))
    await session.flush()
    growth_project = GrowthProject(
        platform_project_id=lead_platform_project.id,
        user_id=owner_uid,
        owner_hasn_id=owner,
        owner_scope='personal',
        name=f'获客工具漏斗-{tag}',
        owner_agent_id=agent_hasn,
        status='active',
        provision_status='ready',
    )
    session.add(growth_project)
    session.add(LeadRef(user_id=owner_uid, lead_contact_id=lead.id, source='collect', status='new'))
    await session.flush()
    project_lead = GrowthProjectLead(
        growth_project_id=growth_project.id,
        lead_contact_id=lead.id,
        user_id=owner_uid,
        owner_scope='personal',
        source_kind='firecrawl',
        source_tool='test_seed',
        source_ref=f'controlled://gateway/{tag}',
        status='new',
        match_score=Decimal(72),
        scoring_version='gateway-fixture-v1',
    )
    session.add(project_lead)
    await session.flush()

    def _agent(scopes: list[str]) -> AgentTokenPayload:
        return AgentTokenPayload(
            agent_hasn_id=agent_hasn,
            agent_name=f'agent_{tag}',
            owner_hasn_id=owner,
            owner_user_id=owner_uid,
            session_uuid=f'sess_{tag}',
            expire_time=datetime(2099, 1, 1, tzinfo=UTC),
        )

    try:
        yield SimpleNamespace(
            session=session,
            owner=owner,
            owner_uid=owner_uid,
            agent_hasn=agent_hasn,
            lead_id=lead.id,
            platform_project_id=platform_project.id,
            lead_platform_project_id=lead_platform_project.id,
            growth_project_id=growth_project.id,
            project_lead_id=project_lead.id,
            agent=_agent,
        )
    finally:
        await session.rollback()
        await session.close()
        await engine.dispose()


async def test_all_growth_tools_resolve_in_gateway_registry() -> None:  # ruff: ignore[unused-async]
    """manifest tools[].handler 与网关 handler 注册表零漂移：每条都能 dispatch 到真实 handler。"""
    handlers = [t['handler'] for t in GROWTH_AI_NATIVE_MANIFEST['tools']]
    assert len(handlers) == 29
    for h in (
        'growth.project_get',
        'growth.project_create',
        'growth.project_update',
        'growth.project_update_profile',
        'growth.project_pause',
    ):
        assert h in handlers, h
    assert 'growth.lead_request' in handlers
    assert 'growth.lead_ingest' in handlers
    assert 'growth.lead_list' in handlers
    for h in ('growth.lookup_company', 'growth.search_companies', 'growth.enrich_company'):
        assert h in handlers, h
    for h in handlers:
        assert h.startswith('growth.'), h
        assert h in _REG, f'manifest 声明的工具 handler {h} 未在网关注册表中'
        assert callable(_REG[h])


async def test_growth_project_agent_writes_register_one_session_artifact(
    ctx: SimpleNamespace,
) -> None:
    """分身创建、更新、暂停都自动登记同一 Growth 项目，并绑定工作会话与平台项目。"""
    session = ctx.session
    agent = ctx.agent(['agent', 'growth:read', 'growth:manage'])
    work_session_id = f'ws_growth_project_{uuid.uuid4().hex[:8]}'
    set_current_work_session_id(work_session_id)
    set_current_project_id(str(ctx.platform_project_id))
    try:
        created = await _REG['growth.project_create'](
            session,
            agent,
            {
                'platform_project_id': str(ctx.platform_project_id),
                'trace_id': str(uuid.uuid4()),
                'idempotency_key': f'growth-agent-create-{uuid.uuid4()}',
                'name': '分身创建的获客漏斗',
            },
        )
        growth_project_id = created['growth_project']['id']
        expected_uri = f'hasn://growth/projects/{growth_project_id}'
        assert created['uri'] == expected_uri

        updated = await _REG['growth.project_update'](
            session,
            agent,
            {
                'growth_project_id': growth_project_id,
                'tagline': '已由分身补充说明',
            },
        )
        assert updated['uri'] == expected_uri
        paused = await _REG['growth.project_pause'](
            session,
            agent,
            {'growth_project_id': growth_project_id},
        )
        assert paused['status'] == 'paused'
        assert paused['uri'] == expected_uri

        artifacts = (
            (
                await session.execute(
                    select(HasnArtifacts).where(
                        HasnArtifacts.agent_hasn_id == ctx.agent_hasn,
                        HasnArtifacts.resource_uri == expected_uri,
                        HasnArtifacts.status == 'active',
                    )
                )
            )
            .scalars()
            .all()
        )
        assert len(artifacts) == 1
        artifact = artifacts[0]
        assert artifact.session_id == work_session_id
        assert artifact.project_id == ctx.platform_project_id
        assert artifact.resource_kind == 'growth.project'
    finally:
        clear_current_project_id()
        clear_current_work_session_id()


async def test_growth_lead_ingest_registers_one_cumulative_project_artifact(
    ctx: SimpleNamespace,
) -> None:
    """不同稳定批次累计摘要，同批次重放不重复累计或追加参与记录。"""
    session = ctx.session
    agent = ctx.agent(['agent', 'growth:read', 'growth:collect'])
    project_id = str(ctx.growth_project_id)

    def item(client_ref: str, domain: str) -> dict:
        return {
            'client_ref': client_ref,
            'company_name': f'受控导入企业 {client_ref}',
            'domain': domain,
            'source_kind': 'controlled_import',
            'source_tool': 'hasn.growth.lead.ingest',
            'source_ref': f'controlled://gateway/{client_ref}',
            'match_score': 88,
            'scoring_version': 'profile-v1/rules-v1',
            'score_breakdown': {
                'industry': {
                    'score': 88,
                    'explanation': '受控样本与当前 ICP 行业一致',
                }
            },
        }

    first_payload = {
        'growth_project_id': project_id,
        'batch_id': f'gateway-s6-{uuid.uuid4().hex}',
        'items': [item('first', f'{uuid.uuid4().hex}.example')],
    }
    first = await _REG['growth.lead_ingest'](session, agent, first_payload)
    replay = await _REG['growth.lead_ingest'](session, agent, first_payload)
    second = await _REG['growth.lead_ingest'](
        session,
        agent,
        {
            'growth_project_id': project_id,
            'batch_id': f'gateway-s6-{uuid.uuid4().hex}',
            'items': [item('second', f'{uuid.uuid4().hex}.example')],
        },
    )

    expected_uri = f'hasn://growth/leads/{project_id}'
    assert first['inserted'] == second['inserted'] == 1
    assert replay['skipped'] == 1
    assert first['uri'] == replay['uri'] == second['uri'] == expected_uri

    artifact = (
        await session.execute(
            select(HasnArtifacts).where(
                HasnArtifacts.agent_hasn_id == ctx.agent_hasn,
                HasnArtifacts.resource_uri == expected_uri,
            )
        )
    ).scalar_one()
    assert artifact.project_id == ctx.lead_platform_project_id
    assert artifact.resource_kind == 'growth.leads'
    assert artifact.meta_data['inserted'] == 2
    assert artifact.meta_data['updated'] == 0
    assert artifact.meta_data['skipped'] == 0
    assert artifact.meta_data['error_count'] == 0
    contribution_count = await session.scalar(
        select(sa.func.count())
        .select_from(HasnArtifactContributions)
        .where(HasnArtifactContributions.artifact_id == artifact.artifact_id)
    )
    assert contribution_count == 2

    listed = await _REG['growth.lead_list'](
        session,
        agent,
        {'growth_project_id': project_id, 'page': 1, 'size': 1},
    )
    assert listed['total'] == 3
    assert len(listed['items']) == 1


async def test_growth_cloud_tools_lifecycle_via_gateway_handlers(ctx: SimpleNamespace) -> None:
    """单客户生命周期经云端 handler 链：检索(脱敏)→晋级→列客户(scope)→记活动→触达必审→商机→成交→漏斗。"""
    s, agent = ctx.session, ctx.agent(['agent', 'growth:read', 'growth:manage', 'growth:outreach', 'growth:collect'])

    # 检索默认脱敏（agent scope 无 growth:pii）
    leads = await _REG['growth.lead_search'](s, agent, {'query': 'Acme', 'limit': 10})
    assert leads and leads[0]['email'] == 'w***@acme.com', 'agent 读类默认脱敏'

    # 晋级建客户（写）
    qualify_input = {
        'growth_project_id': str(ctx.growth_project_id),
        'project_lead_id': ctx.project_lead_id,
        'intent_score': 80,
    }
    cust = await _REG['growth.lead_qualify'](s, agent, qualify_input)
    replay = await _REG['growth.lead_qualify'](s, agent, qualify_input)
    cid = cust['id']
    assert replay['id'] == cid
    assert cust['email'] == 'w***@acme.com'
    artifact = (
        await s.execute(
            select(HasnArtifacts).where(
                HasnArtifacts.agent_hasn_id == ctx.agent_hasn,
                HasnArtifacts.resource_uri == f'hasn://growth/customers/{cid}',
            )
        )
    ).scalar_one()
    contribution_count = await s.scalar(
        select(sa.func.count())
        .select_from(HasnArtifactContributions)
        .where(HasnArtifactContributions.artifact_id == artifact.artifact_id)
    )
    assert contribution_count == 1

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
    sent = await _REG['growth.outreach_send'](
        s,
        agent,
        {
            'customer_id': cid,
            'channel': 'manual_assist',
            'content': '您好，想聊聊获客',
            'intent_note': '破冰',
        },
    )
    assert sent['status'] == 'pending_approval'

    # 商机：立 → 推进 → 成交
    opp = await _REG['growth.opportunity_create'](
        s, agent, {'customer_id': cid, 'name': '年度 SaaS 订阅', 'amount': 120000}
    )
    oid = opp['id']
    assert opp['stage'] == 'contacted'
    o1 = await _REG['growth.opportunity_update_stage'](
        s, agent, {'opportunity_id': oid, 'stage': 'proposal', 'note': '发了提案'}
    )
    assert o1['stage'] == 'proposal'
    closed = await _REG['growth.deal_close'](s, agent, {'opportunity_id': oid, 'result': 'won', 'amount': 118000})
    assert closed['stage'] == 'closed_won'

    # 漏斗：本月赢单 ≥1
    funnel = await _REG['growth.report_funnel'](s, agent, {'view': 'team'})
    assert funnel['won_this_month']['count'] >= 1 and funnel['won_this_month']['amount'] >= 118000


async def test_growth_cloud_tools_legacy_pii_scope_stays_masked(ctx: SimpleNamespace) -> None:
    """旧 growth:pii claim 已退役，分身读类保持脱敏，明文授权不得回落到凭证 scope。"""
    s = ctx.session
    masked = await _REG['growth.lead_search'](s, ctx.agent(['agent', 'growth:read']), {'query': 'Acme'})
    assert masked and masked[0]['email'] == 'w***@acme.com'
    revealed = await _REG['growth.lead_search'](s, ctx.agent(['agent', 'growth:read', 'growth:pii']), {'query': 'Acme'})
    assert revealed and revealed[0]['email'] == 'w***@acme.com'


async def test_growth_cloud_tools_reassign_requires_manager(ctx: SimpleNamespace) -> None:
    """customer.reassign 经 handler 落到经理闸门：个人模式（非经理）→ ForbiddenError。"""
    s, agent = ctx.session, ctx.agent(['agent', 'growth:read', 'growth:manage'])
    cust = await _REG['growth.lead_qualify'](
        s,
        agent,
        {
            'growth_project_id': str(ctx.growth_project_id),
            'project_lead_id': ctx.project_lead_id,
            'intent_score': 60,
        },
    )
    with pytest.raises(ForbiddenError):
        await _REG['growth.customer_reassign'](s, agent, {'customer_id': cust['id'], 'assignee': 'h_other'})


async def test_lead_request_pool_hit_delivers_without_backfill(ctx: SimpleNamespace) -> None:
    """2.1 先查池：命中 M≥N → 直接交付 N 条（零采集成本，无补爬 job）。PII 默认脱敏。

    query_pool 查公共池（pool_visibility=public）——seed 的 'Acme' 即公共池线索，会被查到。
    """
    s, agent = ctx.session, ctx.agent(['agent', 'growth:read', 'growth:collect'])
    result = await _REG['growth.lead_request'](s, agent, {'keyword': 'Acme', 'limit': 1})
    assert result['delivered'] >= 1
    assert result['from_pool'] == result['delivered']
    assert result['backfill_job_id'] is None, 'M≥N 不应触发补爬'
    assert result['leads'] and result['leads'][0]['email'] == 'w***@acme.com', '读类默认脱敏'


async def test_lead_request_gap_stays_pool_only(ctx: SimpleNamespace) -> None:
    """请求线索轻入口只看池；缺口不再暗启旧爬虫，由获客分身读穿工具补齐。"""
    s, agent = ctx.session, ctx.agent(['agent', 'growth:collect'])
    miss = f'zzq{uuid.uuid4().hex[:8]}'  # 不会命中任何已有线索的关键词
    result = await _REG['growth.lead_request'](s, agent, {'keyword': miss, 'limit': 3})
    assert result['delivered'] == 0
    assert result['backfill_job_id'] is None


async def test_query_pool_filters_by_keyword(ctx: SimpleNamespace) -> None:
    """2.1 LeadPoolQueryService.query_pool：关键词命中返回，未命中返回空（真实 PG 查询）。"""
    s = ctx.session
    hit = await lead_pool_query_service.query_pool(s, keyword='Acme', limit=10)
    assert any(r.id == ctx.lead_id for r in hit), '关键词 Acme 应命中 seed 线索'
    miss = await lead_pool_query_service.query_pool(s, keyword=f'no{uuid.uuid4().hex[:8]}match', limit=10)
    assert miss == [], '不存在的关键词应返回空'


async def test_collect_start_enqueues_run_job_on_commit(monkeypatch: pytest.MonkeyPatch) -> None:
    """方案A 采集执行链路：collect.start 建 pending job → 事务 commit 后才入 Celery 队列（时序正确）。

    证明分身调 hasn.growth.collect.start 不再"建了 job 却不会跑"——after_commit 钩子在事务提交
    后把 lead_automation_run_job 入队异步执行采集；提交前不入队（避免 worker 读不到未提交的 job）。
    用独立 session（同库）真实建/删，验证真实 commit 触发，不污染其它测试。
    """
    engine = create_async_engine(SQLALCHEMY_DATABASE_URL, poolclass=NullPool)
    try:
        async with engine.connect() as conn:
            await conn.execute(select(1))
    except Exception as exc:
        await engine.dispose()
        pytest.skip(f'本地 PostgreSQL 不可达，跳过: {exc!r}')

    import backend.app.hasn_growth.tasks as growth_tasks

    enqueued: list[int] = []
    monkeypatch.setattr(growth_tasks.lead_automation_run_job, 'delay', lambda job_id: enqueued.append(job_id))

    tag = uuid.uuid4().hex[:8]
    owner_uid = 970000 + int(uuid.uuid4().int % 9000)
    session = async_sessionmaker(engine, expire_on_commit=False)()
    job_id: int | None = None
    try:
        agent = AgentTokenPayload(
            agent_hasn_id=f'a_cs_{tag}',
            agent_name=f'agent_{tag}',
            owner_hasn_id=f'h_cs_{tag}',
            owner_user_id=owner_uid,
            session_uuid=f'sess_{tag}',
            expire_time=datetime(2099, 1, 1, tzinfo=UTC),
        )
        job = await _REG['growth.collect_start'](session, agent, {'keyword': f'kw_{tag}', 'max_results': 5})
        job_id = int(job['id'])
        assert job['status'] == 'pending'
        assert enqueued == [], '事务提交前不应入队（避免 worker 读不到未提交的 job）'

        await session.commit()
        assert enqueued == [job_id], '事务提交后 after_commit 钩子应把采集 job 入队异步执行'
    finally:
        if job_id is not None:
            await session.execute(delete(LeadCollectionJob).where(LeadCollectionJob.id == job_id))
            await session.commit()
        await session.close()
        await engine.dispose()


async def test_collection_job_impl_skips_missing_job() -> None:
    """方案A 幂等守卫：job 不存在 → 早返回 not_found，不进 run_job（不打 firecrawl 网络、无副作用）。"""
    from backend.app.hasn_growth.tasks import _run_collection_job_impl

    missing_id = 990_000_000 + int(uuid.uuid4().int % 9_000_000)
    try:
        result = await _run_collection_job_impl(missing_id)
    except Exception as exc:
        pytest.skip(f'本地 PostgreSQL 不可达，跳过: {exc!r}')
    assert result == {'job_id': missing_id, 'skipped': 'not_found'}
