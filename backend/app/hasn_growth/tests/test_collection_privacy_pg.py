"""获客采集新写的 PII 单一事实源真实 PostgreSQL 测试。

采集原文只在进程内完成清洗；数据库只保留公开商业事实、无明文指纹和 Owner 私有密文。
需要 export DATABASE_PORT=15432。
"""

from __future__ import annotations

import json

from pathlib import Path
from typing import TYPE_CHECKING
from uuid import uuid4

import pytest
import pytest_asyncio

from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

from backend.app.hasn_growth.model.contact_channel import ContactChannel
from backend.app.hasn_growth.model.contact_private_profile import ContactPrivateProfile
from backend.app.hasn_growth.model.lead_collection_job import LeadCollectionJob
from backend.app.hasn_growth.model.lead_contact import LeadContact
from backend.app.hasn_growth.model.lead_contact_source import LeadContactSource
from backend.app.hasn_growth.model.lead_firecrawl_request import LeadFirecrawlRequest
from backend.app.hasn_growth.model.lead_raw_record import LeadRawRecord
from backend.app.hasn_growth.model.lead_rejected_record import LeadRejectedRecord
from backend.app.hasn_growth.schema.business import CreateLeadJobParam
from backend.app.hasn_growth.service.business_service import lead_automation_business_service
from backend.app.hasn_growth.service.contact_privacy_service import contact_privacy_service
from backend.app.hasn_growth.service.funnel_service import growth_funnel_service
from backend.app.hasn_growth.service.industry_tagging_service import IndustryTaggingService
from backend.app.hasn_growth.service.pii_keyring import require_growth_pii_keyring
from backend.app.hasn_growth.service.provider_registry import CrawledItem
from backend.common.exception import errors
from backend.database.db import SQLALCHEMY_DATABASE_URL

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


async def test_collection_persists_only_public_projection_and_private_ciphertext(
    session: AsyncSession,
) -> None:
    """有效采集项不得在原始表、来源表或公共联系人复制姓名和联系方式。"""
    suffix = uuid4().hex[:8]
    user_id = 98_700_000 + int(uuid4().int % 100_000)
    email = f'zhang-{suffix}@acme.test'
    phone = '13800138000'
    address = '北京市海淀区测试路 1 号'
    contact_name = '张三'
    job = LeadCollectionJob(
        job_no=f'PRIV{suffix}',
        keyword='CRM 企业服务',
        source_types=['public_web'],
        user_id=user_id,
        status='running',
        max_pages=1,
        max_results=10,
        request_config={},
        meta_data={},
    )
    session.add(job)
    await session.flush()
    item = CrawledItem(
        source_type='public_web',
        source_url=f'https://acme-{suffix}.test/contact?email={email}',
        title=f'联系 {contact_name}',
        markdown=f'联系人：{contact_name}，邮箱 {email}，电话 {phone}，地址 {address}',
        raw_text=f'{contact_name}|{email}|{phone}|{address}',
        raw_html=f'<p>{contact_name} {email} {phone} {address}</p>',
        raw_payload={'contact_name': contact_name, 'email': email, 'phone': phone},
        structured_payload={
            'company_name': f'Acme {suffix}',
            'contact_name': contact_name,
            'emails': [email],
            'phones': [phone],
            'address': address,
            'website': f'https://acme-{suffix}.test',
            'industry': '企业服务',
        },
        llm_confidence=0.92,
        extract_mode='llm_backend',
        metadata={
            'llm_schema_version': 'lead_v1',
            'llm_prompt_version': 'lead_extract_v1',
            'contact_hint': email,
        },
    )

    result = await lead_automation_business_service._ingest_crawled_item(
        session,
        job=job,
        item=item,
        keyring=require_growth_pii_keyring(),
        tagger=IndustryTaggingService(session, enable_llm=False),
    )

    assert result['created'] is True
    assert result['rejected'] is False
    raw_record = await session.get(LeadRawRecord, result['raw_record_id'])
    contact = await session.get(LeadContact, result['contact_id'])
    assert raw_record is not None and contact is not None
    assert raw_record.markdown is None
    assert raw_record.raw_text is None
    assert raw_record.raw_html is None
    assert raw_record.raw_payload is None
    assert raw_record.structured_payload is None
    assert contact.contact_name is None
    assert contact.email is None and contact.email_normalized is None
    assert contact.phone is None and contact.phone_normalized is None
    assert contact.address is None
    assert contact.dedupe_key_email is None and contact.dedupe_key_phone is None

    request = (
        await session.execute(
            select(LeadFirecrawlRequest).where(LeadFirecrawlRequest.id == raw_record.firecrawl_request_id)
        )
    ).scalar_one()
    source = (
        await session.execute(
            select(LeadContactSource).where(LeadContactSource.raw_record_id == raw_record.id)
        )
    ).scalar_one()
    serialized_public = json.dumps(
        {
            'raw': {
                'title': raw_record.title,
                'metadata': raw_record.meta_data,
                'content_hash': raw_record.content_hash,
            },
            'request': {
                'target_url': request.target_url,
                'request_payload': request.request_payload,
                'metadata': request.meta_data,
            },
            'source': {
                'source_url': source.source_url,
                'metadata': source.meta_data,
            },
            'contact': {
                'metadata': contact.meta_data,
                'source_url': contact.source_url,
                'keyword': contact.keyword,
            },
        },
        ensure_ascii=False,
    )
    for plaintext in (contact_name, email, phone, address):
        assert plaintext not in serialized_public

    profile = (
        await session.execute(
            select(ContactPrivateProfile).where(
                ContactPrivateProfile.user_id == user_id,
                ContactPrivateProfile.lead_contact_id == contact.id,
            )
        )
    ).scalar_one()
    masked = await contact_privacy_service.get_masked_contact(
        session,
        keyring=require_growth_pii_keyring(),
        private_profile_id=profile.id,
        owner_scope='personal',
        user_id=user_id,
        enterprise_id=None,
    )
    assert masked['contact_name'] == '张*'
    assert {channel['channel'] for channel in masked['channels']} == {
        'email',
        'phone',
        'postal_address',
    }
    assert (
        await session.execute(
            select(ContactChannel.id).where(ContactChannel.private_profile_id == profile.id)
        )
    ).scalars().all()


