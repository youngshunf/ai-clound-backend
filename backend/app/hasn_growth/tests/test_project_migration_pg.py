"""获客项目化 S3 迁移、兼容读写与影子核对的真实 PostgreSQL 测试。"""

from __future__ import annotations

from decimal import Decimal
from pathlib import Path
from typing import TYPE_CHECKING
from uuid import uuid4

import pytest
import pytest_asyncio
import sqlalchemy as sa

from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

from backend.app.hasn_core import HasnHumans
from backend.app.hasn_growth.model.activity import Activity
from backend.app.hasn_growth.model.customer import Customer
from backend.app.hasn_growth.model.growth_project import GrowthProject
from backend.app.hasn_growth.model.growth_project_lead import GrowthProjectLead
from backend.app.hasn_growth.model.lead_audit_log import LeadAuditLog
from backend.app.hasn_growth.model.lead_contact import LeadContact
from backend.app.hasn_growth.model.lead_ref import LeadRef
from backend.app.hasn_growth.model.opportunity import Opportunity
from backend.app.hasn_growth.model.outreach_message import OutreachMessage
from backend.app.hasn_growth.model.outreach_message_event import OutreachMessageEvent
from backend.app.hasn_growth.service.funnel_service import growth_funnel_service
from backend.app.hasn_growth.service.project_lead_compatibility_service import (
    project_lead_compatibility_service,
)
from backend.app.hasn_growth.service.project_migration_service import (
    growth_project_migration_service,
)
from backend.app.hasn_project.model.hasn_project import HasnProject
from backend.common.exception import errors
from backend.core.conf import settings
from backend.database.db import SQLALCHEMY_DATABASE_URL
from backend.utils.timezone import timezone

if TYPE_CHECKING:
    from collections.abc import AsyncIterator

    from sqlalchemy.ext.asyncio import AsyncSession

pytestmark = pytest.mark.asyncio

_REPO = Path(__file__).resolve().parents[4]
_SQL_FILES = (
    _REPO / 'backend/sql/hasn_growth/007_create_growth_project_v4_tables.sql',
    _REPO / 'backend/sql/hasn_growth/008_create_growth_pii_key_state.sql',
    _REPO / 'backend/sql/hasn_growth/migrations/2026-07-28-growth-project-v4-columns.sql',
    _REPO / 'backend/sql/hasn_growth/migrations/2026-07-28-growth-pii-key-fence-triggers.sql',
    _REPO / 'backend/sql/hasn_growth/migrations/2026-07-29-growth-playbook-trace-columns.sql',
    _REPO / 'backend/sql/hasn_growth/migrations/2026-07-29-growth-project-association-uniques.sql',
    _REPO / 'backend/sql/hasn_growth/009_create_growth_project_migration_quarantine.sql',
)


async def _apply_sql(session: AsyncSession) -> None:
    raw = await (await session.connection()).get_raw_connection()
    connection = raw.driver_connection
    assert connection is not None
    for sql_file in _SQL_FILES:
        await connection.execute(sql_file.read_text(encoding='utf-8'))


@pytest_asyncio.fixture
async def session() -> AsyncIterator[AsyncSession]:
    engine = create_async_engine(SQLALCHEMY_DATABASE_URL, poolclass=NullPool)
    try:
        async with engine.connect() as connection:
            await connection.execute(sa.select(1))
    except Exception as exc:
        await engine.dispose()
        pytest.skip(f'本地 PostgreSQL 不可达，跳过: {exc!r}')

    db = async_sessionmaker(engine, expire_on_commit=False)()
    try:
        await _apply_sql(db)
        yield db
    finally:
        await db.rollback()
        await db.close()
        await engine.dispose()


