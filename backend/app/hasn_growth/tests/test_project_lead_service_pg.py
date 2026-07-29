"""S6 项目线索批次入池、主体隔离与分页状态的真实 PostgreSQL 测试。"""

from __future__ import annotations

import uuid

from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace
from typing import TYPE_CHECKING

import pytest
import pytest_asyncio
import sqlalchemy as sa

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

from backend.app.hasn.model.hasn_enterprise_membership import HasnEnterpriseMembership
from backend.app.hasn_core import HasnHumans
from backend.app.hasn_growth.model.activity import Activity
from backend.app.hasn_growth.model.contact_channel import ContactChannel
from backend.app.hasn_growth.model.contact_private_profile import ContactPrivateProfile
from backend.app.hasn_growth.model.customer import Customer
from backend.app.hasn_growth.model.growth_attribution_event import (
    GrowthAttributionEvent,
)
from backend.app.hasn_growth.model.growth_project import GrowthProject
from backend.app.hasn_growth.model.growth_project_lead import GrowthProjectLead
from backend.app.hasn_growth.model.lead_contact import LeadContact
from backend.app.hasn_growth.model.opportunity import Opportunity
from backend.app.hasn_growth.model.outreach_message import OutreachMessage
from backend.app.hasn_growth.service.project_customer_service import (
    project_customer_service,
)
from backend.app.hasn_growth.service.project_lead_service import project_lead_service
from backend.app.hasn_growth.service.scope_context import GrowthScope
from backend.app.hasn_project.model.hasn_project import HasnProject
from backend.app.hasn_task.model.task import HasnTask
from backend.common.exception import errors
from backend.database.db import SQLALCHEMY_DATABASE_URL

if TYPE_CHECKING:
    from collections.abc import AsyncIterator

pytestmark = pytest.mark.asyncio

_REPO = Path(__file__).resolve().parents[4]
_SCHEMA_SQL = _REPO / 'backend/sql/hasn_growth/007_create_growth_project_v4_tables.sql'
_KEY_STATE_SQL = _REPO / 'backend/sql/hasn_growth/008_create_growth_pii_key_state.sql'
_S6_MIGRATION_SQL = _REPO / 'backend/sql/hasn_growth/migrations/2026-07-29-add-lead-dedupe-keys.sql'
_S7_MIGRATION_SQL = _REPO / 'backend/sql/hasn_growth/migrations/2026-07-29-project-lead-qualification-idempotency.sql'


async def _apply_sql(session: AsyncSession) -> None:
    raw = await (await session.connection()).get_raw_connection()
    connection = raw.driver_connection
    assert connection is not None
    await connection.execute(_SCHEMA_SQL.read_text(encoding='utf-8'))
    await connection.execute(_KEY_STATE_SQL.read_text(encoding='utf-8'))
    await connection.execute(_S6_MIGRATION_SQL.read_text(encoding='utf-8'))
    await connection.execute(_S7_MIGRATION_SQL.read_text(encoding='utf-8'))


