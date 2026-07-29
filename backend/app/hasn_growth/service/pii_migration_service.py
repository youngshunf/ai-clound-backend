"""获客存量 PII 的分批迁移与无明文隔离。

只迁移能同时证明个人主体和合法依据的 `contact/customer/form_submission` 行。
企业主体、人工来源、缺失同意、过期保留期或缺失联系人事实的记录只写隔离元数据；
隔离表只保存字段名和版本化 HMAC，不保存原始 PII。旧明文列在 S13 独立授权前保持
不变，以支持回滚和影子核对。
"""

from __future__ import annotations

import json
import re

from collections.abc import Iterator
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import TYPE_CHECKING, Any, Literal

import sqlalchemy as sa

from sqlalchemy.dialects.postgresql import insert as pg_insert

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
from backend.app.hasn_growth.service.contact_privacy_service import (
    ContactChannelWrite,
    contact_privacy_service,
    ensure_growth_pii_key_write_fence,
)
from backend.app.hasn_growth.service.pii_keyring import GrowthPiiKeyring
from backend.common.exception import errors
from backend.common.exception.errors import BaseExceptionError
from backend.utils.timezone import timezone

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession

MigrationSource = Literal['contact', 'customer', 'form_submission']

_PUBLIC_SOURCE_TYPES = frozenset({
    'b2b',
    'crawl',
    'enterprise',
    'firecrawl',
    'maps',
    'public_web',
    'qcc',
    'social_media',
    'web',
    'yellow_pages',
})
_PUBLIC_LEAD_REF_SOURCES = frozenset({'backfill', 'collect', 'request'})
_FORM_CONSENT_PURPOSES = frozenset({'sales_contact', 'sales_followup'})
_FORM_CONSENT_SOURCES = frozenset({'landing_form'})
_FORM_PAYLOAD_PII_KEYS = frozenset({
    'address',
    'contact_name',
    'email',
    'name',
    'phone',
    'wechat',
})
_EMAIL_IN_TEXT_PATTERN = re.compile(
    r'(?<![\w.+-])[\w.!#$%&\'*+/=?^`{|}~-]+@'
    r'(?:[A-Z0-9-]+\.)+[A-Z]{2,63}(?![\w.-])',
    re.ASCII | re.IGNORECASE,
)
_PHONE_IN_TEXT_PATTERN = re.compile(r'(?<!\d)\+?\d[\d\s().-]{5,}\d(?!\d)')
_LABELED_TEXT_PII_PATTERNS = (
    (
        'payload.free_text_wechat',
        re.compile(r'(?:微信|wechat|wx)\s*(?:号|id)?\s*[:：]?\s*([A-Z][-_A-Z0-9]{5,19})', re.IGNORECASE),
    ),
    (
        'payload.free_text_name',
        re.compile(r'(?:联系人|姓名|contact\s*name|name)\s*[:：]\s*([^,，;；\n]{1,100})', re.IGNORECASE),
    ),
    (
        'payload.free_text_address',
        re.compile(r'(?:地址|address)\s*[:：]\s*([^,，;；\n]{1,255})', re.IGNORECASE),
    ),
)
_RETENTION_DAYS = 365


@dataclass
class MigrationBatchResult:
    """一批迁移的无 PII 统计和续跑游标。"""

    source_table: MigrationSource
    after_id: int
    next_cursor: int
    scanned: int = 0
    migrated: int = 0
    quarantined: int = 0
    skipped: int = 0
    dry_run: bool = True


@dataclass(frozen=True)
class _Evidence:
    lawful_basis: str
    source_ref: str
    retention_until: datetime
    consent_ref: str | None = None


@dataclass(frozen=True)
class _MigrationCandidate:
    source_table: MigrationSource
    source_record_id: str
    lead_contact_id: int
    user_id: int
    contact_name: str | None
    title: str | None
    channels: tuple[tuple[str, str], ...]
    field_names: tuple[str, ...]
    fingerprint_values: tuple[str, ...]
    evidence: _Evidence
    form_submission: FormSubmission | None = None


@dataclass(frozen=True)
class _QuarantineCandidate:
    source_table: MigrationSource
    source_record_id: str
    reason_code: str
    field_names: tuple[str, ...]
    fingerprint_values: tuple[str, ...]
    user_id_hint: int | None
    owner_scope_hint: str | None = None
    enterprise_id_hint: int | None = None