async def _seed_personal_chain(
    session: AsyncSession,
    *,
    legacy_status: str = 'replied',
) -> tuple[int, str, LeadContact, LeadRef, Customer, Opportunity, OutreachMessage]:
    suffix = uuid4().hex[:12]
    user_id = 8_000_000_000 + int(suffix[:7], 16)
    owner_hasn_id = f'h_s3_{suffix}'
    session.add(
        HasnHumans(
            hasn_id=owner_hasn_id,
            star_id=f's3{suffix}',
            user_id=user_id,
            nickname='S3 迁移主人',
            status='active',
        )
    )
    contact = LeadContact(
        lead_no=f'L-{suffix}',
        pool_visibility='public',
        company_name='迁移样本企业',
        source_type='public_web',
        source_url='https://example.com/company',
        status='valid',
        confidence_score=Decimal(70),
        normalization_version='s3-test',
    )
    session.add(contact)
    await session.flush()
    lead_ref = LeadRef(
        user_id=user_id,
        lead_contact_id=contact.id,
        source='collect',
        status='qualified',
        note='仅含非敏感业务备注',
    )
    session.add(lead_ref)
    customer = Customer(
        customer_no=f'C-{suffix}',
        user_id=user_id,
        lead_contact_id=contact.id,
        source_kind='outbound_crawl',
        company_name='迁移样本企业',
        lifecycle_status='engaged',
        owner_scope='personal',
    )
    session.add(customer)
    await session.flush()
    opportunity = Opportunity(
        opportunity_no=f'O-{suffix}',
        customer_id=customer.id,
        user_id=user_id,
        name='存量商机',
        stage='proposal',
        currency='CNY',
        created_by_kind='owner',
        owner_scope='personal',
    )
    session.add(opportunity)
    await session.flush()
    message = OutreachMessage(
        customer_id=customer.id,
        opportunity_id=opportunity.id,
        user_id=user_id,
        direction='outbound',
        channel='manual_assist',
        content='不含联系方式的迁移测试内容',
        status=legacy_status,
        approval_status=None,
        delivery_status=None,
        approval_version=None,
        content_version=None,
        sent_at=timezone.now(),
        owner_scope='personal',
    )
    session.add(message)
    await session.flush()
    return (
        user_id,
        owner_hasn_id,
        contact,
        lead_ref,
        customer,
        opportunity,
        message,
    )


async def test_s3_quarantine_sql_is_additive_and_idempotent(
    session: AsyncSession,
) -> None:
    sql_file = _SQL_FILES[-1]
    sql = sql_file.read_text(encoding='utf-8')
    normalized = sql.upper()
    assert 'DROP TABLE' not in normalized
    assert 'DROP COLUMN' not in normalized
    assert 'CASCADE' not in normalized
    await _apply_sql(session)
    actual_schema = await session.scalar(
        sa.text(
            "SELECT table_schema FROM information_schema.tables WHERE table_name='growth_project_migration_quarantine'"
        )
    )
    assert actual_schema == 'hasn_growth'


