"""获客存量 PII 迁移与隔离的真实 PostgreSQL 测试。

覆盖可证明个人主体与公开商业来源的联系人迁移、仅姓名资料迁移、默认 dry-run、
幂等重跑，以及人工/企业/无同意数据只进入无明文隔离清单。

需要 export DATABASE_PORT=15432。
"""

from __future__ import annotations

import json

from decimal import Decimal
from pathlib import Path
from typing import TYPE_CHECKING
from uuid import uuid4

import pytest
import pytest_asyncio

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

from backend.app.hasn_growth.model.contact_channel import ContactChannel
from backend.app.hasn_growth.model.contact_private_profile import ContactPrivateProfile
from backend.app.hasn_growth.model.customer import Customer
from backend.app.hasn_growth.model.form_submission import FormSubmission
from backend.app.hasn_growth.model.growth_pii_migration_quarantine import (
    GrowthPiiMigrationQuarantine,
)
from backend.app.hasn_growth.model.lead_contact import LeadContact
from backend.app.hasn_growth.model.lead_contact_source import LeadContactSource
from backend.app.hasn_growth.model.lead_ref import LeadRef
from backend.app.hasn_growth.service.pii_keyring import require_growth_pii_keyring
from backend.app.hasn_growth.service.pii_migration_service import (
    MigrationSource,
    growth_pii_migration_service,
)
from backend.database.db import SQLALCHEMY_DATABASE_URL
from backend.utils.timezone import timezone

if TYPE_CHECKING:
    from collections.abc import AsyncIterator

    from sqlalchemy.ext.asyncio import AsyncSession

pytestmark = pytest.mark.asyncio

_REPO = Path(__file__).resolve().parents[4]
_SCHEMA_SQL = _REPO / 'backend/sql/hasn_growth/007_create_growth_project_v4_tables.sql'
_KEY_STATE_SQL = _REPO / 'backend/sql/hasn_growth/008_create_growth_pii_key_state.sql'
_MIGRATION_SQL = _REPO / 'backend/sql/hasn_growth/migrations/2026-07-28-growth-project-v4-columns.sql'
_KEY_FENCE_SQL = (
    _REPO / 'backend/sql/hasn_growth/migrations/2026-07-28-growth-pii-key-fence-triggers.sql'
)


async def _apply_sql(session: AsyncSession) -> None:
    """经 asyncpg simple query 协议运行可重复的 S1 SQL。"""
    raw = await (await session.connection()).get_raw_connection()
    connection = raw.driver_connection
    assert connection is not None
    await connection.execute(_SCHEMA_SQL.read_text(encoding='utf-8'))
    await connection.execute(_KEY_STATE_SQL.read_text(encoding='utf-8'))
    await connection.execute(_MIGRATION_SQL.read_text(encoding='utf-8'))
    await connection.execute(_KEY_FENCE_SQL.read_text(encoding='utf-8'))


@pytest_asyncio.fixture
async def session() -> AsyncIterator[AsyncSession]:
    engine = create_async_engine(SQLALCHEMY_DATABASE_URL, poolclass=NullPool)
    try:
        async with engine.connect() as connection:
            await connection.execute(select(1))
    except Exception as exc:
        await engine.dispose()
        pytest.skip(f'本地 PostgreSQL 不可达，跳过: {exc!r}')

    db = async_sessionmaker(engine, expire_on_commit=False)()
    await _apply_sql(db)
    try:
        yield db
    finally:
        await db.rollback()
        await db.close()
        await engine.dispose()


