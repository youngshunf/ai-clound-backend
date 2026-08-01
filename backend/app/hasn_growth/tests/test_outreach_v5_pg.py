"""S8 项目触达审批、投递、回复与合规门禁真实 PostgreSQL 验收。"""

from __future__ import annotations

import uuid

from collections.abc import AsyncIterator
from datetime import timedelta
from decimal import Decimal
from pathlib import Path
from types import SimpleNamespace

import pytest
import pytest_asyncio
import sqlalchemy as sa

from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

from backend.app.hasn.model.hasn_app_entitlement import HasnAppEntitlement
from backend.app.hasn_core import HasnHumans
from backend.app.hasn_growth.model.activity import Activity
from backend.app.hasn_growth.model.contact_channel import ContactChannel
from backend.app.hasn_growth.model.contact_private_profile import ContactPrivateProfile
from backend.app.hasn_growth.model.customer import Customer
from backend.app.hasn_growth.model.growth_attribution_event import (
    GrowthAttributionEvent,
)
from backend.app.hasn_growth.model.growth_project import GrowthProject
from backend.app.hasn_growth.model.lead_contact import LeadContact
from backend.app.hasn_growth.model.outreach_message import OutreachMessage
from backend.app.hasn_growth.model.outreach_message_event import OutreachMessageEvent
from backend.app.hasn_growth.service.dispatch_service import growth_dispatch_service
from backend.app.hasn_growth.service.outreach_service import growth_outreach_service
from backend.app.hasn_growth.service.scope_context import GrowthScope
from backend.app.hasn_project.model.hasn_project import HasnProject
from backend.common.exception import errors
from backend.database.db import SQLALCHEMY_DATABASE_URL
from backend.utils.timezone import timezone

pytestmark = pytest.mark.asyncio