async def test_personal_migration_dry_run_apply_and_replay_are_consistent(
    session: AsyncSession,
) -> None:
    (
        user_id,
        owner_hasn_id,
        contact,
        _lead_ref,
        customer,
        opportunity,
        message,
    ) = await _seed_personal_chain(session)
    task_count_before = await session.scalar(sa.text('SELECT count(*) FROM hasn_task.task'))
    notification_count_before = await session.scalar(
        sa.text('SELECT count(*) FROM hasn_notification_im_command_outbox')
    )

    dry_run = await growth_project_migration_service.migrate_owner(
        session,
        user_id=user_id,
        dry_run=True,
        change_ticket='TEST-S3-DRY-RUN',
    )
    assert dry_run.status == 'ready'
    assert dry_run.project_created == 1
    assert dry_run.project_leads_upserted == 1
    assert dry_run.crm_rows_updated == 4
    assert dry_run.outreach_rows_mapped == 1
    assert dry_run.quarantined == 0
    assert dry_run.next_cursor == user_id
    assert (
        await session.scalar(
            sa.select(sa.func.count()).select_from(HasnProject).where(HasnProject.owner_id == owner_hasn_id)
        )
        == 0
    )

    applied = await growth_project_migration_service.migrate_owner(
        session,
        user_id=user_id,
        dry_run=False,
        change_ticket='TEST-S3-APPLY',
    )
    assert applied.comparable_counts() == dry_run.comparable_counts()

    platform_project = (
        await session.execute(
            sa.select(HasnProject).where(
                HasnProject.owner_id == owner_hasn_id,
                HasnProject.client_request_id == f'growth-migrate:personal:{owner_hasn_id}',
            )
        )
    ).scalar_one()
    growth_project = (
        await session.execute(sa.select(GrowthProject).where(GrowthProject.platform_project_id == platform_project.id))
    ).scalar_one()
    assert growth_project.status == 'paused'
    assert growth_project.provision_status == 'pending'
    assert (
        await session.scalar(
            sa
            .select(sa.func.count())
            .select_from(GrowthProjectLead)
            .where(
                GrowthProjectLead.growth_project_id == growth_project.id,
                GrowthProjectLead.lead_contact_id == contact.id,
            )
        )
        == 1
    )
    await session.refresh(customer)
    await session.refresh(opportunity)
    await session.refresh(message)
    assert customer.growth_project_id == growth_project.id
    assert opportunity.growth_project_id == growth_project.id
    assert customer.lifecycle_status == 'engaged'
    assert opportunity.stage == 'proposal'
    assert message.growth_project_id == growth_project.id
    assert message.approval_status == 'approved'
    assert message.delivery_status == 'sent'
    assert message.replied_at is not None
    assert (
        await session.scalar(
            sa
            .select(sa.func.count())
            .select_from(OutreachMessageEvent)
            .where(OutreachMessageEvent.outreach_message_id == message.id)
        )
        == 1
    )
    assert (
        await session.scalar(
            sa
            .select(sa.func.count())
            .select_from(Activity)
            .where(
                Activity.kind == 'reply',
                Activity.ref_table == 'outreach_message',
                Activity.ref_id == str(message.id),
            )
        )
        == 1
    )

    replay = await growth_project_migration_service.migrate_owner(
        session,
        user_id=user_id,
        dry_run=False,
        change_ticket='TEST-S3-REPLAY',
    )
    assert replay.comparable_counts() == dry_run.comparable_counts()
    assert (
        await session.scalar(
            sa
            .select(sa.func.count())
            .select_from(HasnProject)
            .where(HasnProject.client_request_id == f'growth-migrate:personal:{owner_hasn_id}')
        )
        == 1
    )
    assert (
        await session.scalar(
            sa
            .select(sa.func.count())
            .select_from(GrowthProjectLead)
            .where(GrowthProjectLead.growth_project_id == growth_project.id)
        )
        == 1
    )
    assert (
        await session.scalar(
            sa
            .select(sa.func.count())
            .select_from(OutreachMessageEvent)
            .where(OutreachMessageEvent.outreach_message_id == message.id)
        )
        == 1
    )
    assert await session.scalar(sa.text('SELECT count(*) FROM hasn_task.task')) == task_count_before
    assert (
        await session.scalar(sa.text('SELECT count(*) FROM hasn_notification_im_command_outbox'))
        == notification_count_before
    )


@pytest.mark.parametrize('legacy_status', ['legacy_unknown', ''])
async def test_unknown_outreach_and_enterprise_rows_enter_quarantine(
    session: AsyncSession,
    legacy_status: str,
) -> None:
    user_id, _owner, _contact, _ref, customer, _opportunity, message = await _seed_personal_chain(
        session, legacy_status=legacy_status
    )
    await session.execute(
        sa
        .update(OutreachMessage)
        .where(OutreachMessage.id == message.id)
        .values(approval_status=None, delivery_status=None)
    )
    await session.refresh(message)
    enterprise_customer = Customer(
        customer_no=f'E-{uuid4().hex[:12]}',
        user_id=user_id,
        source_kind='manual',
        company_name='企业门禁样本',
        lifecycle_status='active',
        owner_scope='enterprise',
        enterprise_id=987654,
        assignee='h_enterprise_member',
    )
    session.add(enterprise_customer)
    await session.flush()

    result = await growth_project_migration_service.migrate_owner(
        session,
        user_id=user_id,
        dry_run=False,
        change_ticket='TEST-S3-QUARANTINE',
    )
    assert result.quarantined == 2
    reasons = set(
        (
            await session.execute(
                sa.text(
                    'SELECT reason_code FROM hasn_growth.growth_project_migration_quarantine '
                    'WHERE (source_table, source_record_id) IN '
                    "    (('outreach_message', :message_id), ('customer', :customer_id))"
                ),
                {
                    'message_id': str(message.id),
                    'customer_id': str(enterprise_customer.id),
                },
            )
        ).scalars()
    )
    assert reasons == {
        'enterprise_identity_gate_closed',
        'unknown_outreach_status',
    }
    await session.refresh(message)
    await session.refresh(customer)
    assert message.approval_status is None
    assert message.delivery_status is None
    assert customer.growth_project_id is not None
    assert enterprise_customer.growth_project_id is None