async def _public_contact(
    session: AsyncSession,
    *,
    user_id: int,
    suffix: str,
    with_channels: bool,
) -> LeadContact:
    """创建带 Owner 引用和公开来源证据的存量联系人。"""
    contact = LeadContact(
        lead_no=f'MIG{suffix}',
        pool_visibility='public',
        company_name='迁移测试企业',
        contact_name=f'联系人{suffix}',
        email=f'{suffix.casefold()}@example.com' if with_channels else None,
        phone='+86 138 0013 8000' if with_channels else None,
        address='杭州市西湖区示例路 1 号' if with_channels else None,
        source_type='public_web',
        source_url=f'https://example.com/company/{suffix}',
        status='valid',
        confidence_score=Decimal(50),
        normalization_version='legacy-v1',
        meta_data={},
    )
    session.add(contact)
    await session.flush()
    session.add_all([
        LeadRef(
            user_id=user_id,
            lead_contact_id=contact.id,
            source='collect',
            status='new',
        ),
        LeadContactSource(
            lead_contact_id=contact.id,
            source_type='public_web',
            source_url=f'https://example.com/company/{suffix}',
            match_dimension='new',
            seen_at=timezone.now(),
            meta_data={},
        ),
    ])
    await session.flush()
    return contact


async def test_contact_migration_encrypts_per_owner_and_is_idempotent(
    session: AsyncSession,
) -> None:
    """有主体和公开来源证据时迁移；重跑不改密文、不重复建行，旧列留待 S13。"""
    user_id = 98_100_000 + int(uuid4().int % 100_000)
    full = await _public_contact(
        session,
        user_id=user_id,
        suffix=uuid4().hex[:8],
        with_channels=True,
    )
    name_only = await _public_contact(
        session,
        user_id=user_id,
        suffix=uuid4().hex[:8],
        with_channels=False,
    )

    result = await growth_pii_migration_service.migrate_batch(
        session,
        keyring=require_growth_pii_keyring(),
        source_table='contact',
        after_id=min(full.id, name_only.id) - 1,
        batch_size=10,
        dry_run=False,
    )
    assert result.scanned == 2
    assert result.migrated == 2
    assert result.quarantined == 0
    assert result.next_cursor == max(full.id, name_only.id)

    profiles = (
        (
            await session.execute(
                select(ContactPrivateProfile)
                .where(
                    ContactPrivateProfile.user_id == user_id,
                    ContactPrivateProfile.lead_contact_id.in_((full.id, name_only.id)),
                )
                .order_by(ContactPrivateProfile.lead_contact_id)
            )
        )
        .scalars()
        .all()
    )
    assert len(profiles) == 2
    full_profile = next(row for row in profiles if row.lead_contact_id == full.id)
    assert full.contact_name is not None
    assert full.contact_name not in (full_profile.contact_name_ciphertext or '')

    channels = (
        (
            await session.execute(
                select(ContactChannel)
                .where(
                    ContactChannel.user_id == user_id,
                    ContactChannel.lead_contact_id == full.id,
                )
                .order_by(ContactChannel.channel)
            )
        )
        .scalars()
        .all()
    )
    assert {row.channel for row in channels} == {'email', 'phone', 'postal_address'}
    plaintext = f'{full.email}|{full.phone}|{full.address}'
    assert all(row.value_ciphertext not in plaintext for row in channels)
    ciphertext_snapshot = (
        full_profile.contact_name_ciphertext,
        tuple((row.id, row.value_ciphertext, row.value_hmac) for row in channels),
    )

    rerun = await growth_pii_migration_service.migrate_batch(
        session,
        keyring=require_growth_pii_keyring(),
        source_table='contact',
        after_id=min(full.id, name_only.id) - 1,
        batch_size=10,
        dry_run=False,
    )
    assert rerun.migrated == 2
    await session.refresh(full_profile)
    for row in channels:
        await session.refresh(row)
    assert (
        full_profile.contact_name_ciphertext,
        tuple((row.id, row.value_ciphertext, row.value_hmac) for row in channels),
    ) == ciphertext_snapshot
    assert full.contact_name and full.email and full.phone and full.address