_REPO = Path(__file__).resolve().parents[4]
_SCHEMA_SQL = _REPO / 'backend/sql/hasn_growth/007_create_growth_project_v4_tables.sql'
_S11_SQL = _REPO / 'backend/sql/hasn_growth/migrations/2026-07-29-growth-review-v8.sql'


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
    raw = await (await session.connection()).get_raw_connection()
    driver_connection = raw.driver_connection
    assert driver_connection is not None
    await driver_connection.execute(_SCHEMA_SQL.read_text(encoding='utf-8'))
    await driver_connection.execute(_S11_SQL.read_text(encoding='utf-8'))
    key_fence = (
        await session.execute(
            sa.text(
                'SELECT min_encryption_write_version, min_hmac_write_version '
                'FROM hasn_growth.growth_pii_key_state WHERE id = 1'
            )
        )
    ).one_or_none()
    encryption_version, hmac_version = key_fence or (1, 1)

    tag = uuid.uuid4().hex[:10]
    user_id = 97_200_000_000 + int(uuid.uuid4().int % 500_000_000)
    owner_hasn_id = f'h_growth_s8_{tag}'
    agent_hasn_id = f'a_growth_s8_{tag}'
    platform_project = HasnProject(
        owner_id=owner_hasn_id,
        name=f'S8 触达项目 {tag}',
        status='active',
    )
    session.add_all((
        HasnHumans(
            hasn_id=owner_hasn_id,
            star_id=f's_{user_id}',
            user_id=user_id,
            nickname=f'S8 主人 {tag}',
            status='active',
        ),
        platform_project,
    ))
    await session.flush()
    growth_project = GrowthProject(
        platform_project_id=platform_project.id,
        user_id=user_id,
        owner_hasn_id=owner_hasn_id,
        owner_scope='personal',
        name=f'S8 获客漏斗 {tag}',
        owner_agent_id=agent_hasn_id,
        status='active',
        provision_status='ready',
    )
    session.add(growth_project)
    await session.flush()
    lead = LeadContact(
        lead_no=f'L-S8-{tag}',
        pool_visibility='private',
        company_name=f'S8 受控客户 {tag}',
        source_type='controlled_import',
        status='valid',
        confidence_score=Decimal(90),
        normalization_version='s8-v1',
    )
    session.add(lead)
    await session.flush()
    private_profile = ContactPrivateProfile(
        lead_contact_id=lead.id,
        owner_scope='personal',
        user_id=user_id,
        contact_name_ciphertext='encrypted-contact-name',
        encryption_key_version=encryption_version,
        lawful_basis='controlled_import',
        source_ref=f's8://fixture/{tag}',
        retention_until=timezone.now() + timedelta(days=30),
        status='active',
    )
    session.add(private_profile)
    await session.flush()
    contact_channel = ContactChannel(
        private_profile_id=private_profile.id,
        lead_contact_id=lead.id,
        owner_scope='personal',
        user_id=user_id,
        channel='email',
        value_ciphertext='encrypted-email',
        encryption_key_version=encryption_version,
        value_hmac=f'hmac-{tag}',
        hash_key_version=hmac_version,
        lawful_basis='controlled_import',
        source_ref=f's8://fixture/{tag}',
        retention_until=timezone.now() + timedelta(days=30),
        status='active',
    )
    customer = Customer(
        customer_no=f'C-S8-{tag}',
        user_id=user_id,
        growth_project_id=growth_project.id,
        lead_contact_id=lead.id,
        source_kind='controlled_import',
        company_name=f'S8 受控客户 {tag}',
        contact_name='陈女士',
        email='s8-controlled@example.com',
        lifecycle_status='active',
        owner_agent_id=agent_hasn_id,
        owner_scope='personal',
    )
    entitlement = HasnAppEntitlement(
        app_id='growth',
        subject_type='owner',
        subject_id=owner_hasn_id,
        source='admin_grant',
        status='active',
        feature_key='app:growth',
    )
    session.add_all((
        customer,
        contact_channel,
        entitlement,
    ))
    await session.flush()
    try:
        yield SimpleNamespace(
            session=session,
            user_id=user_id,
            owner_hasn_id=owner_hasn_id,
            agent_hasn_id=agent_hasn_id,
            growth_project=growth_project,
            customer=customer,
            contact_channel=contact_channel,
            entitlement=entitlement,
            scope=GrowthScope(
                user_id=user_id,
                owner_hasn_id=owner_hasn_id,
            ),
        )
    finally:
        await session.rollback()
        await session.close()
        await engine.dispose()


async def _approved_email(ctx: SimpleNamespace, suffix: str) -> dict:
    """创建并批准一条使用真实冻结目标引用的邮件触达。"""
    message = await growth_outreach_service.send_outreach(
        ctx.session,
        user_id=ctx.user_id,
        customer_id=ctx.customer.id,
        channel='email',
        content=f'您好，想沟通一下增长方案（{suffix}）',
        agent_id=ctx.agent_hasn_id,
        scope=ctx.scope,
        growth_project_id=ctx.growth_project.id,
        idempotency_key=f's8:email:{suffix}',
    )
    return await growth_outreach_service.approve_outreach(
        ctx.session,
        user_id=ctx.user_id,
        message_id=message['id'],
        approver_user_id=ctx.user_id,
        expected_content_version=1,
        growth_project_id=ctx.growth_project.id,
        scope=ctx.scope,
    )


