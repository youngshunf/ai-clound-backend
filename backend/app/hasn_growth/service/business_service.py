from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal
from typing import Any

import sqlalchemy as sa

from sqlalchemy.ext.asyncio import AsyncSession

from backend.app.hasn_growth.model import (
    LeadAuditLog,
    LeadCollectionJob,
    LeadContact,
    LeadContactSource,
    LeadExportBatch,
    LeadExportItem,
    LeadFirecrawlRequest,
    LeadRawRecord,
    LeadRejectedRecord,
    LeadSourceConfig,
)
from backend.app.hasn_growth.schema.business import CreateLeadJobParam
from backend.app.hasn_growth.service.cleaner_service import clean_raw_record
from backend.app.hasn_growth.service.dedupe_service import dedupe_key
from backend.app.hasn_growth.service.export_service import build_csv_export
from backend.app.hasn_growth.service.firecrawl_client import FirecrawlClient
from backend.app.hasn_growth.service.metering_service import growth_metering_service
from backend.app.hasn_growth.service.provider_registry import CrawlRequest, get_provider
from backend.common.exception import errors


class LeadAutomationBusinessService:
    def __init__(self, firecrawl_client: FirecrawlClient | None = None) -> None:
        self.firecrawl_client = firecrawl_client or FirecrawlClient()

    async def create_job(self, db: AsyncSession, obj: CreateLeadJobParam) -> dict[str, Any]:
        user_id = None if obj.lead_scope == 'public' else obj.user_id
        if obj.lead_scope == 'user' and user_id is None:
            user_id = 0
        job = LeadCollectionJob(
            job_no=f'LAJ{datetime.now(UTC).strftime("%Y%m%d%H%M%S%f")}',
            keyword=obj.keyword,
            source_types=obj.source_types,
            lead_scope=obj.lead_scope,
            user_id=user_id,
            status='pending',
            max_pages=obj.max_pages,
            max_results=obj.max_results,
            request_config=obj.request_config,
            meta_data={},
        )
        db.add(job)
        await db.flush()
        return model_to_dict(job)

    async def run_job(self, db: AsyncSession, job_id: int, *, user_id: int | None = None, admin: bool = False) -> dict[str, Any]:  # noqa: C901
        job = await db.get(LeadCollectionJob, job_id)
        if job is None:
            raise errors.NotFoundError(msg='采集任务不存在')
        if not admin and user_id is not None and job.lead_scope != 'public' and job.user_id != user_id:
            raise errors.ForbiddenError(msg='无权执行该采集任务')
        job.status = 'running'
        job.started_at = datetime.now(UTC)
        request_count = 0
        for source_type in _as_list(job.source_types):
            request_count += 1
            try:
                provider = get_provider(source_type)
                items = await provider.crawl(
                    CrawlRequest(
                        job_id=job.id,
                        keyword=job.keyword,
                        source_type=source_type,
                        lead_scope=job.lead_scope,
                        user_id=job.user_id,
                        max_pages=job.max_pages,
                        max_results=job.max_results,
                        config=job.request_config or {},
                    ),
                    firecrawl_client=self.firecrawl_client,
                )
            except Exception as exc:
                await self._persist_rejected(
                    db,
                    job_id=job.id,
                    source_type=source_type,
                    reason='firecrawl_failed',
                    error_message=str(exc),
                )
                job.firecrawl_failed_count += 1
                continue

            job.firecrawl_success_count += 1
            job.total_found += len(items)
            for item in items[: job.max_results]:
                firecrawl_request = LeadFirecrawlRequest(
                    job_id=job.id,
                    source_type=item.source_type,
                    endpoint='/v1/extract' if item.extract_mode == 'extract' else '/v1/scrape',
                    target_url=item.source_url,
                    request_payload=_redact_request_payload({'keyword': job.keyword, **(job.request_config or {})}),
                    extract_mode=item.extract_mode or 'scrape_json',
                    llm_schema_version=item.metadata.get('llm_schema_version'),
                    llm_prompt_version=item.metadata.get('llm_prompt_version'),
                    status='succeeded',
                    attempt_count=item.metadata.get('attempt_count') or 1,
                    result_count=1,
                    meta_data=dict(item.metadata),
                )
                db.add(firecrawl_request)
                await db.flush()

                raw_html, raw_html_metadata = _raw_html_policy(
                    item.raw_html,
                    persist=bool((job.request_config or {}).get('persist_raw_html', False)),
                    max_bytes=int((job.request_config or {}).get('max_html_bytes', 524288)),
                    raw_record_id=firecrawl_request.id,
                )
                raw_dict = {
                    'job_id': job.id,
                    'source_type': item.source_type,
                    'source_url': item.source_url,
                    'domain': _domain(item.source_url),
                    'title': item.title,
                    'markdown': item.markdown,
                    'raw_text': item.raw_text,
                    'raw_html': raw_html,
                    'raw_payload': item.raw_payload,
                    'structured_payload': item.structured_payload,
                    'llm_confidence': item.llm_confidence,
                    'extract_mode': item.extract_mode,
                    'content_hash': _sha256((item.markdown or item.raw_text or item.source_url or '').strip().lower()),
                    'metadata': {**item.metadata, **raw_html_metadata},
                }
                raw_record = LeadRawRecord(
                    job_id=job.id,
                    firecrawl_request_id=firecrawl_request.id,
                    source_type=item.source_type,
                    source_url=item.source_url,
                    domain=raw_dict['domain'],
                    title=item.title,
                    markdown=item.markdown,
                    raw_text=item.raw_text,
                    raw_html=raw_html,
                    raw_payload=item.raw_payload,
                    structured_payload=item.structured_payload,
                    llm_confidence=Decimal(str(item.llm_confidence)) if item.llm_confidence is not None else None,
                    content_hash=raw_dict['content_hash'],
                    normalization_version='v1',
                    status='pending',
                    meta_data=raw_dict['metadata'],
                )
                db.add(raw_record)
                await db.flush()
                job.raw_count += 1

                cleaned = clean_raw_record(
                    raw_dict,
                    min_contact_fields=_contact_field_requirements(job.request_config or {}),
                    country_hint=(job.request_config or {}).get('country_hint', 'CN'),
                )
                raw_record.system_score = Decimal(str(cleaned.system_score))
                raw_record.status = 'cleaned' if cleaned.accepted else 'invalid'
                if not cleaned.accepted:
                    await self._persist_rejected(
                        db,
                        job_id=job.id,
                        raw_record_id=raw_record.id,
                        firecrawl_request_id=firecrawl_request.id,
                        source_type=item.source_type,
                        source_url=item.source_url,
                        reason=cleaned.rejected_reason or 'missing_contact',
                        email=cleaned.email,
                        phone=cleaned.phone,
                        metadata=cleaned.metadata,
                    )
                    job.invalid_count += 1
                    continue

                created, contact, match_dimension = await self._upsert_contact(
                    db,
                    cleaned=cleaned,
                    lead_scope=job.lead_scope,
                    user_id=job.user_id,
                    keyword=job.keyword,
                )
                db.add(
                    LeadContactSource(
                        lead_contact_id=contact.id,
                        raw_record_id=raw_record.id,
                        firecrawl_request_id=firecrawl_request.id,
                        source_type=item.source_type,
                        source_url=item.source_url,
                        match_dimension=match_dimension,
                        meta_data=cleaned.metadata,
                    )
                )
                if created:
                    job.valid_count += 1
                else:
                    raw_record.status = 'duplicate'
                    job.duplicate_count += 1

        job.finished_at = datetime.now(UTC)
        job.status = _final_status(job, request_count)
        await db.flush()

        # 计量上报（G7：采集按量计积分，获客只报量不自建账本；best-effort 不阻断）。
        await growth_metering_service.report_crawl_usage(
            db,
            user_id=getattr(job, 'user_id', None) or user_id,
            job_id=job.id,
            success_count=job.firecrawl_success_count,
        )
        return model_to_dict(job)

    async def get_job(self, db: AsyncSession, *, job_id: int, user_id: int | None = None, admin: bool = False) -> dict[str, Any]:
        job = await db.get(LeadCollectionJob, job_id)
        if job is None:
            raise errors.NotFoundError(msg='采集任务不存在')
        if not admin and job.lead_scope != 'public' and job.user_id != user_id:
            raise errors.ForbiddenError(msg='无权访问该采集任务')
        return model_to_dict(job)

    async def list_rejected(
        self,
        db: AsyncSession,
        *,
        user_id: int | None = None,
        job_id: int | None = None,
        admin: bool = False,
    ) -> list[dict[str, Any]]:
        stmt = sa.select(LeadRejectedRecord).order_by(LeadRejectedRecord.id.desc())
        if job_id is not None:
            stmt = stmt.where(LeadRejectedRecord.job_id == job_id)
        if not admin:
            visible_jobs = sa.select(LeadCollectionJob.id).where(
                sa.or_(LeadCollectionJob.lead_scope == 'public', LeadCollectionJob.user_id == user_id)
            )
            stmt = stmt.where(LeadRejectedRecord.job_id.in_(visible_jobs))
        rows = [model_to_dict(row) for row in (await db.execute(stmt)).scalars().all()]
        for row in rows:
            row['email'] = _mask_email(row.get('email'))
            row['phone'] = _mask_phone(row.get('phone'))
        return rows

    async def list_audit_logs(
        self,
        db: AsyncSession,
        *,
        event_type: str | None = None,
        actor_user_id: int | None = None,
        target_table: str | None = None,
        limit: int = 100,
    ) -> list[dict[str, Any]]:
        stmt = sa.select(LeadAuditLog).order_by(LeadAuditLog.id.desc()).limit(max(1, min(limit, 500)))
        if event_type:
            stmt = stmt.where(LeadAuditLog.event_type == event_type)
        if actor_user_id is not None:
            stmt = stmt.where(LeadAuditLog.actor_user_id == actor_user_id)
        if target_table:
            stmt = stmt.where(LeadAuditLog.target_table == target_table)
        return [model_to_dict(row) for row in (await db.execute(stmt)).scalars().all()]

    async def list_contacts(
        self,
        db: AsyncSession,
        *,
        user_id: int | None = None,
        admin: bool = False,
        masked: bool = True,
    ) -> list[dict[str, Any]]:
        stmt = sa.select(LeadContact).order_by(LeadContact.id.desc())
        if not admin:
            stmt = stmt.where(sa.or_(LeadContact.lead_scope == 'public', LeadContact.user_id == user_id))
        rows = [model_to_dict(row) for row in (await db.execute(stmt)).scalars().all()]
        if masked:
            for row in rows:
                row['email'] = _mask_email(row.get('email'))
                row['phone'] = _mask_phone(row.get('phone'))
        elif admin and rows:
            db.add(
                LeadAuditLog(
                    event_type='pii_read',
                    actor_role='admin',
                    target_table='lead_contact',
                    target_count=len(rows),
                    payload={'endpoint': 'lead_automation_admin_contacts', 'lead_no_list': [row.get('lead_no') for row in rows[:100]]},
                    result='success',
                )
            )
            await db.flush()
        return rows

    async def update_blacklist(self, db: AsyncSession, payload: dict[str, Any]) -> dict[str, Any]:
        source_type = str(payload.get('source_type') or 'public_web')
        name = str(payload.get('name') or 'default')
        config = await db.scalar(
            sa.select(LeadSourceConfig).where(LeadSourceConfig.source_type == source_type, LeadSourceConfig.name == name)
        )
        if config is None:
            config = LeadSourceConfig(
                source_type=source_type,
                name=name,
                enabled=True,
                firecrawl_options={},
                min_contact_fields=['email', 'phone'],
                persist_raw_html=False,
                max_html_bytes=524288,
                domain_blacklist=[],
                country_blacklist=['DE', 'FR', 'IT', 'NL', 'ES'],
                rate_limit_per_minute=60,
                concurrency=3,
                meta_data={},
            )
            db.add(config)
            await db.flush()
        config.domain_blacklist = payload.get('domain_blacklist') or config.domain_blacklist or []
        config.country_blacklist = payload.get('country_blacklist') or config.country_blacklist or []
        db.add(
            LeadAuditLog(
                event_type='config_change',
                actor_role='admin',
                target_table='lead_source_config',
                target_count=1,
                target_ref=f'{source_type}:{name}',
                payload={
                    'source_type': source_type,
                    'name': name,
                    'domain_blacklist_count': len(config.domain_blacklist or []),
                    'country_blacklist_count': len(config.country_blacklist or []),
                },
                result='success',
            )
        )
        await db.flush()
        return model_to_dict(config)

    async def export_contacts(self, db: AsyncSession, *, user_id: int, filter_payload: dict | None = None) -> dict[str, Any]:
        today = datetime.now(UTC).date()
        start = datetime(today.year, today.month, today.day, tzinfo=UTC)
        export_count = await db.scalar(
            sa.select(sa.func.count())
            .select_from(LeadExportBatch)
            .where(LeadExportBatch.user_id == user_id, LeadExportBatch.created_time >= start)
        )
        if (export_count or 0) >= 3:
            raise ValueError('daily export limit exceeded')
        rows = await self.list_contacts(db, user_id=user_id, admin=False, masked=False)
        batch_no = f'LEX{datetime.now(UTC).strftime("%Y%m%d%H%M%S%f")}'
        result = build_csv_export(rows[:5000], batch_no=batch_no, user_id=user_id, filter_payload=filter_payload or {}, now=datetime.now(UTC))
        batch = LeadExportBatch(
            batch_no=batch_no,
            user_id=user_id,
            lead_scope='user',
            filter_payload=filter_payload or {},
            format='csv',
            total_count=result.batch['total_count'],
            file_sha256=result.batch['file_sha256'],
            status='succeeded',
            started_at=result.batch['started_at'],
            finished_at=result.batch['finished_at'],
        )
        db.add(batch)
        await db.flush()
        contact_ids = []
        for item in result.items:
            contact_ids.append(item['lead_contact_id'])
            db.add(
                LeadExportItem(
                    batch_id=batch.id,
                    lead_contact_id=item['lead_contact_id'],
                    lead_no=item['lead_no'],
                    snapshot=item['snapshot'],
                )
            )
        if contact_ids:
            await db.execute(
                sa.update(LeadContact)
                .where(LeadContact.id.in_(contact_ids))
                .values(status='exported', last_exported_at=datetime.now(UTC))
            )
        db.add(
            LeadAuditLog(
                event_type='export',
                actor_user_id=user_id,
                actor_role='app',
                target_table='lead_export_batch',
                target_count=result.batch['total_count'],
                target_ref=batch_no,
                payload=result.audit_log['payload'],
                result='success',
            )
        )
        await db.flush()
        return {'batch': model_to_dict(batch), 'items': result.items, 'csv': result.csv_text}

    async def archive_expired(self, db: AsyncSession) -> int:
        result = await db.execute(
            sa.select(LeadContact).where(
                LeadContact.archived_at <= datetime.now(UTC),
                LeadContact.status.notin_(['contacted', 'exported']),
            )
        )
        contacts = result.scalars().all()
        for contact in contacts:
            contact.status = 'archived'
            contact.email = None
            contact.phone = None
            contact.email_normalized = None
            contact.phone_normalized = None
        if contacts:
            db.add(
                LeadAuditLog(
                    event_type='archive_run',
                    actor_role='system',
                    target_table='lead_contact',
                    target_count=len(contacts),
                    payload={'archived_count': len(contacts)},
                    result='success',
                )
            )
        await db.flush()
        return len(contacts)

    async def extend_retention(self, db: AsyncSession, *, contact_id: int) -> dict[str, Any]:
        from datetime import timedelta

        contact = await db.get(LeadContact, contact_id)
        if contact is None:
            raise errors.NotFoundError(msg='线索不存在')
        contact.archived_at = datetime.now(UTC) + timedelta(days=548)
        db.add(
            LeadAuditLog(
                event_type='config_change',
                actor_role='admin',
                target_table='lead_contact',
                target_count=1,
                target_ref=contact.lead_no,
                payload={'action': 'extend_retention', 'contact_id': contact_id},
                result='success',
            )
        )
        await db.flush()
        return model_to_dict(contact)

    async def dsr_delete_by_email(self, db: AsyncSession, *, emails: list[str], request_id: str | None = None) -> dict[str, Any]:
        from backend.app.hasn_growth.service.cleaner_service import normalize_email

        normalized = [email for email in (normalize_email(email) for email in emails) if email]
        contacts = (
            (await db.execute(sa.select(LeadContact).where(LeadContact.email_normalized.in_(normalized))))
            .scalars()
            .all()
        )
        for contact in contacts:
            contact.email = None
            contact.email_normalized = None
        audit = LeadAuditLog(
            event_type='dsr_delete_email',
            actor_role='admin',
            target_table='lead_contact',
            target_count=len(contacts),
            payload={'request_id': request_id, 'target_emails_sha256': [_sha256(value) for value in normalized]},
            result='success',
        )
        db.add(audit)
        await db.flush()
        return model_to_dict(audit)

    async def dsr_delete_by_phone(
        self,
        db: AsyncSession,
        *,
        phones: list[str],
        country_hint: str = 'CN',
        request_id: str | None = None,
    ) -> dict[str, Any]:
        from backend.app.hasn_growth.service.cleaner_service import normalize_phone

        normalized = [phone for phone in (normalize_phone(phone, country_hint=country_hint) for phone in phones) if phone]
        contacts = (
            (await db.execute(sa.select(LeadContact).where(LeadContact.phone_normalized.in_(normalized))))
            .scalars()
            .all()
        )
        for contact in contacts:
            contact.phone = None
            contact.phone_normalized = None
        audit = LeadAuditLog(
            event_type='dsr_delete_phone',
            actor_role='admin',
            target_table='lead_contact',
            target_count=len(contacts),
            payload={'request_id': request_id, 'target_phones_sha256': [_sha256(value) for value in normalized]},
            result='success',
        )
        db.add(audit)
        await db.flush()
        return model_to_dict(audit)

    async def _upsert_contact(self, db: AsyncSession, *, cleaned, lead_scope: str, user_id: int | None, keyword: str):
        keys = {
            'email': dedupe_key(cleaned.email_normalized, lead_scope=lead_scope, user_id=user_id),
            'phone': dedupe_key(cleaned.phone_normalized, lead_scope=lead_scope, user_id=user_id),
            'domain': dedupe_key(cleaned.domain, lead_scope=lead_scope, user_id=user_id),
        }
        for dimension in ('email', 'phone', 'domain'):
            key = keys[dimension]
            if not key:
                continue
            existing = await db.scalar(sa.select(LeadContact).where(getattr(LeadContact, f'dedupe_key_{dimension}') == key))
            if existing is not None:
                existing.last_seen_at = datetime.now(UTC)
                existing.confidence_score = max(existing.confidence_score, Decimal(str(cleaned.system_score)))
                return False, existing, dimension
        contact = LeadContact(
            lead_no=f'LEAD{datetime.now(UTC).strftime("%Y%m%d%H%M%S%f")}',
            lead_scope=lead_scope,
            user_id=user_id,
            company_name=cleaned.company_name,
            contact_name=cleaned.contact_name,
            email=cleaned.email,
            email_normalized=cleaned.email_normalized,
            phone=cleaned.phone,
            phone_normalized=cleaned.phone_normalized,
            website=cleaned.website,
            domain=cleaned.domain,
            country=cleaned.country,
            region=cleaned.region,
            city=cleaned.city,
            address=cleaned.address,
            industry=cleaned.industry,
            source_type=cleaned.source_type,
            source_url=cleaned.source_url,
            keyword=keyword,
            status='new',
            confidence_score=Decimal(str(cleaned.system_score)),
            dedupe_key_email=keys['email'],
            dedupe_key_phone=keys['phone'],
            dedupe_key_domain=keys['domain'],
            normalization_version=cleaned.normalization_version,
            meta_data=cleaned.metadata,
        )
        db.add(contact)
        await db.flush()
        return True, contact, 'new'

    async def _persist_rejected(self, db: AsyncSession, *, job_id: int, reason: str, **kwargs: Any) -> None:
        db.add(
            LeadRejectedRecord(
                job_id=job_id,
                raw_record_id=kwargs.get('raw_record_id'),
                firecrawl_request_id=kwargs.get('firecrawl_request_id'),
                source_type=kwargs.get('source_type'),
                source_url=kwargs.get('source_url'),
                reason=reason,
                email=kwargs.get('email'),
                phone=kwargs.get('phone'),
                error_message=kwargs.get('error_message'),
                meta_data=kwargs.get('metadata') or {},
            )
        )