async def test_unproven_and_enterprise_rows_only_write_plaintext_free_quarantine(
    session: AsyncSession,
) -> None:
    """人工来源、企业主体和无同意表单不能猜测迁移，隔离行不保存原值。"""
    user_id = 98_200_000 + int(uuid4().int % 100_000)
    manual_email = f'manual-{uuid4().hex[:8]}@example.com'
    manual = LeadContact(
        lead_no=f'MAN{uuid4().hex[:8]}',
        pool_visibility='private',
        contact_name='人工联系人',
        email=manual_email,
        source_type='manual',
        status='valid',
        confidence_score=Decimal(50),
        normalization_version='legacy-v1',
        meta_data={},
    )
    session.add(manual)
    await session.flush()
    session.add(
        LeadRef(
            user_id=user_id,
            lead_contact_id=manual.id,
            source='manual',
            status='new',
        )
    )

    enterprise_wechat = f'enterprise_{uuid4().hex[:8]}'
    enterprise_customer = Customer(
        customer_no=f'ENT{uuid4().hex[:8]}',
        user_id=user_id,
        lead_contact_id=manual.id,
        source_kind='manual',
        wechat=enterprise_wechat,
        lifecycle_status='active',
        owner_scope='enterprise',
        enterprise_id=88_000_000 + int(uuid4().int % 100_000),
    )
    session.add(enterprise_customer)

    form_email = f'no-consent-{uuid4().hex[:8]}@example.com'
    no_consent_form = FormSubmission(
        user_id=user_id,
        email=form_email,
        name='无同意留资',
        status='pending',
        lead_contact_id=manual.id,
        owner_scope='personal',
        payload={'email': form_email},
    )
    session.add(no_consent_form)
    await session.flush()

    migration_cases: tuple[tuple[MigrationSource, int], ...] = (
        ('contact', manual.id - 1),
        ('customer', enterprise_customer.id - 1),
        ('form_submission', no_consent_form.id - 1),
    )
    for source_table, after_id in migration_cases:
        await growth_pii_migration_service.migrate_batch(
            session,
            keyring=require_growth_pii_keyring(),
            source_table=source_table,
            after_id=after_id,
            batch_size=1,
            dry_run=False,
        )

    quarantine = (
        (
            await session.execute(
                select(GrowthPiiMigrationQuarantine).where(GrowthPiiMigrationQuarantine.user_id_hint == user_id)
            )
        )
        .scalars()
        .all()
    )
    assert {row.reason_code for row in quarantine} == {
        'lawful_basis_unproven',
        'enterprise_mode_disabled',
        'form_consent_unproven',
    }
    serialized = json.dumps(
        [
            {
                'source_table': row.source_table,
                'source_record_id': row.source_record_id,
                'reason_code': row.reason_code,
                'field_names': row.field_names,
                'pii_fingerprint': row.pii_fingerprint,
            }
            for row in quarantine
        ],
        ensure_ascii=False,
    )
    assert manual_email not in serialized
    assert enterprise_wechat not in serialized
    assert form_email not in serialized
    assert all(row.pii_fingerprint and row.pii_fingerprint.startswith('v2:') for row in quarantine)
    profile_count = (
        await session.execute(
            select(func.count())
            .select_from(ContactPrivateProfile)
            .where(ContactPrivateProfile.lead_contact_id == manual.id)
        )
    ).scalar_one()
    assert profile_count == 0

    for source_table, after_id in migration_cases:
        await growth_pii_migration_service.migrate_batch(
            session,
            keyring=require_growth_pii_keyring(),
            source_table=source_table,
            after_id=after_id,
            batch_size=1,
            dry_run=False,
        )
    rerun_count = (
        await session.execute(
            select(func.count())
            .select_from(GrowthPiiMigrationQuarantine)
            .where(GrowthPiiMigrationQuarantine.user_id_hint == user_id)
        )
    ).scalar_one()
    assert rerun_count == 3