async def test_draft_submit_approval_version_and_manual_attestation_are_orthogonal(
    ctx: SimpleNamespace,
) -> None:
    """改稿使旧批准失效；人工证明不伪装成 sent/delivered。"""
    draft = await growth_outreach_service.draft_outreach(
        ctx.session,
        user_id=ctx.user_id,
        growth_project_id=ctx.growth_project.id,
        customer_id=ctx.customer.id,
        channel='manual_assist',
        content='您好，想了解贵司近期的获客计划',
        content_assets={'attachments': ['hasn://asset/s8-controlled-deck']},
        intent_note='首次触达，确认是否有获客提效需求',
        agent_id=ctx.agent_hasn_id,
        idempotency_key='s8:draft:manual:1',
        scope=ctx.scope,
    )
    replay = await growth_outreach_service.draft_outreach(
        ctx.session,
        user_id=ctx.user_id,
        growth_project_id=ctx.growth_project.id,
        customer_id=ctx.customer.id,
        channel='manual_assist',
        content='重放不得新建',
        agent_id=ctx.agent_hasn_id,
        idempotency_key='s8:draft:manual:1',
        scope=ctx.scope,
    )
    assert draft['id'] == replay['id']
    assert draft['approval_status'] == 'draft'
    assert draft['delivery_status'] == 'not_queued'
    assert draft['content_version'] == 1

    submitted = await growth_outreach_service.submit_outreach(
        ctx.session,
        user_id=ctx.user_id,
        growth_project_id=ctx.growth_project.id,
        message_id=draft['id'],
        expected_content_version=1,
        idempotency_key='s8:submit:manual:1',
        scope=ctx.scope,
    )
    assert submitted['approval_status'] == 'pending_approval'
    assert submitted['delivery_status'] == 'not_queued'

    approved = await growth_outreach_service.approve_outreach(
        ctx.session,
        user_id=ctx.user_id,
        message_id=draft['id'],
        approver_user_id=ctx.user_id,
        expected_content_version=1,
        growth_project_id=ctx.growth_project.id,
        scope=ctx.scope,
    )
    assert approved['approval_status'] == 'approved'
    assert approved['approval_version'] == approved['content_version'] == 1
    assert approved['delivery_status'] == 'not_queued'

    edited = await growth_outreach_service.edit_outreach(
        ctx.session,
        user_id=ctx.user_id,
        growth_project_id=ctx.growth_project.id,
        message_id=draft['id'],
        expected_content_version=1,
        content='您好，想请教贵司今年的客户增长目标',
        content_assets={'attachments': ['hasn://asset/s8-controlled-deck-v2']},
        scope=ctx.scope,
    )
    assert edited['content_version'] == 2
    assert edited['approval_version'] == 1
    assert edited['approval_status'] == 'pending_approval'

    with pytest.raises(errors.ConflictError):
        await growth_outreach_service.build_send_material(
            ctx.session,
            user_id=ctx.user_id,
            message_id=draft['id'],
            expected_content_version=1,
            growth_project_id=ctx.growth_project.id,
            scope=ctx.scope,
        )

    reapproved = await growth_outreach_service.approve_outreach(
        ctx.session,
        user_id=ctx.user_id,
        message_id=draft['id'],
        approver_user_id=ctx.user_id,
        expected_content_version=2,
        growth_project_id=ctx.growth_project.id,
        scope=ctx.scope,
    )
    assert reapproved['approval_version'] == reapproved['content_version'] == 2

    attested = await growth_outreach_service.attest_manual_send(
        ctx.session,
        user_id=ctx.user_id,
        growth_project_id=ctx.growth_project.id,
        message_id=draft['id'],
        expected_content_version=2,
        actor_id=ctx.owner_hasn_id,
        channel_actual='email',
        proof={'method': 'owner_checkbox', 'note': '已在企业邮箱客户端人工发送'},
        idempotency_key='s8:manual-attested:1',
        scope=ctx.scope,
    )
    assert attested['manual_attested_at'] is not None
    assert attested['manual_attested_by'] == ctx.owner_hasn_id
    assert attested['delivery_status'] == 'not_queued'
    assert attested['sent_at'] is None

    events = (
        (
            await ctx.session.execute(
                sa
                .select(OutreachMessageEvent)
                .where(OutreachMessageEvent.outreach_message_id == draft['id'])
                .order_by(OutreachMessageEvent.id)
            )
        )
        .scalars()
        .all()
    )
    assert [event.event_type for event in events] == [
        'drafted',
        'approval_requested',
        'approved',
        'approval_requested',
        'approved',
        'manual_attested',
    ]
    assert events[0].meta_data['first_touch_candidate'] is True
    assert all(event.growth_project_id == ctx.growth_project.id for event in events)
    attribution = (
        (
            await ctx.session.execute(
                sa
                .select(GrowthAttributionEvent)
                .where(
                    GrowthAttributionEvent.growth_project_id == ctx.growth_project.id,
                    GrowthAttributionEvent.source_ref == f'outreach:{draft["id"]}',
                )
                .order_by(GrowthAttributionEvent.event_type)
            )
        )
        .scalars()
        .all()
    )
    assert [event.event_type for event in attribution] == ['cost', 'outreach_sent']
    assert attribution[0].amount == Decimal(0)
    assert attribution[0].meta_data['cost_state'] == 'known'
    assert attribution[1].meta_data['manual_attested'] is True