async def test_dual_write_new_read_and_audited_legacy_fallback(
    session: AsyncSession,
) -> None:
    user_id, _owner, contact, lead_ref, _customer, _opportunity, _message = await _seed_personal_chain(session)
    await growth_project_migration_service.migrate_owner(
        session,
        user_id=user_id,
        dry_run=False,
        change_ticket='TEST-S3-COMPAT',
    )
    growth_project = (
        await session.execute(sa.select(GrowthProject).where(GrowthProject.user_id == user_id))
    ).scalar_one()

    previous_dual = settings.GROWTH_PROJECT_DUAL_WRITE_ENABLED
    previous_cutover = settings.GROWTH_PROJECT_READ_CUTOVER_ENABLED
    try:
        settings.GROWTH_PROJECT_DUAL_WRITE_ENABLED = True
        settings.GROWTH_PROJECT_READ_CUTOVER_ENABLED = False
        await project_lead_compatibility_service.upsert_reference(
            session,
            user_id=user_id,
            lead_contact_id=contact.id,
            source='manual',
            status='dismissed',
            dismiss_reason='目标不匹配',
            note='兼容写入',
            growth_project_id=growth_project.id,
        )
        await session.refresh(lead_ref)
        project_lead = (
            await session.execute(
                sa.select(GrowthProjectLead).where(
                    GrowthProjectLead.growth_project_id == growth_project.id,
                    GrowthProjectLead.lead_contact_id == contact.id,
                )
            )
        ).scalar_one()
        assert (project_lead.status, lead_ref.status) == (
            'dismissed',
            'dismissed',
        )

        new_read = await project_lead_compatibility_service.get_reference(
            session,
            user_id=user_id,
            lead_contact_id=contact.id,
            growth_project_id=growth_project.id,
        )
        assert new_read is not None
        assert new_read.source_table == 'growth_project_lead'
        assert new_read.status == 'dismissed'

        await session.delete(project_lead)
        await session.flush()
        fallback = await project_lead_compatibility_service.get_reference(
            session,
            user_id=user_id,
            lead_contact_id=contact.id,
            growth_project_id=growth_project.id,
        )
        assert fallback is not None
        assert fallback.source_table == 'lead_ref'
        assert (
            await session.scalar(
                sa
                .select(sa.func.count())
                .select_from(LeadAuditLog)
                .where(
                    LeadAuditLog.event_type == 'project_read_fallback',
                    LeadAuditLog.target_ref == str(contact.id),
                )
            )
            == 1
        )

        settings.GROWTH_PROJECT_READ_CUTOVER_ENABLED = True
        assert (
            await project_lead_compatibility_service.get_reference(
                session,
                user_id=user_id,
                lead_contact_id=contact.id,
                growth_project_id=growth_project.id,
            )
            is None
        )
    finally:
        settings.GROWTH_PROJECT_DUAL_WRITE_ENABLED = previous_dual
        settings.GROWTH_PROJECT_READ_CUTOVER_ENABLED = previous_cutover


async def test_enterprise_only_owner_is_quarantined_without_personal_project(
    session: AsyncSession,
) -> None:
    suffix = uuid4().hex[:12]
    user_id = 9_000_000_000 + int(suffix[:7], 16)
    owner_hasn_id = f'h_s3_enterprise_{suffix}'
    session.add(
        HasnHumans(
            hasn_id=owner_hasn_id,
            star_id=f's3e{suffix}',
            user_id=user_id,
            nickname='S3 企业门禁主人',
            status='active',
        )
    )
    session.add(
        Customer(
            customer_no=f'E-{suffix}',
            user_id=user_id,
            source_kind='manual',
            company_name='企业门禁样本',
            lifecycle_status='active',
            owner_scope='enterprise',
            enterprise_id=778899,
            assignee=owner_hasn_id,
        )
    )
    await session.flush()

    result = await growth_project_migration_service.migrate_owner(
        session,
        user_id=user_id,
        dry_run=False,
        change_ticket='TEST-S3-ENTERPRISE-ONLY',
    )
    assert result.status == 'quarantined'
    assert result.project_created == 0
    assert (
        await session.scalar(
            sa.select(sa.func.count()).select_from(HasnProject).where(HasnProject.owner_id == owner_hasn_id)
        )
        == 0
    )