async def test_customer_and_consented_form_migrate_to_linked_contact(
    session: AsyncSession,
) -> None:
    """客户需公开来源证据，表单需完整同意证据；成功后只回填新的私有引用。"""
    user_id = 98_250_000 + int(uuid4().int % 100_000)
    customer_contact = await _public_contact(
        session,
        user_id=user_id,
        suffix=uuid4().hex[:8],
        with_channels=False,
    )
    customer_email = f'customer-{uuid4().hex[:8]}@example.com'
    customer_wechat = f'customer_{uuid4().hex[:8]}'
    customer = Customer(
        customer_no=f'CUS{uuid4().hex[:8]}',
        user_id=user_id,
        lead_contact_id=customer_contact.id,
        source_kind='outbound_crawl',
        contact_name='存量客户联系人',
        email=customer_email,
        wechat=customer_wechat,
        lifecycle_status='active',
        owner_scope='personal',
    )
    session.add(customer)

    form_contact = LeadContact(
        lead_no=f'FORM{uuid4().hex[:8]}',
        pool_visibility='private',
        source_type='inbound_form',
        status='valid',
        confidence_score=Decimal(50),
        normalization_version='legacy-v1',
        meta_data={},
    )
    session.add(form_contact)
    await session.flush()
    form_email = f'form-{uuid4().hex[:8]}@example.com'
    form = FormSubmission(
        user_id=user_id,
        email=form_email,
        name='同意留资联系人',
        status='pending',
        lead_contact_id=form_contact.id,
        privacy_notice_version='2026-07',
        consent_purpose='sales_contact',
        consent_source='landing_form',
        consent_at=timezone.now(),
        owner_scope='personal',
        payload={'message_received': True},
    )
    session.add(form)
    await session.flush()

    customer_result = await growth_pii_migration_service.migrate_batch(
        session,
        keyring=require_growth_pii_keyring(),
        source_table='customer',
        after_id=customer.id - 1,
        batch_size=1,
        dry_run=False,
    )
    form_result = await growth_pii_migration_service.migrate_batch(
        session,
        keyring=require_growth_pii_keyring(),
        source_table='form_submission',
        after_id=form.id - 1,
        batch_size=1,
        dry_run=False,
    )
    assert customer_result.migrated == 1 and customer_result.quarantined == 0
    assert form_result.migrated == 1 and form_result.quarantined == 0

    customer_channels = (
        (
            await session.execute(
                select(ContactChannel).where(
                    ContactChannel.user_id == user_id,
                    ContactChannel.lead_contact_id == customer_contact.id,
                )
            )
        )
        .scalars()
        .all()
    )
    assert {row.channel for row in customer_channels} == {'email', 'wechat'}
    await session.refresh(form)
    assert form.contact_private_profile_id
    assert form.contact_channel_ids
    form_channel = await session.get(ContactChannel, form.contact_channel_ids[0])
    assert form_channel is not None
    assert form_channel.lead_contact_id == form_contact.id
    assert form_channel.lawful_basis == 'explicit_form_consent'
    assert form_channel.consent_ref == 'privacy_notice:2026-07'
    assert customer.email == customer_email
    assert customer.wechat == customer_wechat
    assert form.email == form_email


async def test_migration_dry_run_advances_cursor_without_writes(
    session: AsyncSession,
) -> None:
    """默认演练只给出游标和分类，不生成密文或隔离行。"""
    user_id = 98_300_000 + int(uuid4().int % 100_000)
    contact = await _public_contact(
        session,
        user_id=user_id,
        suffix=uuid4().hex[:8],
        with_channels=True,
    )

    result = await growth_pii_migration_service.migrate_batch(
        session,
        keyring=require_growth_pii_keyring(),
        source_table='contact',
        after_id=contact.id - 1,
        batch_size=1,
        dry_run=True,
    )
    assert result.scanned == 1
    assert result.migrated == 1
    assert result.quarantined == 0
    assert result.next_cursor == contact.id
    assert (
        await session.execute(
            select(func.count())
            .select_from(ContactPrivateProfile)
            .where(ContactPrivateProfile.lead_contact_id == contact.id)
        )
    ).scalar_one() == 0
    assert (
        await session.execute(
            select(func.count())
            .select_from(GrowthPiiMigrationQuarantine)
            .where(GrowthPiiMigrationQuarantine.user_id_hint == user_id)
        )
    ).scalar_one() == 0