async def test_enterprise_manager_sees_team_state_but_cannot_act_for_assignee(
    ctx: SimpleNamespace,
) -> None:
    """经理只能看团队审批聚合，不能代负责人审批、拒绝、取材或证明。"""
    enterprise_id = 73_001
    assignee = 'h_growth_s8_assignee'
    ctx.growth_project.owner_scope = 'enterprise'
    ctx.growth_project.enterprise_id = enterprise_id
    ctx.customer.owner_scope = 'enterprise'
    ctx.customer.enterprise_id = enterprise_id
    ctx.customer.assignee = assignee
    await ctx.session.flush()
    message = await growth_outreach_service.send_outreach(
        ctx.session,
        user_id=ctx.user_id,
        customer_id=ctx.customer.id,
        channel='manual_assist',
        content='您好，想确认本季度增长目标',
        agent_id='a_growth_s8_assignee',
        growth_project_id=ctx.growth_project.id,
        idempotency_key='s8:enterprise:assignee',
    )
    manager_scope = GrowthScope(
        user_id=ctx.user_id,
        owner_hasn_id=ctx.owner_hasn_id,
        enterprise_id=enterprise_id,
        viewer_role='manager',
        view='team',
    )
    overview = await growth_outreach_service.team_approval_overview(
        ctx.session,
        user_id=ctx.user_id,
        scope=manager_scope,
        growth_project_id=ctx.growth_project.id,
    )
    assert overview == [
        {
            'assignee': assignee,
            'pending_count': 1,
            'earliest_waiting_at': overview[0]['earliest_waiting_at'],
        }
    ]
    with pytest.raises(errors.NotFoundError):
        await growth_outreach_service.approve_outreach(
            ctx.session,
            user_id=ctx.user_id,
            message_id=message['id'],
            approver_user_id=ctx.user_id,
            expected_content_version=1,
            growth_project_id=ctx.growth_project.id,
            scope=manager_scope,
        )
    with pytest.raises(errors.NotFoundError):
        await growth_outreach_service.reject_outreach(
            ctx.session,
            user_id=ctx.user_id,
            message_id=message['id'],
            approver_user_id=ctx.user_id,
            reason='经理不得代负责人拒绝',
            expected_content_version=1,
            growth_project_id=ctx.growth_project.id,
            scope=manager_scope,
        )

    assignee_scope = GrowthScope(
        user_id=ctx.user_id,
        owner_hasn_id=assignee,
        enterprise_id=enterprise_id,
        viewer_role='member',
        view='mine',
    )
    await growth_outreach_service.approve_outreach(
        ctx.session,
        user_id=ctx.user_id,
        message_id=message['id'],
        approver_user_id=ctx.user_id,
        expected_content_version=1,
        growth_project_id=ctx.growth_project.id,
        scope=assignee_scope,
    )
    with pytest.raises(errors.NotFoundError):
        await growth_outreach_service.build_send_material(
            ctx.session,
            user_id=ctx.user_id,
            message_id=message['id'],
            expected_content_version=1,
            growth_project_id=ctx.growth_project.id,
            scope=manager_scope,
        )
    with pytest.raises(errors.NotFoundError):
        await growth_outreach_service.attest_manual_send(
            ctx.session,
            user_id=ctx.user_id,
            growth_project_id=ctx.growth_project.id,
            message_id=message['id'],
            expected_content_version=1,
            actor_id=ctx.owner_hasn_id,
            channel_actual='email',
            proof={'method': 'manager_attempt'},
            idempotency_key='s8:manager:attest',
            scope=manager_scope,
        )


