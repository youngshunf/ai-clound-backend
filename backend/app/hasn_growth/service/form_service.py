"""获客落地页表单回流服务。

公开表单只保存去标识业务元数据；姓名和联系方式立即进入 Owner 私有密文表，
`contact/customer/form_submission` 的旧 PII 列不再写入。落地页未开放、PII 新写未
开放或密钥不可用时均 fail-closed。
"""

from __future__ import annotations

import html
import json
import re
import unicodedata

from datetime import timedelta
from decimal import Decimal
from typing import Any
from urllib.parse import urlsplit
from uuid import UUID, uuid4

import sqlalchemy as sa

from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from backend.app.hasn_core import HasnHumans
from backend.app.hasn_growth.model.contact_channel import ContactChannel
from backend.app.hasn_growth.model.customer import Customer
from backend.app.hasn_growth.model.form_submission import FormSubmission
from backend.app.hasn_growth.model.growth_attribution_event import GrowthAttributionEvent
from backend.app.hasn_growth.model.growth_project import GrowthProject
from backend.app.hasn_growth.model.growth_project_lead import GrowthProjectLead
from backend.app.hasn_growth.model.lead_contact import LeadContact
from backend.app.hasn_growth.model.lead_ref import LeadRef
from backend.app.hasn_growth.service.contact_privacy_service import (
    ContactChannelWrite,
    contact_privacy_service,
    ensure_growth_pii_key_write_fence,
)
from backend.app.hasn_growth.service.funnel_service import GrowthFunnelService
from backend.app.hasn_growth.service.growth_notification import growth_notification_service
from backend.app.hasn_growth.service.pii import redact_pii_value
from backend.app.hasn_growth.service.pii_keyring import GrowthPiiKeyring, require_growth_pii_keyring
from backend.app.hasn_publish.provider.client import publish_provider
from backend.app.hasn_task.model.task import HasnTask
from backend.common.exception import errors
from backend.common.response.response_code import StandardResponseCode
from backend.core.conf import settings
from backend.database.redis import redis_client
from backend.utils.timezone import timezone

_FORM_RETENTION_DAYS = 365
_FORM_UTM_KEYS = ('utm_source', 'utm_medium', 'utm_campaign', 'utm_content', 'utm_term')
_HTML_TAG_RE = re.compile(r'<[^>]*>')


def _gen_no(prefix: str) -> str:
    return f'{prefix}{uuid4().hex[:12].upper()}'