async def test_form_payload_only_classifies_known_pii_without_copying_plaintext(
    session: AsyncSession,
) -> None:
    """普通业务载荷跳过；已知字段和自由文本 PII 只进入无明文复核隔离项。"""
    user_id = 98_350_000 + int(uuid4().int % 100_000)
    clean_form = FormSubmission(
        user_id=user_id,
        status='pending',
        owner_scope='personal',
        payload={'message_received': True, 'context': {'campaign': 'summer'}},
    )
    payload_email = f'payload-{uuid4().hex[:8]}@example.com'
    pii_form = FormSubmission(
        user_id=user_id,
        status='pending',
        privacy_notice_version='2026-07',
        consent_purpose='sales_contact',
        consent_source='landing_form',
        consent_at=timezone.now(),
        owner_scope='personal',
        payload={'context': {'email': payload_email}},
    )
    free_text_email = f'free-text-{uuid4().hex[:8]}@example.com'
    free_text_phone = '+86 138 0013 8123'
    free_text_form = FormSubmission(
        user_id=user_id,
        status='pending',
        privacy_notice_version='2026-07',
        consent_purpose='sales_contact',
        consent_source='landing_form',
        consent_at=timezone.now(),
        owner_scope='personal',
        payload={
            'message': f'请联系 {free_text_email}',
            'context': {'note': f'姓名：张三；也可致电 {free_text_phone}；微信：wx_owner_123'},
        },
    )
    session.add_all([clean_form, pii_form, free_text_form])
    await session.flush()

    result = await growth_pii_migration_service.migrate_batch(
        session,
        keyring=require_growth_pii_keyring(),
        source_table='form_submission',
        after_id=clean_form.id - 1,
        batch_size=3,
        dry_run=False,
    )

    assert result.scanned == 3
    assert result.skipped == 1
    assert result.quarantined == 2
    quarantines = (
        (
            await session.execute(
                select(GrowthPiiMigrationQuarantine)
                .where(
                    GrowthPiiMigrationQuarantine.source_table == 'form_submission',
                    GrowthPiiMigrationQuarantine.source_record_id.in_((str(pii_form.id), str(free_text_form.id))),
                )
                .order_by(GrowthPiiMigrationQuarantine.source_record_id)
            )
        )
        .scalars()
        .all()
    )
    assert len(quarantines) == 2
    assert {row.reason_code for row in quarantines} == {'form_payload_requires_review'}
    assert {tuple(row.field_names) for row in quarantines} == {
        ('payload.email',),
        (
            'payload.free_text_email',
            'payload.free_text_name',
            'payload.free_text_phone',
            'payload.free_text_wechat',
        ),
    }
    serialized = json.dumps(
        {
            'rows': [
                {
                    'field_names': row.field_names,
                    'pii_fingerprint': row.pii_fingerprint,
                }
                for row in quarantines
            ]
        },
        ensure_ascii=False,
    )
    assert payload_email not in serialized
    assert free_text_email not in serialized
    assert free_text_phone not in serialized