async def test_worker_refuses_paused_project(ctx: SimpleNamespace) -> None:
    """worker 二次门禁验证项目运行态。"""
    message = await _approved_email(ctx, 'paused')
    ctx.growth_project.status = 'paused'
    await ctx.session.flush()

    stat = await growth_dispatch_service.dispatch_approved_batch(
        ctx.session,
        limit=200,
        now_hour=10,
    )
    assert stat['blocked_project'] >= 1
    row = await ctx.session.get(OutreachMessage, message['id'])
    assert row is not None
    assert row.approval_status == 'approved'
    assert row.delivery_status == 'blocked_compliance'
    event = (
        (
            await ctx.session.execute(
                sa.select(OutreachMessageEvent).where(
                    OutreachMessageEvent.outreach_message_id == message['id'],
                    OutreachMessageEvent.event_type == 'blocked_compliance',
                )
            )
        )
        .scalars()
        .one()
    )
    assert event.error_class == 'project_not_active'


async def test_worker_refuses_stale_approval_and_changed_target_version(
    ctx: SimpleNamespace,
) -> None:
    """worker 拒绝失效批准，并拒绝审批后轮换的目标密钥版本。"""
    stale = await _approved_email(ctx, 'stale')
    stale_row = await ctx.session.get(OutreachMessage, stale['id'])
    assert stale_row is not None
    stale_row.content_version = 2
    await ctx.session.flush()
    stale_stat = await growth_dispatch_service.dispatch_approved_batch(
        ctx.session,
        limit=200,
        now_hour=10,
    )
    assert stale_stat['blocked_compliance'] >= 1
    assert stale_row.delivery_status == 'blocked_compliance'

    changed = await _approved_email(ctx, 'target-version')
    ctx.contact_channel.encryption_key_version += 1
    await ctx.session.flush()
    target_stat = await growth_dispatch_service.dispatch_approved_batch(
        ctx.session,
        limit=200,
        now_hour=10,
    )
    assert target_stat['invalid_target'] >= 1
    changed_row = await ctx.session.get(OutreachMessage, changed['id'])
    assert changed_row is not None
    assert changed_row.delivery_status == 'failed'
    target_event = await ctx.session.scalar(
        sa.select(OutreachMessageEvent).where(
            OutreachMessageEvent.outreach_message_id == changed['id'],
            OutreachMessageEvent.event_type == 'failed',
        )
    )
    assert target_event is not None
    assert target_event.error_class == 'contact_channel_version_changed'


async def test_worker_blocks_expired_entitlement_and_exhausted_budget(
    ctx: SimpleNamespace,
) -> None:
    """权益和月预算都在实际发送前由 worker 重查。"""
    entitlement_message = await _approved_email(ctx, 'entitlement')
    ctx.entitlement.status = 'revoked'
    await ctx.session.flush()
    entitlement_stat = await growth_dispatch_service.dispatch_approved_batch(
        ctx.session,
        limit=200,
        now_hour=10,
    )
    assert entitlement_stat['blocked_entitlement'] >= 1
    entitlement_row = await ctx.session.get(
        OutreachMessage,
        entitlement_message['id'],
    )
    assert entitlement_row is not None
    assert entitlement_row.delivery_status == 'blocked_compliance'

    ctx.entitlement.status = 'active'
    ctx.growth_project.monthly_budget = Decimal('10.00')
    ctx.session.add(
        GrowthAttributionEvent(
            growth_project_id=ctx.growth_project.id,
            event_type='cost',
            customer_id=ctx.customer.id,
            amount=Decimal('10.00'),
            currency='CNY',
            idempotency_key='s8:cost:budget-exhausted',
        )
    )
    budget_message = await _approved_email(ctx, 'budget')
    await ctx.session.flush()
    budget_stat = await growth_dispatch_service.dispatch_approved_batch(
        ctx.session,
        limit=200,
        now_hour=10,
    )
    assert budget_stat['blocked_budget'] >= 1
    budget_row = await ctx.session.get(OutreachMessage, budget_message['id'])
    assert budget_row is not None
    assert budget_row.delivery_status == 'blocked_compliance'