def _present_fields(row: Any, names: tuple[str, ...]) -> tuple[str, ...]:
    return tuple(name for name in names if isinstance(getattr(row, name, None), str) and getattr(row, name).strip())


def _field_values(row: Any, names: tuple[str, ...]) -> tuple[str, ...]:
    return tuple(str(getattr(row, name)).strip() for name in names if getattr(row, name, None))


def _known_payload_value(value: Any) -> str | None:
    if isinstance(value, str):
        return value.strip() or None
    if value is None or isinstance(value, bool):
        return None
    return json.dumps(value, ensure_ascii=False, sort_keys=True)


def _free_text_pii(value: str) -> Iterator[tuple[str, str]]:
    for match in _EMAIL_IN_TEXT_PATTERN.finditer(value):
        yield 'payload.free_text_email', match.group()
    for match in _PHONE_IN_TEXT_PATTERN.finditer(value):
        candidate = match.group().strip()
        digit_count = sum(character.isdigit() for character in candidate)
        if 7 <= digit_count <= 20:
            yield 'payload.free_text_phone', candidate
    for field, pattern in _LABELED_TEXT_PII_PATTERNS:
        for match in pattern.finditer(value):
            yield field, match.group(1).strip()


def _walk_payload_pii(value: Any) -> Iterator[tuple[str, str]]:
    if isinstance(value, dict):
        for key, nested in value.items():
            normalized_key = str(key).strip().casefold()
            if normalized_key in _FORM_PAYLOAD_PII_KEYS:
                serialized = _known_payload_value(nested)
                if serialized:
                    yield f'payload.{normalized_key}', serialized
                continue
            yield from _walk_payload_pii(nested)
        return
    if isinstance(value, list):
        for nested in value:
            yield from _walk_payload_pii(nested)
        return
    if isinstance(value, str):
        yield from _free_text_pii(value)


def _payload_pii(payload: Any) -> tuple[tuple[str, ...], tuple[str, ...]]:
    """提取表单载荷中已知 PII 类别与自由文本命中，不保留调用方自定义键名。"""
    matches = tuple(_walk_payload_pii(payload))
    return (
        tuple(sorted({field for field, _value in matches})),
        tuple(value for _field, value in matches),
    )


def _retention_until(evidence_time: datetime) -> datetime:
    return evidence_time + timedelta(days=_RETENTION_DAYS)