def _clean_optional(value: Any, *, max_length: int) -> str | None:
    if not isinstance(value, str):
        return None
    normalized = unicodedata.normalize('NFKC', value)
    without_controls = ''.join(
        char
        for char in normalized
        if unicodedata.category(char) not in {'Cc', 'Cf'} or char in {'\n', '\t'}
    )
    cleaned = _HTML_TAG_RE.sub('', html.unescape(without_controls)).strip()
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
    async def _resolve_owner(
        db: AsyncSession,
        *,
        binding: dict[str, Any],
    ) -> tuple[int, str, GrowthProject]:
        """只按 Publish provider 返回的权威平台项目解析 Growth，绝不信任公开请求体。"""
        project = (
            await db.execute(
                sa.select(GrowthProject).where(
                    GrowthProject.platform_project_id == binding['platform_project_id'],
                    GrowthProject.landing_site_ref == f'hasn://publish/sites/{binding["site_id"]}',
                    GrowthProject.owner_hasn_id == binding['owner_hasn_id'],
                    GrowthProject.owner_scope == 'personal',
                    GrowthProject.enterprise_id.is_(None),
                    GrowthProject.status == 'active',
                    GrowthProject.provision_status == 'ready',
                )
            )
        ).scalar_one_or_none()
        if project is None:
            raise errors.ConflictError(
                msg='落地页未绑定可用的获客项目',
                data={'error_code': 'GROWTH_FORM_PROJECT_UNAVAILABLE'},
            )
        human = (
            await db.execute(sa.select(HasnHumans).where(HasnHumans.hasn_id == binding['owner_hasn_id']))
        ).scalar_one_or_none()
        if not human or human.user_id != project.user_id:
            raise errors.NotFoundError(msg='落地页归属主人不存在')
        return human.user_id, binding['owner_hasn_id'], project

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

        return contact

    @staticmethod
    async def _ensure_project_lead(
        db: AsyncSession,
        *,
        project: GrowthProject,
        contact: LeadContact,
        submission_id: int,
        source_meta: dict[str, Any],
    ) -> GrowthProjectLead:
        """幂等建立项目线索与迁移期旧引用，项目来源事实保持 inbound_form。"""
        await db.execute(
            pg_insert(LeadRef)
            .values(
                user_id=project.user_id,
                lead_contact_id=contact.id,
                source='manual',
                status='new',
            )
            .on_conflict_do_nothing(constraint='uq_growth_lead_ref_user_lead')
        )
        statement = (
            pg_insert(GrowthProjectLead)
            .values(
                growth_project_id=project.id,
                lead_contact_id=contact.id,
                user_id=project.user_id,
                owner_scope='personal',
                enterprise_id=None,
                assignee=None,
                source_kind='inbound_form',
                source_tool='publish_form',
                source_ref=f'form_submission:{submission_id}',
                source_meta=source_meta,
                status='new',
            )
            .on_conflict_do_nothing(constraint='uq_growth_project_lead_contact')
            .returning(GrowthProjectLead.id)
        )
        project_lead_id = (await db.execute(statement)).scalar_one_or_none()
        if project_lead_id is None:
            project_lead = (
                await db.execute(
                    sa.select(GrowthProjectLead).where(
                        GrowthProjectLead.growth_project_id == project.id,
                        GrowthProjectLead.lead_contact_id == contact.id,
                    )
                )
            ).scalar_one()
        else:
            loaded = await db.get(GrowthProjectLead, int(project_lead_id))
            if loaded is None:
                raise errors.ServerError(msg='项目线索写入后无法读取')
            project_lead = loaded
        return project_lead

    @staticmethod
    async def _ensure_followup_task(
        db: AsyncSession,
        *,
        project: GrowthProject,
        customer: Customer,
        submission_id: int,
    ) -> str | None:
        """为有效留资建立一次性接续任务；项目未绑定分身时如实保留待恢复状态。"""
        if not project.owner_agent_id:
            return None
        task_uuid = f'growth:inbound:{submission_id}'
        prompt = (
            f'跟进获客项目 {project.id} 的新入站客户 {customer.id}。'
            '先读取客户脱敏详情与同意记录，再给主人提出合规跟进建议；'
            '任何对外发送必须进入审批流程。'
        )
        now = timezone.now()
        await db.execute(
            pg_insert(HasnTask)
            .values(
                owner_id=project.owner_hasn_id,
                agent_id=project.owner_agent_id,
                name='处理新的落地页留资',
                description='公开表单回流后自动建立的 Owner 接续任务',
                prompt=prompt,
                schedule_type='once',
                schedule_config={'run_at': now.isoformat()},
                schedule_display='收到留资后立即处理',
                timezone='Asia/Shanghai',
                misfire_policy='skip',
                enabled=True,
                state='scheduled',
                next_run_at=now,
                created_by=project.owner_hasn_id,
                task_uuid=task_uuid,
                executor_policy='local_node',
                task_revision=1,
                created_by_kind='owner',
                risk_level='low',
                project_id=project.platform_project_id,
                app_id='growth',
                execution_kind='freeform',
                execution_spec={'prompt': prompt},
            )
            .on_conflict_do_nothing(index_elements=[HasnTask.task_uuid])
        )
        if customer.followup_task_id is None:
            customer.followup_task_id = task_uuid
        return task_uuid

    @staticmethod
    async def _record_inbound_attribution(
        db: AsyncSession,
        *,
        project: GrowthProject,
        contact: LeadContact,
        customer: Customer,
        submission: FormSubmission,
        binding: dict[str, Any],
    ) -> None:
        """追加 first/last touch：首次触点固定一次，最近触点按每次提交追加。"""
        base_metadata = {
            'publish_site_id': binding['site_id'],
            'landing_revision_id': binding['revision_id'],
            'form_ref': binding['form_ref'],
            'utm_source_hmac': submission.utm_source,
            'utm_medium_hmac': submission.utm_medium,
            'utm_campaign_hmac': submission.utm_campaign,
            'referrer_origin': submission.referrer,
        }
        values = [
            {
                'growth_project_id': project.id,
                'event_type': 'inbound',
                'lead_contact_id': contact.id,
                'customer_id': customer.id,
                'source_kind': 'inbound_form',
                'source_ref': f'hasn://publish/sites/{binding["site_id"]}',
                'campaign_ref': submission.utm_campaign,
                'occurred_time': submission.consent_at,
                'idempotency_key': f'inbound:first:{customer.id}',
                'meta_data': {**base_metadata, 'touch_model': 'first_touch'},
            },
            {
                'growth_project_id': project.id,
                'event_type': 'inbound',
                'lead_contact_id': contact.id,
                'customer_id': customer.id,
                'source_kind': 'inbound_form',
                'source_ref': f'hasn://publish/sites/{binding["site_id"]}',
                'campaign_ref': submission.utm_campaign,
                'occurred_time': submission.consent_at,
                'idempotency_key': f'inbound:last:{submission.id}',
                'meta_data': {**base_metadata, 'touch_model': 'last_touch'},
            },
        ]
        for value in values:
            await db.execute(
                pg_insert(GrowthAttributionEvent)
                .values(**value)
                .on_conflict_do_nothing(
                    index_elements=[
                        GrowthAttributionEvent.growth_project_id,
                        GrowthAttributionEvent.idempotency_key,
                    ]
                )
            )

    @staticmethod
    async def _enforce_rate_limit(
        *,
        publish_ref: str,
        ip_hmac: str,
        identity_hmac: str,
    ) -> None:
        """按站点+IP 与站点+身份双维固定窗口限流；Redis 故障显式 503。"""
        window = max(1, settings.GROWTH_FORM_RATE_WINDOW_SECONDS)
        dimensions = (
            (f'growth:form:rate:ip:{publish_ref}:{ip_hmac}', max(1, settings.GROWTH_FORM_RATE_IP_MAX)),
            (
                f'growth:form:rate:identity:{publish_ref}:{identity_hmac}',
                max(1, settings.GROWTH_FORM_RATE_IDENTITY_MAX),
            ),
        )
        try:
            for key, limit in dimensions:
                count = await redis_client.incr(key)
                if count == 1:
                    await redis_client.expire(key, window)
                if count > limit:
                    retry_after = await redis_client.ttl(key)
                    raise errors.HTTPError(
                        code=429,
                        msg='提交过于频繁，请稍后重试',
                        headers={'Retry-After': str(max(1, retry_after))},
                    )
        except errors.HTTPError:
            raise
        except Exception as exc:
            raise errors.RequestError(
                code=StandardResponseCode.HTTP_503,
                msg='表单安全服务暂时不可用，请稍后重试',
                data={'error_code': 'GROWTH_FORM_RATE_LIMIT_UNAVAILABLE'},
            ) from exc

    @staticmethod
    def _submission_result(
        submission: FormSubmission,
        *,
        keyring: GrowthPiiKeyring,
    ) -> dict[str, Any]:
        """公开响应不暴露 CRM/客户 ID；spam 与有效留资返回同形状。"""
        receipt_input = f'{submission.publish_site_id}:{submission.id}'
        return {
            'status': 'received',
            'receipt_ref': keyring.hmac_for('form_receipt', receipt_input)[:24],
        }

    @staticmethod
    async def _upsert_inbound_customer(
        db: AsyncSession,
        *,
        user_id: int,
        growth_project_id: str,
        company: str | None,
        lead_contact_id: int,
    ) -> Customer:
        """客户只引用全局联系人事实，旧姓名和渠道列恒为 NULL。"""
        now = timezone.now()
        insert = pg_insert(Customer).values(
            customer_no=_gen_no('CUS'),
            user_id=user_id,
            growth_project_id=growth_project_id,
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
                        index_elements=[
                            Customer.growth_project_id,
                            Customer.lead_contact_id,
                        ],
                        index_where=sa.and_(
                            Customer.growth_project_id.is_not(None),
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
        form_access_token: str,
        idempotency_key: str,
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
        try:
            normalized_idempotency_key = str(UUID(idempotency_key.strip()))
        except (ValueError, AttributeError) as exc:
            raise errors.RequestError(
                msg='Idempotency-Key 必须是有效 UUID',
                data={'error_code': 'GROWTH_FORM_IDEMPOTENCY_KEY_INVALID'},
            ) from exc
        keyring = require_growth_pii_keyring()
        await ensure_growth_pii_key_write_fence(db, keyring=keyring)
        binding = await publish_provider.resolve_form_access(
            publish_ref=publish_ref,
            form_access_token=form_access_token,
        )
        user_id, owner_hasn_id, project = await cls._resolve_owner(
            db,
            binding=binding,
        )

        email = _clean_optional(data.get('email'), max_length=254)
        phone = _clean_optional(data.get('phone'), max_length=32)
        wechat = _clean_optional(data.get('wechat'), max_length=64)
        company = _clean_business_metadata(data.get('company_name'), max_length=200)
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
            or consent_purpose != 'sales_contact'
            or data.get('consent') is not True
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
        source_ref_pending = f'publish_site:{binding["site_id"]}'
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
                'site_id': binding['site_id'],
                'revision_id': binding['revision_id'],
                'form_ref': binding['form_ref'],
                'contact_name': contact_name,
                'email': email,
                'phone': phone,
                'wechat': wechat,
                'company': company,
                'message': message,
                'privacy_notice_version': notice_version,
                'consent_purpose': consent_purpose,
                'consent': True,
                'website_url': _clean_optional(data.get('website_url'), max_length=2048),
                'utm': data.get('utm') if isinstance(data.get('utm'), dict) else {},
            },
            ensure_ascii=False,
            sort_keys=True,
            separators=(',', ':'),
        )
        fingerprint = f'v{keyring.active_hmac_version}:{keyring.hmac_for("form_submission", fingerprint_payload)}'

        existing_submission = (
            await db.execute(
                sa.select(FormSubmission).where(
                    FormSubmission.publish_site_id == binding['site_id'],
                    FormSubmission.idempotency_key == normalized_idempotency_key,
                )
            )
        ).scalar_one_or_none()
        if existing_submission is not None:
            if existing_submission.submission_fingerprint != fingerprint:
                raise errors.ConflictError(
                    msg='同一 Idempotency-Key 不能提交不同内容',
                    data={'error_code': 'GROWTH_FORM_IDEMPOTENCY_CONFLICT'},
                )
            return cls._submission_result(existing_submission, keyring=keyring)

        normalized_ip = _clean_optional(client_ip, max_length=128) or 'unknown'
        ip_hmac = f'v{keyring.active_hmac_version}:{keyring.hmac_for("ip", normalized_ip)}'
        identity_payload = json.dumps(
            {
                'email': email.casefold() if email else None,
                'phone': ''.join(char for char in phone or '' if char.isdigit() or char == '+') or None,
                'wechat': wechat.casefold() if wechat else None,
            },
            ensure_ascii=False,
            sort_keys=True,
            separators=(',', ':'),
        )
        identity_hmac = (
            f'v{keyring.active_hmac_version}:'
            f'{keyring.hmac_for("form_identity", identity_payload)}'
        )
        await cls._enforce_rate_limit(
            publish_ref=publish_ref,
            ip_hmac=ip_hmac,
            identity_hmac=identity_hmac,
        )

        raw_utm = data.get('utm')
        submitted_utm: dict[str, Any] = raw_utm if isinstance(raw_utm, dict) else {}
        utm_source_values = {
            'utm_source': submitted_utm.get('source'),
            'utm_medium': submitted_utm.get('medium'),
            'utm_campaign': submitted_utm.get('campaign'),
            'utm_content': submitted_utm.get('content'),
            'utm_term': submitted_utm.get('term'),
        }
        utm = {
            key: _hmac_untrusted_metadata(
                keyring,
                field=key,
                value=utm_source_values.get(key),
                max_length=255,
            )
            for key in _FORM_UTM_KEYS
        }

        insert_submission = (
            pg_insert(FormSubmission)
            .values(
                user_id=user_id,
                growth_project_id=project.id,
                platform_project_id=project.platform_project_id,
                publish_ref=publish_ref,
                publish_site_id=binding['site_id'],
                idempotency_key=normalized_idempotency_key,
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
            .on_conflict_do_nothing(
                index_elements=[
                    FormSubmission.publish_site_id,
                    FormSubmission.idempotency_key,
                ],
                index_where=sa.text('publish_site_id IS NOT NULL AND idempotency_key IS NOT NULL'),
            )
            .returning(FormSubmission.id)
        )
        submission_id = (await db.execute(insert_submission)).scalar_one_or_none()
        if submission_id is None:
            raced = (
                await db.execute(
                    sa.select(FormSubmission).where(
                        FormSubmission.publish_site_id == binding['site_id'],
                        FormSubmission.idempotency_key == normalized_idempotency_key,
                    )
                )
            ).scalar_one()
            if raced.submission_fingerprint != fingerprint:
                raise errors.ConflictError(
                    msg='同一 Idempotency-Key 不能提交不同内容',
                    data={'error_code': 'GROWTH_FORM_IDEMPOTENCY_CONFLICT'},
                )
            return cls._submission_result(raced, keyring=keyring)
        submission = await db.get(FormSubmission, int(submission_id))
        if submission is None:
            raise errors.ServerError(msg='表单提交写入后无法读取')

        if is_spam:
            return cls._submission_result(submission, keyring=keyring)

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
            growth_project_id=str(project.id),
            company=company,
            lead_contact_id=contact.id,
        )
        project_lead = await cls._ensure_project_lead(
            db,
            project=project,
            contact=contact,
            submission_id=submission.id,
            source_meta={
                'publish_site_id': binding['site_id'],
                'landing_revision_id': binding['revision_id'],
                'form_ref': binding['form_ref'],
                'identity_hmac': identity_hmac,
            },
        )
        task_id = await cls._ensure_followup_task(
            db,
            project=project,
            customer=customer,
            submission_id=submission.id,
        )
        submission.customer_id = customer.id
        submission.lead_contact_id = contact.id
        submission.project_lead_id = project_lead.id
        submission.contact_private_profile_id = private['private_profile_id']
        submission.contact_channel_ids = [channel['id'] for channel in private['channels']]
        submission.task_id = task_id
        submission.status = 'converted'
        await db.flush()

        await GrowthFunnelService._add_activity(
            db,
            user_id=user_id,
            customer_id=customer.id,
            kind='inbound',
            content='落地页留资已进入待处理队列',
            actor_kind='owner',
            actor_id=None,
            ref_table='form_submission',
            ref_id=str(submission.id),
        )
        await cls._record_inbound_attribution(
            db,
            project=project,
            contact=contact,
            customer=customer,
            submission=submission,
            binding=binding,
        )
        await growth_notification_service.inbound_form_received(
            db,
            owner_hasn_id=owner_hasn_id,
            customer_id=customer.id,
            project_lead_id=project_lead.id,
            submission_id=submission.id,
            task_ready=task_id is not None,
        )
        return cls._submission_result(submission, keyring=keyring)


growth_form_service = GrowthFormService()