async def test_provider_receipts_are_idempotent_and_never_infer_delivery(
    ctx: SimpleNamespace,
) -> None:
    """渠道受理只到 sent，必须等待独立回执才能进入 delivered。"""
    message = await _approved_email(ctx, 'provider-receipt')
    await growth_outreach_service.mark_sending(
        ctx.session,
        user_id=ctx.user_id,
        message_id=message['id'],
        idempotency_key='s8:dispatch:provider-receipt',
    )
    sent = await growth_outreach_service.record_delivery_receipt(
        ctx.session,
        user_id=ctx.user_id,
        message_id=message['id'],
        provider_event_id='provider-sent-s8-1',
        outcome='sent',
        channel_actual='email',
    )
    replay = await growth_outreach_service.record_delivery_receipt(
        ctx.session,
        user_id=ctx.user_id,
        message_id=message['id'],
        provider_event_id='provider-sent-s8-1',
        outcome='sent',
        channel_actual='email',
    )
    assert sent['delivery_status'] == replay['delivery_status'] == 'sent'

    delivered = await growth_outreach_service.record_delivery_receipt(
        ctx.session,
        user_id=ctx.user_id,
        message_id=message['id'],
        provider_event_id='provider-delivered-s8-1',
        outcome='delivered',
        channel_actual='email',
    )
    assert delivered['delivery_status'] == 'delivered'
    events = (
        (
            await ctx.session.execute(
                sa
                .select(OutreachMessageEvent.event_type)
                .where(OutreachMessageEvent.outreach_message_id == message['id'])
                .order_by(OutreachMessageEvent.id)
            )
        )
        .scalars()
        .all()
    )
    assert events == [
        'drafted',
        'approval_requested',
        'approved',
        'queued',
        'sending',
        'sent',
        'delivered',
    ]
    attribution_counts = dict(
        (
            await ctx.session.execute(
                sa
                .select(
                    GrowthAttributionEvent.event_type,
                    sa.func.count(),
                )
                .where(
                    GrowthAttributionEvent.growth_project_id == ctx.growth_project.id,
                    GrowthAttributionEvent.source_ref == f'outreach:{message["id"]}',
                )
                .group_by(GrowthAttributionEvent.event_type)
            )
        ).all()
    )
    assert attribution_counts == {'cost': 1, 'outreach_sent': 1}
    cost_event = await ctx.session.scalar(
        sa.select(GrowthAttributionEvent).where(
            GrowthAttributionEvent.growth_project_id == ctx.growth_project.id,
            GrowthAttributionEvent.event_type == 'cost',
            GrowthAttributionEvent.source_ref == f'outreach:{message["id"]}',
        )
    )
    assert cost_event is not None
    assert cost_event.amount is None
    assert cost_event.meta_data['cost_state'] == 'unknown'