async def test_rejected_consent_and_cross_owner_customer_link_cannot_authorize_migration(
    session: AsyncSession,
) -> None:
    """拒绝表单不能授权客户迁移，表单也不能借他户客户关系解析联系人。"""
    owner_user_id = 98_400_000 + int(uuid4().int % 100_000)
    other_user_id = 98_500_000 + int(uuid4().int % 100_000)
    contact = LeadContact(
        lead_no=f'XOWN{uuid4().hex[:8]}',
        pool_visibility='private',
        source_type='inbound_form',
        status='valid',
        confidence_score=Decimal(50),
        normalization_version='legacy-v1',
        meta_data={},
    )
    session.add(contact)
    await session.flush()
    customer_email = f'cross-customer-{uuid4().hex[:8]}@example.com'
    customer = Customer(
        customer_no=f'XCUS{uuid4().hex[:8]}',
        user_id=other_user_id,
        lead_contact_id=contact.id,
        source_kind='inbound_form',
        email=customer_email,
        lifecycle_status='active',
        owner_scope='personal',
    )
    session.add(customer)
    await session.flush()
    session.add(
        FormSubmission(
            user_id=other_user_id,
            status='spam',
            customer_id=customer.id,
            privacy_notice_version='2026-07',
            consent_purpose='sales_contact',
            consent_source='landing_form',
            consent_at=timezone.now(),
            owner_scope='personal',
            payload={'message_received': True},
        )
    )
    form_email = f'cross-form-{uuid4().hex[:8]}@example.com'
    cross_owner_form = FormSubmission(
        user_id=owner_user_id,
        email=form_email,
        status='pending',
        customer_id=customer.id,
        privacy_notice_version='2026-07',
        consent_purpose='sales_contact',
        consent_source='landing_form',
        consent_at=timezone.now(),
        owner_scope='personal',
        payload={'message_received': True},
    )
    session.add(cross_owner_form)
    await session.flush()

    customer_result = await growth_pii_migration_service.migrate_batch(
        session,
        keyring=require_growth_pii_keyring(),
        source_table='customer',
        after_id=customer.id - 1,
        batch_size=1,
        dry_run=True,
    )
    form_result = await growth_pii_migration_service.migrate_batch(
        session,
        keyring=require_growth_pii_keyring(),
        source_table='form_submission',
        after_id=cross_owner_form.id - 1,
        batch_size=1,
        dry_run=True,
    )

    assert customer_result.migrated == 0
    assert customer_result.quarantined == 1
    assert form_result.migrated == 0
    assert form_result.quarantined == 1


async def test_blank_privacy_notice_cannot_authorize_customer_or_form_migration(
    session: AsyncSession,
) -> None:
    """纯空白隐私声明版本不能构成客户或表单迁移的明确同意。"""
    user_id = 98_550_000 + int(uuid4().int % 100_000)
    contact = LeadContact(
        lead_no=f'BPN{uuid4().hex[:8]}',
        pool_visibility='private',
        source_type='inbound_form',
        status='valid',
        confidence_score=Decimal(50),
        normalization_version='legacy-v1',
        meta_data={},
    )
    session.add(contact)
    await session.flush()
    customer = Customer(
        customer_no=f'BPC{uuid4().hex[:8]}',
        user_id=user_id,
        lead_contact_id=contact.id,
        source_kind='inbound_form',
        email=f'blank-customer-{uuid4().hex[:8]}@example.com',
        lifecycle_status='active',
        owner_scope='personal',
    )
    session.add(customer)
    await session.flush()
    form = FormSubmission(
        user_id=user_id,
        email=f'blank-form-{uuid4().hex[:8]}@example.com',
        status='pending',
        customer_id=customer.id,
        lead_contact_id=contact.id,
        privacy_notice_version='   ',
        consent_purpose='sales_contact',
        consent_source='landing_form',
        consent_at=timezone.now(),
        owner_scope='personal',
        payload={'message_received': True},
    )
    session.add(form)
    await session.flush()

    customer_result = await growth_pii_migration_service.migrate_batch(
        session,
        keyring=require_growth_pii_keyring(),
        source_table='customer',
        after_id=customer.id - 1,
        batch_size=1,
        dry_run=False,
    )
    form_result = await growth_pii_migration_service.migrate_batch(
        session,
        keyring=require_growth_pii_keyring(),
        source_table='form_submission',
        after_id=form.id - 1,
        batch_size=1,
        dry_run=False,
    )

    assert customer_result.migrated == 0
    assert customer_result.quarantined == 1
    assert form_result.migrated == 0
    assert form_result.quarantined == 1
    reasons = (
        (
            await session.execute(
                select(GrowthPiiMigrationQuarantine.reason_code).where(
                    GrowthPiiMigrationQuarantine.source_record_id.in_(
                        (str(customer.id), str(form.id))
                    )
                )
            )
        )
        .scalars()
        .all()
    )
    assert set(reasons) == {'lawful_basis_unproven', 'form_consent_unproven'}