async def test_rejected_collection_does_not_persist_contact_plaintext(
    session: AsyncSession,
) -> None:
    """无效采集项只保留拒绝原因、字段名和带密钥指纹。"""
    suffix = uuid4().hex[:8]
    user_id = 98_800_000 + int(uuid4().int % 100_000)
    email = f'invalid-{suffix}@example.com'
    phone = '12345'
    job = LeadCollectionJob(
        job_no=f'REJ{suffix}',
        keyword='无效采集',
        source_types=['public_web'],
        user_id=user_id,
        status='running',
        max_pages=1,
        max_results=10,
        request_config={},
        meta_data={},
    )
    session.add(job)
    await session.flush()
    item = CrawledItem(
        source_type='public_web',
        source_url=f'https://reject-{suffix}.test/contact',
        markdown=f'邮箱 {email} 电话 {phone}',
        raw_payload={'email': email, 'phone': phone},
        structured_payload={'emails': [email], 'phones': [phone]},
        metadata={'candidate': email},
    )

    result = await lead_automation_business_service._ingest_crawled_item(
        session,
        job=job,
        item=item,
        keyring=require_growth_pii_keyring(),
        tagger=IndustryTaggingService(session, enable_llm=False),
    )

    assert result['rejected'] is True
    rejected = (
        await session.execute(
            select(LeadRejectedRecord).where(LeadRejectedRecord.raw_record_id == result['raw_record_id'])
        )
    ).scalar_one()
    assert rejected.email is None
    assert rejected.phone is None
    assert rejected.raw_excerpt is None
    serialized = json.dumps(
        {
            'error_message': rejected.error_message,
            'metadata': rejected.meta_data,
        },
        ensure_ascii=False,
    )
    assert email not in serialized
    assert phone not in serialized
    assert rejected.meta_data['pii_fields']
    assert rejected.meta_data['pii_fingerprint'].startswith('v2:')