async def test_reply_idempotency_keeps_one_fact_activity_and_notification(
    ctx: SimpleNamespace,
) -> None:
    """同一 provider 回复事件重放不会重复建消息、activity 或通知。"""
    first = await growth_outreach_service.record_inbound_reply(
        ctx.session,
        user_id=ctx.user_id,
        customer_id=ctx.customer.id,
        channel='email',
        content='可以，下周二下午沟通',
        provider_event_id='provider-reply-s8-1',
        scope=ctx.scope,
    )
    second = await growth_outreach_service.record_inbound_reply(
        ctx.session,
        user_id=ctx.user_id,
        customer_id=ctx.customer.id,
        channel='email',
        content='重放内容不得覆盖首次事实',
        provider_event_id='provider-reply-s8-1',
        scope=ctx.scope,
    )
    assert first['id'] == second['id']
    inbound_count = await ctx.session.scalar(
        sa
        .select(sa.func.count())
        .select_from(OutreachMessage)
        .where(
            OutreachMessage.customer_id == ctx.customer.id,
            OutreachMessage.direction == 'inbound',
        )
    )
    activity_count = await ctx.session.scalar(
        sa
        .select(sa.func.count())
        .select_from(Activity)
        .where(
            Activity.customer_id == ctx.customer.id,
            Activity.kind == 'reply',
        )
    )
    event_count = await ctx.session.scalar(
        sa
        .select(sa.func.count())
        .select_from(OutreachMessageEvent)
        .where(
            OutreachMessageEvent.outreach_message_id == first['id'],
            OutreachMessageEvent.event_type == 'replied',
        )
    )
    assert inbound_count == activity_count == event_count == 1
    attribution_count = await ctx.session.scalar(
        sa
        .select(sa.func.count())
        .select_from(GrowthAttributionEvent)
        .where(
            GrowthAttributionEvent.growth_project_id == ctx.growth_project.id,
            GrowthAttributionEvent.event_type == 'replied',
            GrowthAttributionEvent.source_ref == f'outreach:{first["id"]}',
        )
    )
    assert attribution_count == 1


async def test_project_policy_and_entitlement_usage_gates_are_server_side(
    ctx: SimpleNamespace,
) -> None:
    """静默、每日频控、权益配额和未知成本预算门禁都从服务端事实计算。"""
    approved = await _approved_email(ctx, 'project-policy-gates')
    message = await ctx.session.get(OutreachMessage, approved['id'])
    assert message is not None
    ctx.growth_project.quiet_hours_start = 22
    ctx.growth_project.quiet_hours_end = 8
    ctx.growth_project.daily_outreach_limit = 1
    await ctx.session.flush()

    quiet = await growth_outreach_service.dispatch_preflight(
        ctx.session,
        message=message,
        now_hour=23,
    )
    assert quiet['error_class'] == 'quiet_hours'

    ctx.session.add(
        GrowthAttributionEvent(
            growth_project_id=ctx.growth_project.id,
            event_type='outreach_sent',
            customer_id=ctx.customer.id,
            source_kind='email',
            source_ref='outreach:prior-daily',
            idempotency_key='s11:usage:daily:1',
            meta_data={'usage_kind': 'outreach', 'channel': 'email'},
        )
    )
    await ctx.session.flush()
    daily = await growth_outreach_service.dispatch_preflight(
        ctx.session,
        message=message,
        now_hour=10,
    )
    assert daily['error_class'] == 'daily_outreach_limit'

    ctx.growth_project.daily_outreach_limit = 20
    ctx.entitlement.quota_json = {'monthly_outreach': 1}
    await ctx.session.flush()
    entitlement = await growth_outreach_service.dispatch_preflight(
        ctx.session,
        message=message,
        now_hour=10,
    )
    assert entitlement['error_class'] == 'entitlement_quota_exhausted'

    ctx.entitlement.quota_json = {}
    ctx.growth_project.monthly_budget = Decimal(100)
    ctx.session.add(
        GrowthAttributionEvent(
            growth_project_id=ctx.growth_project.id,
            event_type='cost',
            customer_id=ctx.customer.id,
            source_kind='email',
            source_ref='outreach:prior-unknown-cost',
            amount=None,
            currency='CNY',
            idempotency_key='s11:cost:unknown:1',
            meta_data={'cost_state': 'unknown', 'usage_kind': 'outreach'},
        )
    )
    await ctx.session.flush()
    unknown_cost = await growth_outreach_service.dispatch_preflight(
        ctx.session,
        message=message,
        now_hour=10,
    )
    assert unknown_cost['error_class'] == 'cost_unknown_budget_guard'