@pytest_asyncio.fixture
async def ctx() -> AsyncIterator[SimpleNamespace]:
    engine = create_async_engine(SQLALCHEMY_DATABASE_URL, poolclass=NullPool)
    try:
        async with engine.connect() as connection:
            await connection.execute(sa.select(1))
    except Exception as exc:
        await engine.dispose()
        pytest.skip(f'本地 PostgreSQL 不可达，跳过: {exc!r}')

    session = async_sessionmaker(engine, expire_on_commit=False)()
    await _apply_sql(session)
    tag = uuid.uuid4().hex[:10]
    first_user = 96_100_000_000 + int(uuid.uuid4().int % 300_000_000)
    second_user = first_user + 400_000_000
    first_owner = f'h_growth_lead_first_{tag}'
    second_owner = f'h_growth_lead_second_{tag}'
    first_platform = HasnProject(
        owner_id=first_owner,
        name=f'项目线索甲 {tag}',
        status='active',
    )
    second_platform = HasnProject(
        owner_id=second_owner,
        name=f'项目线索乙 {tag}',
        status='active',
    )
    session.add_all((
        HasnHumans(
            hasn_id=first_owner,
            star_id=f's_{first_user}',
            user_id=first_user,
            nickname=f'线索主人甲 {tag}',
            status='active',
        ),
        HasnHumans(
            hasn_id=second_owner,
            star_id=f's_{second_user}',
            user_id=second_user,
            nickname=f'线索主人乙 {tag}',
            status='active',
        ),
        first_platform,
        second_platform,
    ))
    await session.flush()
    first_growth = GrowthProject(
        platform_project_id=first_platform.id,
        user_id=first_user,
        owner_hasn_id=first_owner,
        owner_scope='personal',
        name=f'获客甲 {tag}',
        owner_agent_id=f'a_growth_first_{tag}',
        status='active',
        provision_status='ready',
    )
    second_growth = GrowthProject(
        platform_project_id=second_platform.id,
        user_id=second_user,
        owner_hasn_id=second_owner,
        owner_scope='personal',
        name=f'获客乙 {tag}',
        owner_agent_id=f'a_growth_second_{tag}',
        status='active',
        provision_status='ready',
    )
    session.add_all((first_growth, second_growth))
    await session.flush()
    try:
        yield SimpleNamespace(
            session=session,
            first_user=first_user,
            second_user=second_user,
            first_owner=first_owner,
            second_owner=second_owner,
            first_growth=first_growth,
            second_growth=second_growth,
        )
    finally:
        await session.rollback()
        await session.close()
        await engine.dispose()


def _item(
    *,
    client_ref: str,
    email: str | None = None,
    company_name: str = '星海科技',
    website: str = 'https://xinghai.example/about',
    score: float = 91,
) -> dict:
    item = {
        'client_ref': client_ref,
        'company_name': company_name,
        'website': website,
        'industry': '企业软件',
        'region': '广东',
        'city': '深圳',
        'source_kind': 'controlled_import',
        'source_tool': 'owner_import',
        'source_ref': f'controlled://growth-s6/{client_ref}',
        'source_meta': {'campaign': 's6-real-sample'},
        'match_score': score,
        'score_breakdown': {
            'industry': {
                'score': 45,
                'explanation': '行业与当前 ICP 一致',
            },
            'region': {
                'score': 20,
                'explanation': '目标销售区域一致',
            },
        },
        'scoring_version': 'profile-v2/rules-v1',
        'evidence_fresh_at': datetime.now(UTC).isoformat(),
    }
    if email is not None:
        item['private_contact'] = {
            'contact_name': f'{client_ref}负责人',
            'title': '销售负责人',
            'lawful_basis': 'public_business_contact',
            'source_ref': f'controlled://growth-s6/{client_ref}/pii',
            'retention_until': (datetime.now(UTC) + timedelta(days=90)).isoformat(),
            'channels': [
                {
                    'channel': 'email',
                    'value': email,
                    'lawful_basis': 'public_business_contact',
                    'source_ref': f'controlled://growth-s6/{client_ref}/email',
                }
            ],
        }
    return item


def _personal_scope(*, user_id: int, owner_hasn_id: str) -> GrowthScope:
    return GrowthScope(user_id=user_id, owner_hasn_id=owner_hasn_id)