async def test_collection_job_rejects_pii_keyword_and_uncontrolled_config(
    session: AsyncSession,
) -> None:
    """任务入口必须在持久化前拒绝 PII 查询词和未声明配置。"""
    with pytest.raises(errors.RequestError):
        await lead_automation_business_service.create_job(
            session,
            CreateLeadJobParam(
                keyword='sales@example.com',
                request_config={},
            ),
        )
    with pytest.raises(errors.RequestError):
        await lead_automation_business_service.create_job(
            session,
            CreateLeadJobParam(
                keyword='企业服务',
                request_config={'authorization': 'sales@example.com'},
            ),
        )


async def test_collection_job_sanitizes_url_keyword_and_persists_allowlisted_config(
    session: AsyncSession,
) -> None:
    """URL 任务去掉凭据、查询和片段，运行配置只保存受控字段。"""
    created = await lead_automation_business_service.create_job(
        session,
        CreateLeadJobParam(
            keyword='https://user:password@example.test/directory?q=CRM#staff',
            request_config={
                'country_hint': 'CN',
                'required_contact_fields': ['email'],
                'firecrawl_options': {
                    'extract_mode': 'extract',
                    'schema_version': 'lead_v1',
                    'prompt_version': 'lead_extract_v1',
                    'search_limit': 5,
                },
            },
        ),
    )
    job = await session.get(LeadCollectionJob, created['id'])
    assert job is not None
    assert job.keyword == 'https://example.test/directory'
    assert job.request_config == {
        'country_hint': 'CN',
        'required_contact_fields': ['email'],
        'firecrawl_options': {
            'extract_mode': 'extract',
            'schema_version': 'lead_v1',
            'prompt_version': 'lead_extract_v1',
            'search_limit': 5,
        },
    }
    serialized = json.dumps(model_to_safe_job(job), ensure_ascii=False)
    assert 'user' not in serialized
    assert 'password' not in serialized
    assert 'q=CRM' not in serialized
    assert 'staff' not in serialized


def model_to_safe_job(job: LeadCollectionJob) -> dict[str, object]:
    """测试辅助：只序列化本用例关注的任务输入字段。"""
    return {
        'keyword': job.keyword,
        'request_config': job.request_config,
    }


async def test_domain_only_dedupe_never_merges_private_people(
    session: AsyncSession,
) -> None:
    """同域名的不同人或不同 Owner 必须拥有不同公共锚点和私有资料。"""
    suffix = uuid4().hex[:8]
    website = f'https://same-{suffix}.example.test'
    first = await growth_funnel_service.create_manual_lead(
        session,
        user_id=98_910_001,
        company_name='同域名公司',
        contact_name='甲联系人',
        email=f'alpha-{suffix}@example.test',
        website=(
            f'https://operator:secret@same-{suffix}.example.test'
            f'/directory?email=alpha-{suffix}@example.test#person'
        ),
    )
    same_owner_other_person = await growth_funnel_service.create_manual_lead(
        session,
        user_id=98_910_001,
        company_name='同域名公司',
        contact_name='乙联系人',
        email=f'beta-{suffix}@example.test',
        website=website,
    )
    other_owner = await growth_funnel_service.create_manual_lead(
        session,
        user_id=98_910_002,
        company_name='同域名公司',
        contact_name='丙联系人',
        email=f'gamma-{suffix}@example.test',
        website=website,
    )
    assert len({
        first['lead_contact_id'],
        same_owner_other_person['lead_contact_id'],
        other_owner['lead_contact_id'],
    }) == 3
    contacts = (
        await session.execute(
            select(LeadContact).where(
                LeadContact.id.in_({
                    first['lead_contact_id'],
                    same_owner_other_person['lead_contact_id'],
                    other_owner['lead_contact_id'],
                })
            )
        )
    ).scalars().all()
    assert all(contact.dedupe_key_domain is None for contact in contacts)
    contacts_by_id = {contact.id: contact for contact in contacts}
    assert contacts_by_id[first['lead_contact_id']].website == f'{website}/directory'
    assert contacts_by_id[same_owner_other_person['lead_contact_id']].website == f'{website}/'
    assert contacts_by_id[other_owner['lead_contact_id']].website == f'{website}/'