async def test_customer_consent_cannot_cross_linked_contact(
    session: AsyncSession,
) -> None:
    """同一 Owner 下绑定其他联系人的表单不能授权迁移当前客户。"""
    user_id = 98_575_000 + int(uuid4().int % 100_000)
    customer_contact = LeadContact(
        lead_no=f'CCA{uuid4().hex[:8]}',
        pool_visibility='private',
        source_type='inbound_form',
        status='valid',
        confidence_score=Decimal(50),
        normalization_version='legacy-v1',
        meta_data={},
    )
    consent_contact = LeadContact(
        lead_no=f'CCB{uuid4().hex[:8]}',
        pool_visibility='private',
        source_type='inbound_form',
        status='valid',
        confidence_score=Decimal(50),
        normalization_version='legacy-v1',
        meta_data={},
    )
    session.add_all([customer_contact, consent_contact])
    await session.flush()
    customer = Customer(
        customer_no=f'CCC{uuid4().hex[:8]}',
        user_id=user_id,
        lead_contact_id=customer_contact.id,
        source_kind='inbound_form',
        email=f'contact-a-{uuid4().hex[:8]}@example.com',
        lifecycle_status='active',
        owner_scope='personal',
    )
    session.add(customer)
    await session.flush()
    session.add(
        FormSubmission(
            user_id=user_id,
            status='pending',
            customer_id=customer.id,
            lead_contact_id=consent_contact.id,
            privacy_notice_version='2026-07',
            consent_purpose='sales_contact',
            consent_source='landing_form',
            consent_at=timezone.now(),
            owner_scope='personal',
            payload={'message_received': True},
        )
    )
    await session.flush()

    result = await growth_pii_migration_service.migrate_batch(
        session,
        keyring=require_growth_pii_keyring(),
        source_table='customer',
        after_id=customer.id - 1,
        batch_size=1,
        dry_run=False,
    )

    assert result.migrated == 0
    assert result.quarantined == 1
    quarantine = (
        await session.execute(
            select(GrowthPiiMigrationQuarantine).where(
                GrowthPiiMigrationQuarantine.source_table == 'customer',
                GrowthPiiMigrationQuarantine.source_record_id == str(customer.id),
            )
        )
    ).scalar_one()
    assert quarantine.reason_code == 'lawful_basis_unproven'


async def test_blank_public_source_url_cannot_authorize_contact_migration(
    session: AsyncSession,
) -> None:
    """空白来源 URL 不构成可追溯公开商业来源证据。"""
    user_id = 98_600_000 + int(uuid4().int % 100_000)
    contact = LeadContact(
        lead_no=f'BLANK{uuid4().hex[:8]}',
        pool_visibility='public',
        contact_name='空白来源联系人',
        email=f'blank-{uuid4().hex[:8]}@example.com',
        source_type='web',
        source_url='   ',
        status='valid',
        confidence_score=Decimal(50),
        normalization_version='legacy-v1',
        meta_data={},
    )
    session.add(contact)
    await session.flush()
    session.add_all([
        LeadRef(
            user_id=user_id,
            lead_contact_id=contact.id,
            source='collect',
            status='new',
        ),
        LeadContactSource(
            lead_contact_id=contact.id,
            source_type='web',
            source_url='   ',
            match_dimension='new',
            seen_at=timezone.now(),
            meta_data={},
        ),
    ])
    await session.flush()

    result = await growth_pii_migration_service.migrate_batch(
        session,
        keyring=require_growth_pii_keyring(),
        source_table='contact',
        after_id=contact.id - 1,
        batch_size=1,
        dry_run=True,
    )

    assert result.migrated == 0
    assert result.quarantined == 1