async def test_ingest_reuses_public_fact_but_keeps_each_owner_private_pii(
    ctx: SimpleNamespace,
) -> None:
    first = await project_lead_service.ingest_batch(
        ctx.session,
        growth_project_id=ctx.first_growth.id,
        batch_id=f's6-first-{uuid.uuid4()}',
        items=[_item(client_ref='first', email='first.owner@xinghai.example')],
        scope=_personal_scope(
            user_id=ctx.first_user,
            owner_hasn_id=ctx.first_owner,
        ),
        actor_kind='owner',
        actor_id=ctx.first_owner,
    )
    second = await project_lead_service.ingest_batch(
        ctx.session,
        growth_project_id=ctx.second_growth.id,
        batch_id=f's6-second-{uuid.uuid4()}',
        items=[_item(client_ref='second', email='second.owner@xinghai.example')],
        scope=_personal_scope(
            user_id=ctx.second_user,
            owner_hasn_id=ctx.second_owner,
        ),
        actor_kind='owner',
        actor_id=ctx.second_owner,
    )

    assert first['inserted'] == second['inserted'] == 1
    assert first['items'][0]['lead_contact_id'] == second['items'][0]['lead_contact_id']
    contact_id = first['items'][0]['lead_contact_id']
    profiles = (
        (
            await ctx.session.execute(
                sa.select(ContactPrivateProfile).where(ContactPrivateProfile.lead_contact_id == contact_id)
            )
        )
        .scalars()
        .all()
    )
    assert {profile.user_id for profile in profiles} == {
        ctx.first_user,
        ctx.second_user,
    }
    channels = (
        (await ctx.session.execute(sa.select(ContactChannel).where(ContactChannel.lead_contact_id == contact_id)))
        .scalars()
        .all()
    )
    assert len(channels) == 2
    assert all('@xinghai.example' not in channel.value_ciphertext for channel in channels)

    first_page = await project_lead_service.list_project_leads(
        ctx.session,
        growth_project_id=ctx.first_growth.id,
        scope=_personal_scope(
            user_id=ctx.first_user,
            owner_hasn_id=ctx.first_owner,
        ),
        page=1,
        size=20,
    )
    second_page = await project_lead_service.list_project_leads(
        ctx.session,
        growth_project_id=ctx.second_growth.id,
        scope=_personal_scope(
            user_id=ctx.second_user,
            owner_hasn_id=ctx.second_owner,
        ),
        page=1,
        size=20,
    )
    assert first_page['items'][0]['contact_name'].startswith('f')
    assert set(first_page['items'][0]['contact_name'][1:]) == {'*'}
    assert (
        first_page['items'][0]['channels'][0]['masked_value']
        != (second_page['items'][0]['channels'][0]['masked_value'])
    )
    assert 'second.owner' not in str(first_page)
    assert 'first.owner' not in str(second_page)


async def test_stable_batch_retry_does_not_duplicate_project_lead(ctx: SimpleNamespace) -> None:
    batch_id = f's6-retry-{uuid.uuid4()}'
    stable_item = _item(client_ref='retry')
    scope = _personal_scope(
        user_id=ctx.first_user,
        owner_hasn_id=ctx.first_owner,
    )
    first = await project_lead_service.ingest_batch(
        ctx.session,
        growth_project_id=ctx.first_growth.id,
        batch_id=batch_id,
        items=[stable_item],
        scope=scope,
        actor_kind='agent',
        actor_id='a_growth_sales',
    )
    replay = await project_lead_service.ingest_batch(
        ctx.session,
        growth_project_id=ctx.first_growth.id,
        batch_id=batch_id,
        items=[stable_item],
        scope=scope,
        actor_kind='agent',
        actor_id='a_growth_sales',
    )

    assert first['inserted'] == 1
    assert replay['inserted'] == replay['updated'] == 0
    assert replay['skipped'] == 1
    assert replay['items'][0]['project_lead_id'] == first['items'][0]['project_lead_id']
    conflict = await project_lead_service.ingest_batch(
        ctx.session,
        growth_project_id=ctx.first_growth.id,
        batch_id=batch_id,
        items=[
            _item(
                client_ref='retry',
                company_name='被拒绝的批次改写',
                website='https://changed.example/',
            )
        ],
        scope=scope,
        actor_kind='agent',
        actor_id='a_growth_sales',
    )
    assert conflict['error_count'] == 1
    assert conflict['errors'][0]['code'] == 'LEAD_BATCH_ITEM_CONFLICT'
    count = await ctx.session.scalar(
        sa
        .select(sa.func.count())
        .select_from(GrowthProjectLead)
        .where(GrowthProjectLead.growth_project_id == ctx.first_growth.id)
    )
    assert count == 1
    attribution_count = await ctx.session.scalar(
        sa
        .select(sa.func.count())
        .select_from(GrowthAttributionEvent)
        .where(
            GrowthAttributionEvent.growth_project_id == ctx.first_growth.id,
            GrowthAttributionEvent.event_type == 'lead_acquired',
            GrowthAttributionEvent.meta_data['project_lead_id'].astext == str(first['items'][0]['project_lead_id']),
        )
    )
    assert attribution_count == 1
    changed_contact_count = await ctx.session.scalar(
        sa.select(sa.func.count()).select_from(LeadContact).where(LeadContact.domain == 'changed.example')
    )
    assert changed_contact_count == 0


