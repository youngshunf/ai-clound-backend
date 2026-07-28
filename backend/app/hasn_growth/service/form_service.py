"""获客落地页表单回流服务。

公开表单只保存去标识业务元数据；姓名和联系方式立即进入 Owner 私有密文表，
`contact/customer/form_submission` 的旧 PII 列不再写入。落地页未开放、PII 新写未
开放或密钥不可用时均 fail-closed。
"""

from __future__ import annotations

import json

from datetime import timedelta
from decimal import Decimal
from typing import Any
from urllib.parse import urlsplit
from uuid import uuid4

import sqlalchemy as sa

from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from backend.app.hasn_core import HasnHumans
from backend.app.hasn_growth.model.contact_channel import ContactChannel
from backend.app.hasn_growth.model.customer import Customer
from backend.app.hasn_growth.model.form_submission import FormSubmission
from backend.app.hasn_growth.model.growth_project import GrowthProject
from backend.app.hasn_growth.model.lead_contact import LeadContact
from backend.app.hasn_growth.service.contact_privacy_service import (
    ContactChannelWrite,
    contact_privacy_service,
    ensure_growth_pii_key_write_fence,
)
from backend.app.hasn_growth.service.funnel_service import GrowthFunnelService
from backend.app.hasn_growth.service.pii import redact_pii_value
from backend.app.hasn_growth.service.pii_keyring import GrowthPiiKeyring, require_growth_pii_keyring
from backend.app.hasn_growth.service.project_lead_compatibility_service import (
    project_lead_compatibility_service,
)
from backend.app.hasn_publish.model.site import Site
from backend.common.exception import errors
from backend.core.conf import settings
from backend.utils.timezone import timezone

_FORM_RETENTION_DAYS = 365
_FORM_UTM_KEYS = ('utm_source', 'utm_medium', 'utm_campaign', 'utm_content', 'utm_term')


def _gen_no(prefix: str) -> str:
    return f'{prefix}{uuid4().hex[:12].upper()}'


def _clean_optional(value: Any, *, max_length: int) -> str | None:
    if not isinstance(value, str):
        return None
    cleaned = value.strip()
    return cleaned[:max_length] or None


def _clean_business_metadata(value: Any, *, max_length: int) -> str | None:
    """业务元数据仍按可识别邮箱/电话做去标识，防止借字段形成旁路副本。"""
    cleaned = _clean_optional(value, max_length=max_length)
    if cleaned is None:
        return None
    redacted = redact_pii_value(cleaned)
    return redacted if isinstance(redacted, str) else None


def _hmac_untrusted_metadata(
    keyring: GrowthPiiKeyring,
    *,
    field: str,
    value: Any,
    max_length: int,
) -> str | None:
    """客户端归因值不可证明不含 PII，只保存可轮换 HMAC 供去重和聚合。"""
    cleaned = _clean_optional(value, max_length=max_length)
    if cleaned is None:
        return None
    return f'v{keyring.active_hmac_version}:{keyring.hmac_for(f"form_{field}", cleaned)}'


def _referrer_origin(value: str | None) -> str | None:
    """只保留来源站点 origin，避免 URL 查询参数或路径形成 PII 副本。"""
    if not value:
        return None
    parsed = urlsplit(value)
    if parsed.scheme not in {'http', 'https'} or not parsed.hostname:
        return None
    try:
        port = f':{parsed.port}' if parsed.port else ''
    except ValueError:
        return None
    return f'{parsed.scheme}://{parsed.hostname}{port}'[:2048]


