"""获客线索新写的公共事实与 Owner 私有 PII 分流。

所有采集、企业查询和人工登记写点都通过本服务：`contact` 只保存可共享商业事实，
姓名、联系方式和地址只进入 `contact_private_profile/contact_channel`。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from decimal import Decimal
from typing import TYPE_CHECKING, Any
from urllib.parse import unquote, urlsplit

import sqlalchemy as sa

from backend.app.hasn_growth.model.contact_channel import ContactChannel
from backend.app.hasn_growth.model.lead_contact import LeadContact
from backend.app.hasn_growth.service.contact_privacy_service import (
    ContactChannelWrite,
    contact_privacy_service,
)
from backend.app.hasn_growth.service.dedupe_service import dedupe_key
from backend.app.hasn_growth.service.pii import redact_pii_value
from backend.app.hasn_growth.service.pii_keyring import GrowthPiiKeyring
from backend.common.exception import errors
from backend.utils.timezone import timezone

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession


@dataclass(frozen=True)
class PrivateLeadWrite:
    """一次线索入池所需的公开事实、私有字段和合规证据。"""

    user_id: int | None
    pool_visibility: str
    company_name: str | None
    contact_name: str | None
    email: str | None
    phone: str | None
    address: str | None
    website: str | None
    domain: str | None
    country: str | None
    region: str | None
    city: str | None
    industry: str | None
    source_type: str
    source_url: str | None
    lawful_basis: str
    source_ref: str
    retention_until: datetime
    confidence_score: Decimal
    public_metadata: dict[str, Any] = field(default_factory=dict)
    preserve_existing_private: bool = True


@dataclass(frozen=True)
class PrivateLeadWriteResult:
    """线索分流写入结果。"""

    created: bool
    contact: LeadContact
    match_dimension: str
    private_profile_id: int | None
    masked_private_contact: dict[str, Any] | None


def _public_text(value: str | None) -> str | None:
    """清理公开商业文本中的可识别邮箱和电话。"""
    if not value:
        return None
    cleaned = value.strip()
    if not cleaned:
        return None
    redacted = redact_pii_value(cleaned)
    return str(redacted) if redacted else None


def _public_url(value: str | None, *, sensitive_values: tuple[str | None, ...]) -> str | None:
    """公共 URL 只保留站点和安全路径，统一移除凭据、查询、片段及路径 PII。"""
    if not value:
        return None
    candidate = value.strip()
    if not candidate.startswith(('http://', 'https://')):
        candidate = f'https://{candidate.strip("/")}'
    try:
        parsed = urlsplit(candidate)
        if not parsed.hostname:
            return None
        port = f':{parsed.port}' if parsed.port else ''
    except ValueError:
        return None
    path = parsed.path or '/'
    decoded_path = unquote(path).casefold()
    if any(
        sensitive.strip().casefold() in decoded_path
        for sensitive in sensitive_values
        if sensitive and sensitive.strip()
    ):
        path = '/'
    return f'{parsed.scheme}://{parsed.hostname.casefold()}{port}{path}'[:2048]


def _public_domain(value: str | None, *, website: str | None) -> str | None:
    """优先从已清理 website 提取主机，避免 domain 字段携带凭据或路径。"""
    for candidate in (website, value):
        if not candidate:
            continue
        parsed = urlsplit(
            candidate
            if candidate.startswith(('http://', 'https://'))
            else f'https://{candidate}'
        )
        if parsed.hostname:
            return parsed.hostname.casefold()[:255]
    return None


class LeadIngestionPrivacyService:
    """按 Owner 渠道优先、公共域名次之的顺序复用联系人并拆分 PII。"""

    @staticmethod
    def _channels(write: PrivateLeadWrite) -> list[ContactChannelWrite]:
        values = (
            ('email', write.email),
            ('phone', write.phone),
            ('postal_address', write.address),
        )
        return [
            ContactChannelWrite(
                channel=channel,
                value=value.strip(),
                lawful_basis=write.lawful_basis,
                source_ref=write.source_ref,
                retention_until=write.retention_until,
            )
            for channel, value in values
            if value and value.strip()
        ]

    @staticmethod
    async def _find_owner_contact(
        db: AsyncSession,
        *,
        keyring: GrowthPiiKeyring,
        user_id: int,
        channels: list[ContactChannelWrite],
    ) -> int | None:
        """按保留的全部 HMAC 版本查 Owner 联系人；多联系人命中时拒绝猜测合并。"""
        contact_ids: set[int] = set()
        for channel in channels:
            candidates = keyring.hmac_candidates(channel.channel, channel.value)
            contact_ids.update(
                int(contact_id)
                for contact_id in (
                    (
                        await db.execute(
                            sa.select(ContactChannel.lead_contact_id).where(
                                ContactChannel.owner_scope == 'personal',
                                ContactChannel.user_id == user_id,
                                ContactChannel.channel == channel.channel,
                                sa.tuple_(
                                    ContactChannel.hash_key_version,
                                    ContactChannel.value_hmac,
                                ).in_([
                                    (candidate.version, candidate.value)
                                    for candidate in candidates
                                ]),
                            )
                        )
                    )
                    .scalars()
                    .all()
                )
            )
        if len(contact_ids) > 1:
            raise errors.ConflictError(
                msg='采集联系方式分别属于不同联系人，必须由 Owner 先完成联系人合并',
                data={'error_code': 'GROWTH_PII_CONTACT_MERGE_REQUIRED'},
            )
        return next(iter(contact_ids), None)

    async def upsert(
        self,
        db: AsyncSession,
        *,
        keyring: GrowthPiiKeyring,
        write: PrivateLeadWrite,
    ) -> PrivateLeadWriteResult:
        """写入公共联系人事实，并在有 Owner 时把 PII 写入主体私有表。"""
        channels = self._channels(write)
        has_private_identity = write.user_id is not None and (
            bool(channels) or bool((write.contact_name or '').strip())
        )
        sensitive_values = (
            write.contact_name,
            write.email,
            write.phone,
            write.address,
        )
        public_website = _public_url(
            write.website,
            sensitive_values=sensitive_values,
        )
        public_source_url = _public_url(
            write.source_url,
            sensitive_values=sensitive_values,
        )
        public_domain = _public_domain(write.domain, website=public_website)
        contact: LeadContact | None = None
        match_dimension = 'new'
        if write.user_id is not None and channels:
            contact_id = await self._find_owner_contact(
                db,
                keyring=keyring,
                user_id=write.user_id,
                channels=channels,
            )
            if contact_id is not None:
                contact = await db.get(LeadContact, contact_id)
                if contact is None:
                    raise errors.ConflictError(
                        msg='联系人私有索引指向不存在的公共事实',
                        data={'error_code': 'GROWTH_PII_CONTACT_INDEX_INVALID'},
                    )
                match_dimension = 'private_channel'

        domain_key = dedupe_key(public_domain)
        can_dedupe_by_domain = (
            not has_private_identity and write.pool_visibility == 'public'
        )
        if contact is None and domain_key and can_dedupe_by_domain:
            contact = await db.scalar(
                sa.select(LeadContact).where(
                    LeadContact.dedupe_key_domain == domain_key,
                    LeadContact.pool_visibility == 'public',
                )
            )
            if contact is not None:
                match_dimension = 'domain'

        created = contact is None
        if contact is None:
            contact = LeadContact(
                lead_no=f'LEAD{timezone.now().strftime("%Y%m%d%H%M%S%f")}',
                pool_visibility=write.pool_visibility,
                company_name=_public_text(write.company_name),
                contact_name=None,
                email=None,
                email_normalized=None,
                phone=None,
                phone_normalized=None,
                website=public_website,
                domain=public_domain,
                country=_public_text(write.country),
                region=_public_text(write.region),
                city=_public_text(write.city),
                address=None,
                industry=_public_text(write.industry),
                source_type=write.source_type,
                source_url=public_source_url,
                keyword=None,
                status='new',
                confidence_score=write.confidence_score,
                dedupe_key_email=None,
                dedupe_key_phone=None,
                dedupe_key_domain=domain_key if can_dedupe_by_domain else None,
                normalization_version='pii-private-v1',
                meta_data=redact_pii_value(write.public_metadata),
            )
            db.add(contact)
            await db.flush()
        else:
            contact.last_seen_at = timezone.now()
            contact.confidence_score = max(
                contact.confidence_score,
                write.confidence_score,
            )

        private_profile_id: int | None = None
        masked_private_contact: dict[str, Any] | None = None
        if write.user_id is not None and (channels or (write.contact_name or '').strip()):
            masked_private_contact = await contact_privacy_service.store_private_contact(
                db,
                keyring=keyring,
                lead_contact_id=contact.id,
                owner_scope='personal',
                user_id=write.user_id,
                enterprise_id=None,
                contact_name=(write.contact_name or '').strip() or None,
                title=None,
                lawful_basis=write.lawful_basis,
                source_ref=write.source_ref,
                retention_until=write.retention_until,
                channels=channels,
                preserve_existing=write.preserve_existing_private,
                allow_profile_only=True,
            )
            private_profile_id = int(masked_private_contact['private_profile_id'])

        return PrivateLeadWriteResult(
            created=created,
            contact=contact,
            match_dimension=match_dimension,
            private_profile_id=private_profile_id,
            masked_private_contact=masked_private_contact,
        )


lead_ingestion_privacy_service = LeadIngestionPrivacyService()