async def test_shadow_report_covers_boundaries_pii_and_associations(
    session: AsyncSession,
) -> None:
    user_id, _owner, _contact, _ref, _customer, _opportunity, _message = await _seed_personal_chain(session)
    await growth_project_migration_service.migrate_owner(
        session,
        user_id=user_id,
        dry_run=False,
        change_ticket='TEST-S3-SHADOW',
    )
    report = await growth_project_migration_service.build_shadow_report(
        session,
        user_id=user_id,
        sample_size=20,
    )
    assert report['schema_version'] == 'growth-project-shadow-v1'
    assert report['status'] == 'pass'
    assert report['differences']['total'] == 0
    assert report['boundaries']['cross_owner'] == 0
    assert report['boundaries']['wrong_enterprise'] == 0
    assert report['pii']['plaintext_rows'] >= 0
    assert report['pii']['hmac_versions']
    assert report['associations']['orphan_crm'] == 0
    assert report['associations']['orphan_tasks'] == 0
    assert report['associations']['orphan_artifacts'] == 0
    assert report['compatibility']['removal_owner'] == 'Growth 后端值班'
    assert report['compatibility']['remove_after'] == '2026-10-31'
    assert 'sample' in report


async def test_legacy_manual_write_reaches_project_read_when_dual_write_enabled(
    session: AsyncSession,
) -> None:
    user_id, _owner, _contact, _ref, _customer, _opportunity, _message = await _seed_personal_chain(session)
    await growth_project_migration_service.migrate_owner(
        session,
        user_id=user_id,
        dry_run=False,
        change_ticket='TEST-S3-WRITE-PATH',
    )
    growth_project = (
        await session.execute(sa.select(GrowthProject).where(GrowthProject.user_id == user_id))
    ).scalar_one()

    previous_dual = settings.GROWTH_PROJECT_DUAL_WRITE_ENABLED
    try:
        settings.GROWTH_PROJECT_DUAL_WRITE_ENABLED = True
        created = await growth_funnel_service.create_manual_lead(
            session,
            user_id=user_id,
            company_name='双写路径样本企业',
        )
        lead_contact_id = int(created['lead_contact_id'])
        assert (
            await session.scalar(
                sa
                .select(sa.func.count())
                .select_from(LeadRef)
                .where(
                    LeadRef.user_id == user_id,
                    LeadRef.lead_contact_id == lead_contact_id,
                )
            )
            == 1
        )
        assert (
            await session.scalar(
                sa
                .select(sa.func.count())
                .select_from(GrowthProjectLead)
                .where(
                    GrowthProjectLead.growth_project_id == growth_project.id,
                    GrowthProjectLead.lead_contact_id == lead_contact_id,
                )
            )
            == 1
        )
        project_read = await growth_funnel_service.get_lead(
            session,
            user_id=user_id,
            lead_contact_id=lead_contact_id,
            growth_project_id=str(growth_project.id),
        )
        assert project_read['lead_contact_id'] == lead_contact_id
        assert project_read['status'] == 'new'
    finally:
        settings.GROWTH_PROJECT_DUAL_WRITE_ENABLED = previous_dual


async def test_project_search_rejects_cross_owner_even_when_requester_has_no_leads(
    session: AsyncSession,
) -> None:
    user_id, _owner, _contact, _ref, _customer, _opportunity, _message = await _seed_personal_chain(session)
    await growth_project_migration_service.migrate_owner(
        session,
        user_id=user_id,
        dry_run=False,
        change_ticket='TEST-S3-READ-ACL',
    )
    growth_project = (
        await session.execute(sa.select(GrowthProject).where(GrowthProject.user_id == user_id))
    ).scalar_one()

    with pytest.raises(errors.NotFoundError):
        await growth_funnel_service.search_leads(
            session,
            user_id=user_id + 99_000_000,
            growth_project_id=str(growth_project.id),
        )