class GrowthFormService:
    """表单回流：门禁 → 去标识提交 → 私有联系人 → 客户。"""

    @staticmethod
    async def _resolve_owner(db: AsyncSession, *, publish_ref: str) -> tuple[int, str, Site]:
        """按服务端 Growth 绑定解析落地页；普通公开制品不能伪装成获客表单。"""
        site = (
            await db.execute(
                sa.select(Site).where(
                    Site.slug == publish_ref,
                    Site.kind == 'page',
                    Site.source_app == 'growth',
                    Site.status == 'active',
                    Site.visibility.in_(('public', 'unlisted')),
                    Site.deleted_time.is_(None),
                    sa.or_(Site.expires_at.is_(None), Site.expires_at > timezone.now()),
                )
            )
        ).scalar_one_or_none()
        if not site:
            raise errors.NotFoundError(msg='落地页不存在或已下线')
        project = (
            await db.execute(
                sa.select(GrowthProject).where(
                    GrowthProject.landing_site_ref == f'hasn://publish/sites/{site.id}',
                    GrowthProject.owner_hasn_id == site.owner_id,
                    GrowthProject.owner_scope == 'personal',
                    GrowthProject.enterprise_id.is_(None),
                    GrowthProject.status == 'active',
                    GrowthProject.provision_status == 'ready',
                )
            )
        ).scalar_one_or_none()
        if project is None:
            raise errors.NotFoundError(msg='落地页未绑定可用的获客项目')
        human = (
            await db.execute(sa.select(HasnHumans).where(HasnHumans.hasn_id == site.owner_id))
        ).scalar_one_or_none()
        if not human or human.user_id != project.user_id:
            raise errors.NotFoundError(msg='落地页归属主人不存在')
        return human.user_id, site.owner_id, site

    @staticmethod
    async def _find_private_contact_id(
        db: AsyncSession,
        *,
        keyring: GrowthPiiKeyring,
        user_id: int,
        channels: list[ContactChannelWrite],
    ) -> int | None:
        """按保留的全部 HMAC 版本查当前 Owner 已有联系人，跨联系人冲突时拒绝猜测合并。"""
        contact_ids: set[int] = set()
        matched_channels = 0
        for channel in channels:
            candidates = keyring.hmac_candidates(channel.channel, channel.value)
            channel_contact_ids = set(
                (
                    await db.execute(
                        sa.select(ContactChannel.lead_contact_id).where(
                            ContactChannel.owner_scope == 'personal',
                            ContactChannel.user_id == user_id,
                            ContactChannel.channel == channel.channel,
                            sa.tuple_(
                                ContactChannel.hash_key_version,
                                ContactChannel.value_hmac,
                            ).in_([(candidate.version, candidate.value) for candidate in candidates]),
                        )
                    )
                )
                .scalars()
                .all()
            )
            if channel_contact_ids:
                matched_channels += 1
                contact_ids.update(int(contact_id) for contact_id in channel_contact_ids)
        if len(contact_ids) > 1:
            raise errors.ConflictError(
                msg='提交的联系方式分别属于不同联系人，必须由 Owner 先完成联系人合并',
                data={'error_code': 'GROWTH_FORM_CONTACT_MERGE_REQUIRED'},
            )
        if contact_ids and matched_channels != len(channels):
            raise errors.ConflictError(
                msg='既有联系人新增联系方式必须由 Owner 审核',
                data={'error_code': 'GROWTH_FORM_NEW_CHANNEL_REVIEW_REQUIRED'},
            )
        return next(iter(contact_ids), None)

    @staticmethod
    async def _ensure_contact(
        db: AsyncSession,
        *,
        keyring: GrowthPiiKeyring,
        user_id: int,
        company: str | None,
        channels: list[ContactChannelWrite],
    ) -> LeadContact:
        contact_id = await GrowthFormService._find_private_contact_id(
            db,
            keyring=keyring,
            user_id=user_id,
            channels=channels,
        )
        if contact_id is None:
            contact = LeadContact(
                lead_no=_gen_no('L'),
                pool_visibility='private',
                company_name=company,
                contact_name=None,
                email=None,
                email_normalized=None,
                phone=None,
                phone_normalized=None,
                source_type='inbound_form',
                status='valid',
                confidence_score=Decimal(50),
                normalization_version='pii-private-v1',
                meta_data={},
            )
            db.add(contact)
            await db.flush()
        else:
            loaded_contact = await db.get(LeadContact, contact_id)
            if loaded_contact is None:
                raise errors.ConflictError(
                    msg='联系人索引指向不存在的联系人',
                    data={'error_code': 'GROWTH_FORM_CONTACT_INDEX_INVALID'},
                )
            contact = loaded_contact

        await project_lead_compatibility_service.upsert_reference(
            db,
            user_id=user_id,
            lead_contact_id=contact.id,
            source='manual',
            status='qualified',
            update_source=False,
        )
        return contact

    @staticmethod
    async def _upsert_inbound_customer(
        db: AsyncSession,
        *,
        user_id: int,
        company: str | None,
        lead_contact_id: int,
    ) -> Customer:
        """客户只引用全局联系人事实，旧姓名和渠道列恒为 NULL。"""
        now = timezone.now()
        insert = pg_insert(Customer).values(
            customer_no=_gen_no('CUS'),
            user_id=user_id,
            lead_contact_id=lead_contact_id,
            source_kind='inbound_form',
            company_name=company,
            contact_name=None,
            email=None,
            phone=None,
            wechat=None,
            im_refs={},
            profile_json={},
            intent_score=Decimal(50),
            lifecycle_status='engaged',
            tags=[],
            silent_round_count=0,
            last_activity_at=now,
            owner_scope='personal',
            enterprise_id=None,
            assignee=None,
        )
        customer_id = int(
            (
                await db.execute(
                    insert.on_conflict_do_update(
                        index_elements=[Customer.user_id, Customer.lead_contact_id],
                        index_where=sa.and_(
                            Customer.owner_scope == 'personal',
                            Customer.lead_contact_id.is_not(None),
                        ),
                        set_={
                            'company_name': sa.func.coalesce(
                                Customer.company_name,
                                insert.excluded.company_name,
                            ),
                            'lifecycle_status': 'engaged',
                            'last_activity_at': now,
                            'updated_time': now,
                        },
                    ).returning(Customer.id)
                )
            ).scalar_one()
        )
        customer = await db.get(Customer, customer_id)
        if customer is None:
            raise errors.ServerError(msg='表单客户写入失败')
        return customer

    @classmethod
    async def submit_form(
        cls,
        db: AsyncSession,
        *,
        publish_ref: str,
        data: dict[str, Any],
        client_ip: str | None = None,
        referrer: str | None = None,
    ) -> dict[str, Any]:
        """处理一次落地页表单提交，返回去标识结果。"""
        if not settings.GROWTH_PUBLISH_LANDING_ENABLED:
            raise errors.ConflictError(
                msg='获客落地页表单尚未开放',
                data={'error_code': 'GROWTH_PUBLISH_LANDING_DISABLED'},
            )
        if not settings.GROWTH_PII_NEW_WRITE_ENABLED:
            raise errors.ConflictError(
                msg='联系人 PII 新写尚未启用',
                data={'error_code': 'GROWTH_PII_NEW_WRITE_DISABLED'},
            )
        keyring = require_growth_pii_keyring()
        await ensure_growth_pii_key_write_fence(db, keyring=keyring)
        user_id, _owner, site = await cls._resolve_owner(db, publish_ref=publish_ref)

        email = _clean_optional(data.get('email'), max_length=255)
        phone = _clean_optional(data.get('phone'), max_length=50)
        wechat = _clean_optional(data.get('wechat'), max_length=100)
        company = _clean_business_metadata(data.get('company_name'), max_length=255)
        contact_name = _clean_optional(data.get('contact_name'), max_length=100)
        message = _clean_optional(data.get('message'), max_length=2000)
        notice_version = _clean_optional(data.get('privacy_notice_version'), max_length=64)
        consent_purpose = _clean_optional(data.get('consent_purpose'), max_length=128)
        expected_notice_version = settings.GROWTH_FORM_PRIVACY_NOTICE_VERSION.strip()
        if not expected_notice_version:
            raise errors.ConflictError(
                msg='获客表单隐私说明版本尚未配置',
                data={'error_code': 'GROWTH_FORM_PRIVACY_NOTICE_UNCONFIGURED'},
            )
        if (
            notice_version != expected_notice_version
            or consent_purpose != 'sales_followup'
            or data.get('consent_granted') is not True
        ):
            raise errors.RequestError(
                msg='表单必须提供有效的隐私说明版本和明确同意',
                data={'error_code': 'GROWTH_FORM_CONSENT_REQUIRED'},
            )
        consent_at = timezone.now()
        retention_until = consent_at + timedelta(days=_FORM_RETENTION_DAYS)

        # 反滥用：蜜罐字段被填 → spam；或无任何联系方式 → spam（无效留资）。
        honeypot_filled = bool(_clean_optional(data.get('website_url'), max_length=2048))
        no_contact = not (email or phone or wechat)
        is_spam = honeypot_filled or no_contact
        spam_reason = 'honeypot_filled' if honeypot_filled else ('contact_missing' if no_contact else None)

        raw_channels = (
            ('email', email),
            ('phone', phone),
            ('wechat', wechat),
        )
        source_ref_pending = f'publish_site:{site.id}'
        channels = [
            ContactChannelWrite(
                channel=channel,
                value=value,
                lawful_basis='explicit_form_consent',
                source_ref=source_ref_pending,
                consent_ref=f'privacy_notice:{notice_version}',
                retention_until=retention_until,
            )
            for channel, value in raw_channels
            if value
        ]
        fingerprint_payload = json.dumps(
            {
                'site_id': site.id,
                'contact_name': contact_name,
                'email': email,
                'phone': phone,
                'wechat': wechat,
                'company': company,
                'message': message,
            },
            ensure_ascii=False,
            sort_keys=True,
            separators=(',', ':'),
        )
        fingerprint = f'v{keyring.active_hmac_version}:{keyring.hmac_for("form_submission", fingerprint_payload)}'
        ip_hmac = f'v{keyring.active_hmac_version}:{keyring.hmac_for("ip", client_ip)}' if client_ip else None
        raw_extra = data.get('extra')
        extra: dict[str, Any] = raw_extra if isinstance(raw_extra, dict) else {}
        utm = {
            key: _hmac_untrusted_metadata(
                keyring,
                field=key,
                value=extra.get(key),
                max_length=255,
            )
            for key in _FORM_UTM_KEYS
        }

        submission = FormSubmission(
            user_id=user_id,
            publish_ref=publish_ref,
            publish_site_id=site.id,
            submission_fingerprint=fingerprint,
            payload={'message_received': bool(message)},
            email=None,
            phone=None,
            name=None,
            company=company,
            status='spam' if is_spam else 'pending',
            privacy_notice_version=notice_version,
            consent_purpose=consent_purpose,
            consent_source='landing_form',
            consent_at=consent_at,
            ip_hmac=ip_hmac,
            spam_status='blocked' if is_spam else 'clean',
            spam_reason=spam_reason,
            utm_source=utm['utm_source'],
            utm_medium=utm['utm_medium'],
            utm_campaign=utm['utm_campaign'],
            utm_content=utm['utm_content'],
            utm_term=utm['utm_term'],
            referrer=_referrer_origin(referrer),
            source_meta={},
            owner_scope='personal',
            enterprise_id=None,
            assignee=None,
        )
        db.add(submission)
        await db.flush()

        if is_spam:
            return {'status': 'spam', 'form_submission_id': submission.id, 'customer_id': None}

        contact = await cls._ensure_contact(
            db,
            keyring=keyring,
            user_id=user_id,
            company=company,
            channels=channels,
        )
        source_ref = f'form_submission:{submission.id}'
        private = await contact_privacy_service.store_private_contact(
            db,
            keyring=keyring,
            lead_contact_id=contact.id,
            owner_scope='personal',
            user_id=user_id,
            enterprise_id=None,
            contact_name=contact_name,
            title=None,
            lawful_basis='explicit_form_consent',
            source_ref=source_ref,
            retention_until=retention_until,
            channels=[
                ContactChannelWrite(
                    channel=channel.channel,
                    value=channel.value,
                    lawful_basis=channel.lawful_basis,
                    source_ref=source_ref,
                    consent_ref=channel.consent_ref,
                    retention_until=channel.retention_until,
                )
                for channel in channels
            ],
            preserve_existing=True,
        )
        customer = await cls._upsert_inbound_customer(
            db,
            user_id=user_id,
            company=company,
            lead_contact_id=contact.id,
        )
        submission.customer_id = customer.id
        submission.lead_contact_id = contact.id
        submission.contact_private_profile_id = private['private_profile_id']
        submission.contact_channel_ids = [channel['id'] for channel in private['channels']]
        submission.status = 'converted'
        await db.flush()

        await GrowthFunnelService._add_activity(
            db,
            user_id=user_id,
            customer_id=customer.id,
            kind='reply',
            content='落地页留资已转化为客户',
            actor_kind='owner',
            actor_id=None,
            ref_table='form_submission',
            ref_id=str(submission.id),
        )
        return {
            'status': 'converted',
            'customer_id': customer.id,
            'form_submission_id': submission.id,
        }


growth_form_service = GrowthFormService()