async def test_batch_returns_deterministic_per_item_errors_and_keeps_valid_rows(
    ctx: SimpleNamespace,
) -> None:
    result = await project_lead_service.ingest_batch(
        ctx.session,
        growth_project_id=ctx.first_growth.id,
        batch_id=f's6-partial-{uuid.uuid4()}',
        items=[
            _item(client_ref='valid'),
            {
                'client_ref': 'invalid',
                'company_name': '',
                'source_kind': 'controlled_import',
                'source_ref': 'controlled://growth-s6/invalid',
                'match_score': 150,
            },
        ],
        scope=_personal_scope(
            user_id=ctx.first_user,
            owner_hasn_id=ctx.first_owner,
        ),
        actor_kind='owner',
        actor_id=ctx.first_owner,
    )

    assert result['inserted'] == 1
    assert result['error_count'] == 1
    assert result['errors'] == [
        {
            'index': 1,
            'client_ref': 'invalid',
            'code': 'LEAD_ITEM_INVALID',
            'message': '线索条目校验失败',
        }
    ]


async def test_list_is_server_paginated_and_dismiss_can_restore(ctx: SimpleNamespace) -> None:
    scope = _personal_scope(
        user_id=ctx.first_user,
        owner_hasn_id=ctx.first_owner,
    )
    result = await project_lead_service.ingest_batch(
        ctx.session,
        growth_project_id=ctx.first_growth.id,
        batch_id=f's6-page-{uuid.uuid4()}',
        items=[
            _item(
                client_ref=f'page-{index}',
                company_name=f'受控样本企业 {index:03d}',
                website=f'https://sample-{index}.example/',
                score=40 + index,
            )
            for index in range(25)
        ],
        scope=scope,
        actor_kind='owner',
        actor_id=ctx.first_owner,
    )
    assert result['inserted'] == 25
    first_page = await project_lead_service.list_project_leads(
        ctx.session,
        growth_project_id=ctx.first_growth.id,
        scope=scope,
        page=1,
        size=10,
    )
    third_page = await project_lead_service.list_project_leads(
        ctx.session,
        growth_project_id=ctx.first_growth.id,
        scope=scope,
        page=3,
        size=10,
    )
    assert first_page['total'] == 25
    assert len(first_page['items']) == 10
    assert len(third_page['items']) == 5
    assert first_page['items'][0]['match_score'] > third_page['items'][-1]['match_score']

    target = first_page['items'][0]
    dismissed = await project_lead_service.change_lead_status(
        ctx.session,
        growth_project_id=ctx.first_growth.id,
        project_lead_id=target['id'],
        action='dismiss',
        reason='本轮行业优先级不匹配',
        scope=scope,
    )
    assert dismissed['status'] == 'dismissed'
    assert dismissed['dismiss_reason'] == '本轮行业优先级不匹配'
    restored = await project_lead_service.change_lead_status(
        ctx.session,
        growth_project_id=ctx.first_growth.id,
        project_lead_id=target['id'],
        action='restore',
        reason=None,
        scope=scope,
    )
    assert restored['status'] == 'new'
    assert restored['dismiss_reason'] is None