class GrowthPiiMigrationService:
    """按主键游标迁移存量 PII；调用方负责批次事务与提交。"""

    _models: dict[MigrationSource, type[Any]] = {
        'contact': LeadContact,
        'customer': Customer,
        'form_submission': FormSubmission,
    }

    async def _public_evidence(
        self,
        db: AsyncSession,
        *,
        contact: LeadContact,
    ) -> _Evidence | None:
        evidence = (
            (
                await db.execute(
                    sa
                    .select(LeadContactSource)
                    .where(
                        LeadContactSource.lead_contact_id == contact.id,
                        LeadContactSource.source_type.in_(_PUBLIC_SOURCE_TYPES),
                        LeadContactSource.source_url.is_not(None),
                        sa.func.length(sa.func.btrim(LeadContactSource.source_url)) > 0,
                    )
                    .order_by(LeadContactSource.seen_at.desc(), LeadContactSource.id.desc())
                    .limit(1)
                )
            )
            .scalars()
            .first()
        )
        if evidence is not None:
            return _Evidence(
                lawful_basis='public_business_contact',
                source_ref=f'contact_source:{evidence.id}',
                retention_until=_retention_until(evidence.seen_at),
            )
        if (contact.source_type or '').strip().casefold() in _PUBLIC_SOURCE_TYPES and (
            contact.source_url or ''
        ).strip():
            return _Evidence(
                lawful_basis='public_business_contact',
                source_ref=f'contact:{contact.id}:legacy_source',
                retention_until=_retention_until(contact.last_seen_at),
            )
        return None

    @staticmethod
    def _contact_channels(contact: LeadContact) -> tuple[tuple[str, str], ...]:
        values = (
            ('email', contact.email or contact.email_normalized),
            ('phone', contact.phone or contact.phone_normalized),
            ('postal_address', contact.address),
        )
        return tuple((channel, value.strip()) for channel, value in values if value and value.strip())

    async def _contact_outcomes(
        self,
        db: AsyncSession,
        contact: LeadContact,
    ) -> list[_MigrationCandidate | _QuarantineCandidate]:
        fields = _present_fields(
            contact,
            (
                'contact_name',
                'email',
                'email_normalized',
                'phone',
                'phone_normalized',
                'address',
            ),
        )
        if not fields:
            return []
        values = _field_values(contact, fields)
        refs = (
            (
                await db.execute(
                    sa
                    .select(LeadRef)
                    .where(LeadRef.lead_contact_id == contact.id)
                    .order_by(LeadRef.user_id, LeadRef.id)
                )
            )
            .scalars()
            .all()
        )
        if not refs:
            return [
                _QuarantineCandidate(
                    source_table='contact',
                    source_record_id=str(contact.id),
                    reason_code='owner_subject_unknown',
                    field_names=fields,
                    fingerprint_values=values,
                    user_id_hint=None,
                )
            ]

        evidence = await self._public_evidence(db, contact=contact)
        outcomes: list[_MigrationCandidate | _QuarantineCandidate] = []
        for ref in refs:
            source_record_id = f'{contact.id}:user:{ref.user_id}'
            if ref.user_id <= 0:
                outcomes.append(
                    _QuarantineCandidate(
                        source_table='contact',
                        source_record_id=source_record_id,
                        reason_code='owner_subject_unknown',
                        field_names=fields,
                        fingerprint_values=values,
                        user_id_hint=None,
                    )
                )
                continue
            if ref.source not in _PUBLIC_LEAD_REF_SOURCES or evidence is None:
                outcomes.append(
                    _QuarantineCandidate(
                        source_table='contact',
                        source_record_id=source_record_id,
                        reason_code='lawful_basis_unproven',
                        field_names=fields,
                        fingerprint_values=values,
                        user_id_hint=ref.user_id,
                        owner_scope_hint='personal',
                    )
                )
                continue
            if evidence.retention_until <= timezone.now():
                outcomes.append(
                    _QuarantineCandidate(
                        source_table='contact',
                        source_record_id=source_record_id,
                        reason_code='retention_expired',
                        field_names=fields,
                        fingerprint_values=values,
                        user_id_hint=ref.user_id,
                        owner_scope_hint='personal',
                    )
                )
                continue
            outcomes.append(
                _MigrationCandidate(
                    source_table='contact',
                    source_record_id=source_record_id,
                    lead_contact_id=contact.id,
                    user_id=ref.user_id,
                    contact_name=contact.contact_name,
                    title=None,
                    channels=self._contact_channels(contact),
                    field_names=fields,
                    fingerprint_values=values,
                    evidence=evidence,
                )
            )
        return outcomes

    async def _customer_outcomes(  # ruff: ignore[complex-structure]
        self,
        db: AsyncSession,
        customer: Customer,
    ) -> list[_MigrationCandidate | _QuarantineCandidate]:
        fields = _present_fields(customer, ('contact_name', 'email', 'phone', 'wechat'))
        if not fields:
            return []
        values = _field_values(customer, fields)

        def quarantine(reason_code: str) -> _QuarantineCandidate:
            return _QuarantineCandidate(
                source_table='customer',
                source_record_id=str(customer.id),
                reason_code=reason_code,
                field_names=fields,
                fingerprint_values=values,
                user_id_hint=customer.user_id or None,
                owner_scope_hint=customer.owner_scope,
                enterprise_id_hint=customer.enterprise_id,
            )

        if customer.owner_scope != 'personal' or customer.enterprise_id is not None:
            return [quarantine('enterprise_mode_disabled')]
        if customer.user_id <= 0:
            return [quarantine('owner_subject_unknown')]
        if not customer.lead_contact_id:
            return [quarantine('contact_subject_missing')]
        contact = await db.get(LeadContact, customer.lead_contact_id)
        if contact is None:
            return [quarantine('contact_subject_missing')]

        evidence: _Evidence | None = None
        if customer.source_kind == 'outbound_crawl':
            evidence = await self._public_evidence(db, contact=contact)
        elif customer.source_kind == 'inbound_form':
            form = (
                (
                    await db.execute(
                        sa
                        .select(FormSubmission)
                        .where(
                            FormSubmission.customer_id == customer.id,
                            FormSubmission.owner_scope == 'personal',
                            FormSubmission.enterprise_id.is_(None),
                            FormSubmission.user_id == customer.user_id,
                            FormSubmission.status.not_in(('rejected', 'spam')),
                            FormSubmission.privacy_notice_version.is_not(None),
                            sa.func.length(
                                sa.func.btrim(FormSubmission.privacy_notice_version)
                            )
                            > 0,
                            FormSubmission.consent_purpose.in_(_FORM_CONSENT_PURPOSES),
                            FormSubmission.consent_source.in_(_FORM_CONSENT_SOURCES),
                            FormSubmission.consent_at.is_not(None),
                            sa.or_(
                                FormSubmission.lead_contact_id.is_(None),
                                FormSubmission.lead_contact_id == customer.lead_contact_id,
                            ),
                        )
                        .order_by(FormSubmission.consent_at.desc(), FormSubmission.id.desc())
                        .limit(1)
                    )
                )
                .scalars()
                .first()
            )
            if form is not None and form.consent_at is not None:
                privacy_notice_version = (form.privacy_notice_version or '').strip()
                evidence = _Evidence(
                    lawful_basis='explicit_form_consent',
                    source_ref=f'form_submission:{form.id}',
                    consent_ref=f'privacy_notice:{privacy_notice_version}',
                    retention_until=_retention_until(form.consent_at),
                )
        if evidence is None:
            return [quarantine('lawful_basis_unproven')]
        if evidence.retention_until <= timezone.now():
            return [quarantine('retention_expired')]
        channels = tuple(
            (channel, value.strip())
            for channel, value in (
                ('email', customer.email),
                ('phone', customer.phone),
                ('wechat', customer.wechat),
            )
            if value and value.strip()
        )
        return [
            _MigrationCandidate(
                source_table='customer',
                source_record_id=str(customer.id),
                lead_contact_id=contact.id,
                user_id=customer.user_id,
                contact_name=customer.contact_name,
                title=None,
                channels=channels,
                field_names=fields,
                fingerprint_values=values,
                evidence=evidence,
            )
        ]

    async def _form_outcomes(
        self,
        db: AsyncSession,
        form: FormSubmission,
    ) -> list[_MigrationCandidate | _QuarantineCandidate]:
        direct_fields = _present_fields(form, ('name', 'email', 'phone'))
        payload_fields, payload_values = _payload_pii(form.payload)
        fields = direct_fields + payload_fields
        if not fields:
            return []
        values = _field_values(form, direct_fields) + payload_values

        def quarantine(reason_code: str) -> _QuarantineCandidate:
            return _QuarantineCandidate(
                source_table='form_submission',
                source_record_id=str(form.id),
                reason_code=reason_code,
                field_names=fields,
                fingerprint_values=values,
                user_id_hint=form.user_id or None,
                owner_scope_hint=form.owner_scope,
                enterprise_id_hint=form.enterprise_id,
            )

        if form.owner_scope != 'personal' or form.enterprise_id is not None:
            return [quarantine('enterprise_mode_disabled')]
        if form.user_id <= 0:
            return [quarantine('owner_subject_unknown')]
        privacy_notice_version = (form.privacy_notice_version or '').strip()
        if (
            form.status in {'rejected', 'spam'}
            or not privacy_notice_version
            or form.consent_purpose not in _FORM_CONSENT_PURPOSES
            or form.consent_source not in _FORM_CONSENT_SOURCES
            or form.consent_at is None
        ):
            return [quarantine('form_consent_unproven')]
        if payload_fields:
            return [quarantine('form_payload_requires_review')]
        contact_id = form.lead_contact_id
        if contact_id is None and form.customer_id is not None:
            contact_id = (
                await db.execute(
                    sa.select(Customer.lead_contact_id).where(
                        Customer.id == form.customer_id,
                        Customer.owner_scope == 'personal',
                        Customer.enterprise_id.is_(None),
                        Customer.user_id == form.user_id,
                    )
                )
            ).scalar_one_or_none()
        if contact_id is None or await db.get(LeadContact, contact_id) is None:
            return [quarantine('contact_subject_missing')]
        retention_until = _retention_until(form.consent_at)
        if retention_until <= timezone.now():
            return [quarantine('retention_expired')]
        channels = tuple(
            (channel, value.strip())
            for channel, value in (('email', form.email), ('phone', form.phone))
            if value and value.strip()
        )
        return [
            _MigrationCandidate(
                source_table='form_submission',
                source_record_id=str(form.id),
                lead_contact_id=contact_id,
                user_id=form.user_id,
                contact_name=form.name,
                title=None,
                channels=channels,
                field_names=fields,
                fingerprint_values=values,
                evidence=_Evidence(
                    lawful_basis='explicit_form_consent',
                    source_ref=f'form_submission:{form.id}',
                    consent_ref=f'privacy_notice:{privacy_notice_version}',
                    retention_until=retention_until,
                ),
                form_submission=form,
            )
        ]

    async def _outcomes(
        self,
        db: AsyncSession,
        *,
        source_table: MigrationSource,
        row: Any,
    ) -> list[_MigrationCandidate | _QuarantineCandidate]:
        if source_table == 'contact':
            return await self._contact_outcomes(db, row)
        if source_table == 'customer':
            return await self._customer_outcomes(db, row)
        return await self._form_outcomes(db, row)

    @staticmethod
    def _fingerprint(
        keyring: GrowthPiiKeyring,
        *,
        source_table: str,
        source_record_id: str,
        values: tuple[str, ...],
    ) -> str:
        payload = json.dumps(
            {
                'source_table': source_table,
                'source_record_id': source_record_id,
                'values': values,
            },
            ensure_ascii=False,
            sort_keys=True,
            separators=(',', ':'),
        )
        return f'v{keyring.active_hmac_version}:{keyring.hmac_for("migration_quarantine", payload)}'

    async def _quarantine(
        self,
        db: AsyncSession,
        *,
        keyring: GrowthPiiKeyring,
        candidate: _QuarantineCandidate,
    ) -> None:
        await db.execute(
            pg_insert(GrowthPiiMigrationQuarantine)
            .values(
                source_table=candidate.source_table,
                source_record_id=candidate.source_record_id,
                reason_code=candidate.reason_code,
                owner_scope_hint=candidate.owner_scope_hint,
                user_id_hint=candidate.user_id_hint,
                enterprise_id_hint=candidate.enterprise_id_hint,
                field_names=sorted(candidate.field_names),
                pii_fingerprint=self._fingerprint(
                    keyring,
                    source_table=candidate.source_table,
                    source_record_id=candidate.source_record_id,
                    values=candidate.fingerprint_values,
                ),
                status='pending',
            )
            .on_conflict_do_nothing(constraint='uq_growth_pii_quarantine_source')
        )

    @staticmethod
    async def _resolve_quarantine(
        db: AsyncSession,
        *,
        source_table: MigrationSource,
        source_record_id: str,
    ) -> None:
        now = timezone.now()
        await db.execute(
            sa
            .update(GrowthPiiMigrationQuarantine)
            .where(
                GrowthPiiMigrationQuarantine.source_table == source_table,
                GrowthPiiMigrationQuarantine.source_record_id == source_record_id,
                GrowthPiiMigrationQuarantine.status == 'pending',
            )
            .values(
                status='resolved',
                resolution_note='迁移条件满足后自动完成',
                resolved_by='growth_pii_migration',
                resolved_time=now,
                updated_time=now,
            )
        )

    async def _migrate_candidate(
        self,
        db: AsyncSession,
        *,
        keyring: GrowthPiiKeyring,
        candidate: _MigrationCandidate,
    ) -> None:
        profile_id = (
            await db.execute(
                sa.select(ContactPrivateProfile.id).where(
                    ContactPrivateProfile.lead_contact_id == candidate.lead_contact_id,
                    ContactPrivateProfile.owner_scope == 'personal',
                    ContactPrivateProfile.user_id == candidate.user_id,
                )
            )
        ).scalar_one_or_none()
        existing_channel_ids: list[int] = []
        missing_channels: list[tuple[str, str]] = []
        for channel, value in candidate.channels:
            hmac_candidates = keyring.hmac_candidates(channel, value)
            existing_rows = (
                (
                    await db.execute(
                        sa
                        .select(ContactChannel)
                        .where(
                            ContactChannel.owner_scope == 'personal',
                            ContactChannel.user_id == candidate.user_id,
                            ContactChannel.channel == channel,
                            sa.tuple_(
                                ContactChannel.hash_key_version,
                                ContactChannel.value_hmac,
                            ).in_([(item.version, item.value) for item in hmac_candidates]),
                        )
                        .order_by(
                            ContactChannel.hash_key_version.desc(),
                            ContactChannel.id,
                        )
                        .with_for_update()
                    )
                )
                .scalars()
                .all()
            )
            if not existing_rows:
                missing_channels.append((channel, value))
                continue
            if any(existing.lead_contact_id != candidate.lead_contact_id for existing in existing_rows):
                raise errors.ConflictError(
                    msg='迁移渠道已归属于同主体下其他联系人',
                    data={'error_code': 'GROWTH_PII_MIGRATION_CHANNEL_CONFLICT'},
                )
            existing_channel_ids.append(existing_rows[0].id)

        private: dict[str, Any]
        if profile_id is not None and not missing_channels:
            private = {
                'private_profile_id': int(profile_id),
                'channels': [{'id': channel_id} for channel_id in existing_channel_ids],
            }
        else:
            private = await contact_privacy_service.store_private_contact(
                db,
                keyring=keyring,
                lead_contact_id=candidate.lead_contact_id,
                owner_scope='personal',
                user_id=candidate.user_id,
                enterprise_id=None,
                contact_name=candidate.contact_name,
                title=candidate.title,
                lawful_basis=candidate.evidence.lawful_basis,
                source_ref=candidate.evidence.source_ref,
                retention_until=candidate.evidence.retention_until,
                channels=[
                    ContactChannelWrite(
                        channel=channel,
                        value=value,
                        lawful_basis=candidate.evidence.lawful_basis,
                        source_ref=candidate.evidence.source_ref,
                        consent_ref=candidate.evidence.consent_ref,
                        retention_until=candidate.evidence.retention_until,
                    )
                    for channel, value in missing_channels
                ],
                preserve_existing=True,
                allow_profile_only=True,
            )
            private['channels'] = [{'id': channel_id} for channel_id in existing_channel_ids] + private['channels']
        if candidate.form_submission is not None:
            candidate.form_submission.contact_private_profile_id = private['private_profile_id']
            candidate.form_submission.contact_channel_ids = [channel['id'] for channel in private['channels']]
        await self._resolve_quarantine(
            db,
            source_table=candidate.source_table,
            source_record_id=candidate.source_record_id,
        )

    async def migrate_batch(  # ruff: ignore[complex-structure]
        self,
        db: AsyncSession,
        *,
        keyring: GrowthPiiKeyring,
        source_table: MigrationSource,
        after_id: int = 0,
        batch_size: int = 100,
        dry_run: bool = True,
    ) -> MigrationBatchResult:
        """扫描并处理一批；默认 dry-run，返回最后扫描主键供调用方续跑。"""
        if source_table not in self._models:
            raise ValueError(f'不支持的迁移源表：{source_table}')
        if after_id < 0:
            raise ValueError('迁移游标不能为负数')
        if not 1 <= batch_size <= 1000:
            raise ValueError('迁移批次大小必须在 1..1000')

        model = self._models[source_table]
        rows = (
            (await db.execute(sa.select(model).where(model.id > after_id).order_by(model.id).limit(batch_size)))
            .scalars()
            .all()
        )
        result = MigrationBatchResult(
            source_table=source_table,
            after_id=after_id,
            next_cursor=after_id,
            dry_run=dry_run,
        )
        if not dry_run:
            await ensure_growth_pii_key_write_fence(db, keyring=keyring)
        for row in rows:
            result.scanned += 1
            result.next_cursor = int(row.id)
            outcomes = await self._outcomes(db, source_table=source_table, row=row)
            if not outcomes:
                result.skipped += 1
                continue
            for outcome in outcomes:
                if isinstance(outcome, _QuarantineCandidate):
                    result.quarantined += 1
                    if not dry_run:
                        await self._quarantine(db, keyring=keyring, candidate=outcome)
                    continue

                result.migrated += 1
                if dry_run:
                    continue
                try:
                    async with db.begin_nested():
                        await self._migrate_candidate(db, keyring=keyring, candidate=outcome)
                except BaseExceptionError:
                    result.migrated -= 1
                    result.quarantined += 1
                    await self._quarantine(
                        db,
                        keyring=keyring,
                        candidate=_QuarantineCandidate(
                            source_table=outcome.source_table,
                            source_record_id=outcome.source_record_id,
                            reason_code='private_write_conflict',
                            field_names=outcome.field_names,
                            fingerprint_values=outcome.fingerprint_values,
                            user_id_hint=outcome.user_id,
                            owner_scope_hint='personal',
                        ),
                    )
        return result


growth_pii_migration_service = GrowthPiiMigrationService()