lead_automation_business_service = LeadAutomationBusinessService()


def model_to_dict(model: Any) -> dict[str, Any]:
    data = {}
    for column in model.__table__.columns:
        value = getattr(model, column.key)
        data['metadata' if column.key == 'meta_data' else column.name] = value
    return data


def _as_list(value: Any) -> list[str]:
    if isinstance(value, list):
        return [str(item) for item in value]
    if isinstance(value, tuple | set):
        return [str(item) for item in value]
    if isinstance(value, dict):
        return [str(item) for item in value.values()]
    return [str(value)] if value else []


def _final_status(job: LeadCollectionJob, request_count: int) -> str:
    if request_count > 0 and job.firecrawl_failed_count == request_count:
        return 'failed'
    if job.firecrawl_success_count > 0 and job.firecrawl_failed_count > 0:
        return 'partial_succeeded'
    return 'succeeded'


def _sha256(value: str) -> str:
    import hashlib

    return hashlib.sha256(value.encode('utf-8')).hexdigest()


def _domain(url: str | None) -> str | None:
    if not url:
        return None
    from urllib.parse import urlparse

    return urlparse(url).netloc.removeprefix('www.').lower()


def _redact_request_payload(payload: dict[str, Any]) -> dict[str, Any]:
    return {
        key: ('***' if any(secret in key.lower() for secret in ('api_key', 'authorization', 'token', 'proxy', 'password')) else value)
        for key, value in payload.items()
    }


def _raw_html_policy(raw_html: str | None, *, persist: bool, max_bytes: int, raw_record_id: int) -> tuple[str | None, dict[str, str]]:
    if not raw_html or not persist:
        return None, {}
    if len(raw_html.encode('utf-8')) <= max_bytes:
        return raw_html, {}
    digest = _sha256(raw_html)
    return None, {'raw_html_sha256': digest, 'raw_html_object_key': f'lead_raw_html/{raw_record_id}-{digest}.html'}


def _contact_field_requirements(config: dict[str, Any]) -> list[str]:
    fields = config.get('required_contact_fields') or config.get('min_contact_fields') or ['email', 'phone']
    return [str(field) for field in _as_list(fields) if str(field) in {'email', 'phone'}] or ['email', 'phone']


def _mask_email(value: str | None) -> str | None:
    if not value or '@' not in value:
        return value
    local, domain = value.split('@', 1)
    return f'{local[:1]}***@{domain}'


def _mask_phone(value: str | None) -> str | None:
    if not value or len(value) < 8:
        return value
    return f'{value[:4]}****{value[-4:]}'