async def test_enterprise_unassigned_inbound_is_manager_only_until_assignment(
    ctx: SimpleNamespace,
) -> None:
    enterprise_id = 97_000_000_000 + int(uuid.uuid4().int % 200_000_000)
    manager_scope = GrowthScope(
        user_id=ctx.first_user,
        owner_hasn_id=ctx.first_owner,
        enterprise_id=enterprise_id,
        viewer_role='manager',
    )
    member_scope = GrowthScope(
        user_id=ctx.second_user,
        owner_hasn_id=ctx.second_owner,
        enterprise_id=enterprise_id,
        viewer_role='member',
    )
    ctx.first_growth.owner_scope = 'enterprise'
    ctx.first_growth.enterprise_id = enterprise_id
    ctx.session.add_all((
        HasnEnterpriseMembership(
            enterprise_id=enterprise_id,
            user_id=ctx.first_user,
            role='owner',
            status='approved',
        ),
        HasnEnterpriseMembership(
            enterprise_id=enterprise_id,
            user_id=ctx.second_user,
            role='member',
            status='approved',
        ),
    ))
    await ctx.session.flush()
    ingested = await project_lead_service.ingest_batch(
        ctx.session,
        growth_project_id=ctx.first_growth.id,
        batch_id=f's6-inbound-{uuid.uuid4()}',
        items=[
            {
                **_item(client_ref='inbound'),
                'source_kind': 'inbound_form',
            }
        ],
        scope=manager_scope,
        actor_kind='system',
        actor_id='publish_form',
    )
    project_lead_id = ingested['items'][0]['project_lead_id']
    assert ingested['items'][0]['assignee'] is None

    manager_page = await project_lead_service.list_project_leads(
        ctx.session,
        growth_project_id=ctx.first_growth.id,
        scope=manager_scope,
        page=1,
        size=20,
    )
    member_page = await project_lead_service.list_project_leads(
        ctx.session,
        growth_project_id=ctx.first_growth.id,
        scope=member_scope,
        page=1,
        size=20,
    )
    assert manager_page['total'] == 1
    assert member_page['total'] == 0

    with pytest.raises(errors.ForbiddenError):
        await project_lead_service.assign_lead(
            ctx.session,
            growth_project_id=ctx.first_growth.id,
            project_lead_id=project_lead_id,
            assignee=ctx.second_owner,
            scope=member_scope,
        )
    assigned = await project_lead_service.assign_lead(
        ctx.session,
        growth_project_id=ctx.first_growth.id,
        project_lead_id=project_lead_id,
        assignee=ctx.second_owner,
        scope=manager_scope,
    )
    assert assigned['assignee'] == ctx.second_owner
    visible_after_assignment = await project_lead_service.list_project_leads(
        ctx.session,
        growth_project_id=ctx.first_growth.id,
        scope=member_scope,
        page=1,
        size=20,
    )
    assert visible_after_assignment['total'] == 1


async def test_qualify_is_one_idempotent_transaction_for_customer_task_activity_and_attribution(
    ctx: SimpleNamespace,
) -> None:
    scope = _personal_scope(
        user_id=ctx.first_user,
        owner_hasn_id=ctx.first_owner,
    )
    ingested = await project_lead_service.ingest_batch(
        ctx.session,
        growth_project_id=ctx.first_growth.id,
        batch_id=f's7-qualify-{uuid.uuid4()}',
        items=[_item(client_ref='qualify')],
        scope=scope,
        actor_kind='owner',
        actor_id=ctx.first_owner,
    )
    project_lead_id = ingested['items'][0]['project_lead_id']

    first = await project_lead_service.qualify_project_lead(
        ctx.session,
        growth_project_id=ctx.first_growth.id,
        project_lead_id=project_lead_id,
        scope=scope,
        profile={'pain_point': '获客效率需要提升'},
        intent_score=87,
        actor_kind='owner',
        actor_id=ctx.first_owner,
    )
    replay = await project_lead_service.qualify_project_lead(
        ctx.session,
        growth_project_id=ctx.first_growth.id,
        project_lead_id=project_lead_id,
        scope=scope,
        profile={'pain_point': '重试不得覆盖首次画像'},
        intent_score=99,
        actor_kind='owner',
        actor_id=ctx.first_owner,
    )

    assert first['id'] == replay['id']
    assert first['followup_task_id'] == replay['followup_task_id']
    assert first['lifecycle_status'] == 'active'
    assert first['intent_score'] == 87
    assert replay['intent_score'] == 87
    assert first['growth_project_id'] == str(ctx.first_growth.id)

    project_lead = await ctx.session.get(GrowthProjectLead, project_lead_id)
    assert project_lead is not None
    assert project_lead.status == 'qualified'
    customer_count = await ctx.session.scalar(
        sa
        .select(sa.func.count())
        .select_from(Customer)
        .where(
            Customer.growth_project_id == ctx.first_growth.id,
            Customer.lead_contact_id == project_lead.lead_contact_id,
        )
    )
    activity_count = await ctx.session.scalar(
        sa
        .select(sa.func.count())
        .select_from(Activity)
        .where(
            Activity.growth_project_id == ctx.first_growth.id,
            Activity.kind == 'qualify',
            Activity.ref_table == 'growth_project_lead',
            Activity.ref_id == str(project_lead_id),
        )
    )
    task_count = await ctx.session.scalar(
        sa.select(sa.func.count()).select_from(HasnTask).where(HasnTask.task_uuid == first['followup_task_id'])
    )
    attribution_count = await ctx.session.scalar(
        sa
        .select(sa.func.count())
        .select_from(GrowthAttributionEvent)
        .where(
            GrowthAttributionEvent.growth_project_id == ctx.first_growth.id,
            GrowthAttributionEvent.idempotency_key == f'qualify:{project_lead_id}',
        )
    )
    assert customer_count == activity_count == task_count == attribution_count == 1


async def test_project_customer_detail_is_scoped_masked_and_aggregated(
    ctx: SimpleNamespace,
) -> None:
    """客户详情只聚合当前项目事实，并保留可审计的单渠道 reveal 引用。"""
    scope = _personal_scope(
        user_id=ctx.first_user,
        owner_hasn_id=ctx.first_owner,
    )
    imported = await project_lead_service.ingest_batch(
        ctx.session,
        growth_project_id=ctx.first_growth.id,
        batch_id=f's7-detail-{uuid.uuid4()}',
        items=[
            _item(
                client_ref='s7-detail',
                email='private.owner@xinghai.example',
            )
        ],
        scope=scope,
        actor_kind='owner',
        actor_id=ctx.first_owner,
    )
    qualified = await project_lead_service.qualify_project_lead(
        ctx.session,
        growth_project_id=ctx.first_growth.id,
        project_lead_id=imported['items'][0]['project_lead_id'],
        scope=scope,
        profile={'痛点': '需要统一跟进'},
        intent_score=88,
        actor_kind='owner',
        actor_id=ctx.first_owner,
    )
    customer_id = qualified['id']

    current_opportunity = Opportunity(
        opportunity_no=f'OPP-{uuid.uuid4().hex[:8]}',
        customer_id=customer_id,
        user_id=ctx.first_user,
        growth_project_id=ctx.first_growth.id,
        name='当前项目商机',
        stage='contacted',
        currency='CNY',
        created_by_kind='owner',
    )
    current_outreach = OutreachMessage(
        customer_id=customer_id,
        user_id=ctx.first_user,
        growth_project_id=ctx.first_growth.id,
        direction='outbound',
        channel='email',
        content='当前项目触达',
        status='draft',
    )
    ctx.session.add_all((
        current_opportunity,
        current_outreach,
    ))
    await ctx.session.flush()

    page = await project_customer_service.list_customers(
        ctx.session,
        growth_project_id=ctx.first_growth.id,
        scope=scope,
        page=1,
        size=20,
    )
    assert page['total'] == 1
    assert page['items'][0]['id'] == customer_id
    assert 'private.owner@xinghai.example' not in str(page)

    detail = await project_customer_service.get_customer_detail(
        ctx.session,
        growth_project_id=ctx.first_growth.id,
        customer_id=customer_id,
        scope=scope,
    )
    assert detail['customer']['email'] == 'p***@xinghai.example'
    assert detail['customer']['channels'][0]['channel'] == 'email'
    assert isinstance(detail['customer']['channels'][0]['id'], int)
    assert detail['followup_tasks'][0]['task_uuid'] == qualified['followup_task_id']
    assert {row['kind'] for row in detail['activities']} == {'qualify'}
    assert [row['name'] for row in detail['opportunities']] == ['当前项目商机']
    assert [row['content'] for row in detail['outreach']] == ['当前项目触达']
    assert [row['event_type'] for row in detail['attribution']] == ['qualified']
    assert 'private.owner@xinghai.example' not in str(detail)

    with pytest.raises(errors.NotFoundError):
        await project_customer_service.get_customer_detail(
            ctx.session,
            growth_project_id=ctx.second_growth.id,
            customer_id=customer_id,
            scope=_personal_scope(
                user_id=ctx.second_user,
                owner_hasn_id=ctx.second_owner,
            ),
        )
