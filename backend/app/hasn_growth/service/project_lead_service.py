"""S6 项目线索权威服务。

公共 `contact` 只保存可复用、非敏感企业事实；姓名、职位和联系方式通过既有
`contact_private_profile/contact_channel` 加密接缝按个人或企业主体隔离。项目状态、
评分、解释、来源和批次幂等信息只落 `growth_project_lead`。
"""

from __future__ import annotations

import hashlib
import json
import re

from datetime import datetime, timedelta
from decimal import Decimal
from typing import TYPE_CHECKING, Any, Literal
from urllib.parse import urlsplit
from uuid import NAMESPACE_URL, uuid5

import sqlalchemy as sa

from pydantic import ValidationError
from sqlalchemy.dialects.postgresql import insert as pg_insert

from backend.app.hasn.model.hasn_enterprise_membership import HasnEnterpriseMembership
from backend.app.hasn_core import HasnHumans
from backend.app.hasn_growth.model.activity import Activity
from backend.app.hasn_growth.model.contact_private_profile import ContactPrivateProfile
from backend.app.hasn_growth.model.customer import Customer
from backend.app.hasn_growth.model.growth_attribution_event import (
    GrowthAttributionEvent,
)
from backend.app.hasn_growth.model.growth_project import GrowthProject
from backend.app.hasn_growth.model.growth_project_lead import GrowthProjectLead
from backend.app.hasn_growth.model.growth_project_playbook import GrowthProjectPlaybook
from backend.app.hasn_growth.model.lead_contact import LeadContact
from backend.app.hasn_growth.schema.project_lead import ProjectLeadIngestItem
from backend.app.hasn_growth.service.contact_privacy_service import (
    ContactChannelWrite,
    contact_privacy_service,
)
from backend.app.hasn_growth.service.funnel_service import masked_customer_response
from backend.app.hasn_growth.service.pii import redact_pii_value
from backend.app.hasn_growth.service.pii_boundary import (
    GrowthPiiBoundaryError,
    assert_growth_pii_payload_safe,
)
from backend.app.hasn_growth.service.pii_keyring import require_growth_pii_keyring
from backend.app.hasn_growth.service.scope_context import (
    GrowthScope,
    apply_scope,
    can_manage_assignment,
)
from backend.app.hasn_task.model.task import HasnTask
from backend.common.exception import errors
from backend.utils.timezone import timezone

if TYPE_CHECKING:
    from collections.abc import Sequence
    from uuid import UUID

    from sqlalchemy.ext.asyncio import AsyncSession

_FRESHNESS_WINDOW = timedelta(days=90)
_BATCH_META_KEY = '_ingest'
_OWNER_ROLES = ('owner', 'admin')
_CUSTOMER_SOURCE_KIND = {
    'firecrawl': 'outbound_crawl',
    'crawl': 'outbound_crawl',
    'web': 'outbound_crawl',
    'controlled_import': 'manual',
    'manual': 'manual',
    'inbound_form': 'inbound_form',
    'community': 'community',
}


def _clean_text(value: str | None) -> str | None:
    """清理空白并移除自由文本中的邮箱、电话，确保公共事实不含 PII。"""
    cleaned = value.strip() if value else ''
    if not cleaned:
        return None
    redacted = redact_pii_value(cleaned)
    return str(redacted) if redacted else None


def _public_url(value: str | None) -> str | None:
    """公共 URL 只保留 scheme、host 和 path，不保留凭据、查询或片段。"""
    cleaned = value.strip() if value else ''
    if not cleaned:
        return None
    candidate = cleaned if cleaned.startswith(('http://', 'https://')) else f'https://{cleaned}'
    try:
        parsed = urlsplit(candidate)
        if not parsed.hostname:
            return None
        port = f':{parsed.port}' if parsed.port else ''
    except ValueError:
        return None
    safe_path = re.sub(
        r'[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}',
        '',
        parsed.path or '/',
    )
    return f'{parsed.scheme}://{parsed.hostname.casefold()}{port}{safe_path}'[:500]


def _domain(value: str | None, *, website: str | None) -> str | None:
    """从显式域名或安全网站 URL 提取规范 host。"""
    for candidate in (value, website):
        cleaned = candidate.strip() if candidate else ''
        if not cleaned:
            continue
        parsed = urlsplit(cleaned if cleaned.startswith(('http://', 'https://')) else f'https://{cleaned}')
        if parsed.hostname:
            return parsed.hostname.casefold().removeprefix('www.')[:255]
    return None


def _normalized_fact(value: str | None) -> str:
    """把公共企业事实收敛为不受空白、大小写影响的稳定片段。"""
    return re.sub(r'\s+', ' ', (value or '').strip()).casefold()


def _fact_dedupe_key(
    *,
    domain: str | None,
    company_name: str | None,
    country: str | None,
    region: str | None,
    city: str | None,
) -> str:
    """优先按企业域名去重；无域名时按企业名与地域组合去重。"""
    if domain:
        canonical = f'domain:{domain.casefold()}'
    else:
        company = _normalized_fact(company_name)
        if not company:
            raise errors.RequestError(
                msg='缺少可去重的企业域名或企业名称',
                data={'error_code': 'LEAD_PUBLIC_FACT_REQUIRED'},
            )
        canonical = '|'.join((
            f'company:{company}',
            f'country:{_normalized_fact(country)}',
            f'region:{_normalized_fact(region)}',
            f'city:{_normalized_fact(city)}',
        ))
    return hashlib.sha256(canonical.encode()).hexdigest()


def _canonical_payload_hash(item: ProjectLeadIngestItem) -> str:
    """批次幂等只哈希项目公开字段；禁止把低熵 PII 派生值落入元数据。"""
    payload = item.model_dump(
        mode='json',
        exclude={'private_contact'},
        exclude_none=True,
    )
    serialized = json.dumps(
        payload,
        ensure_ascii=False,
        sort_keys=True,
        separators=(',', ':'),
    )
    return hashlib.sha256(serialized.encode()).hexdigest()


def _serialize_datetime(value: datetime | None) -> str | None:
    return value.isoformat() if value is not None else None


class ProjectLeadService:
    """项目线索批次入池、分页读取和状态流转。"""

    async def require_project(
        self,
        db: AsyncSession,
        *,
        growth_project_id: str | UUID,
        scope: GrowthScope,
        require_writable: bool = False,
    ) -> GrowthProject:
        """按当前主人或企业边界加载项目，供项目化下游服务复用。"""
        return await self._require_project(
            db,
            growth_project_id=growth_project_id,
            scope=scope,
            require_writable=require_writable,
        )

    @staticmethod
    async def _require_project(
        db: AsyncSession,
        *,
        growth_project_id: str | UUID,
        scope: GrowthScope,
        require_writable: bool = False,
    ) -> GrowthProject:
        project = (
            await db.execute(sa.select(GrowthProject).where(GrowthProject.id == growth_project_id))
        ).scalar_one_or_none()
        if project is None:
            raise errors.NotFoundError(msg='获客项目不存在或无权访问')
        if scope.is_enterprise:
            allowed = project.owner_scope == 'enterprise' and project.enterprise_id == scope.enterprise_id
        else:
            allowed = (
                project.owner_scope == 'personal'
                and project.enterprise_id is None
                and project.user_id == scope.user_id
                and project.owner_hasn_id == scope.owner_hasn_id
            )
        if not allowed:
            raise errors.NotFoundError(msg='获客项目不存在或无权访问')
        if require_writable:
            if project.status != 'active':
                raise errors.ConflictError(
                    msg='获客项目当前不可写',
                    data={'error_code': 'GROWTH_PROJECT_NOT_ACTIVE'},
                )
            if project.provision_status != 'ready':
                raise errors.ConflictError(
                    msg='获客项目基础资源尚未就绪',
                    data={'error_code': 'GROWTH_PROJECT_NOT_READY'},
                )
        return project

    @staticmethod
    async def _can_reference_private_contact(
        db: AsyncSession,
        *,
        contact: LeadContact,
        scope: GrowthScope,
    ) -> bool:
        """私有池联系人只能由同一授权主体继续引用。"""
        if contact.pool_visibility == 'public':
            return True
        conditions = [
            ContactPrivateProfile.lead_contact_id == contact.id,
            ContactPrivateProfile.status == 'active',
            ContactPrivateProfile.retention_until > timezone.now(),
        ]
        if scope.is_enterprise:
            conditions.extend([
                ContactPrivateProfile.owner_scope == 'enterprise',
                ContactPrivateProfile.enterprise_id == scope.enterprise_id,
            ])
        else:
            conditions.extend([
                ContactPrivateProfile.owner_scope == 'personal',
                ContactPrivateProfile.user_id == scope.user_id,
            ])
        return await db.scalar(sa.select(ContactPrivateProfile.id).where(*conditions).limit(1)) is not None

    async def _upsert_public_contact(
        self,
        db: AsyncSession,
        *,
        item: ProjectLeadIngestItem,
        scope: GrowthScope,
    ) -> LeadContact:
        """按不含 PII 的企业事实 UPSERT 全局公共联系人。"""
        if item.lead_contact_id is not None:
            contact = await db.get(LeadContact, item.lead_contact_id)
            if contact is None or not await self._can_reference_private_contact(
                db,
                contact=contact,
                scope=scope,
            ):
                raise errors.NotFoundError(msg='联系人不存在或无权引用')
            return contact

        website = _public_url(item.website)
        domain = _domain(item.domain, website=website)
        company_name = _clean_text(item.company_name)
        country = _clean_text(item.country)
        region = _clean_text(item.region)
        city = _clean_text(item.city)
        industry = _clean_text(item.industry)
        fact_key = _fact_dedupe_key(
            domain=domain,
            company_name=company_name,
            country=country,
            region=region,
            city=city,
        )
        now = timezone.now()
        insert_statement = pg_insert(LeadContact).values(
            lead_no=f'LEAD{now.strftime("%Y%m%d%H%M%S")}{hashlib.sha1(fact_key.encode()).hexdigest()[:10].upper()}',
            pool_visibility='public',
            company_name=company_name,
            contact_name=None,
            email=None,
            email_normalized=None,
            phone=None,
            phone_normalized=None,
            website=website,
            domain=domain,
            country=country,
            region=region,
            city=city,
            address=None,
            industry=industry,
            source_type=item.source_kind,
            source_url=None,
            keyword=None,
            status='valid',
            confidence_score=Decimal(0),
            dedupe_key_email=None,
            dedupe_key_phone=None,
            dedupe_key_domain=(hashlib.sha256(domain.encode()).hexdigest() if domain else None),
            fact_dedupe_key=fact_key,
            normalization_version='public-fact-v1',
            first_seen_at=now,
            last_seen_at=now,
            archived_at=now + timedelta(days=540),
            meta_data={},
        )
        excluded = insert_statement.excluded
        statement = insert_statement.on_conflict_do_update(
            index_elements=[LeadContact.fact_dedupe_key],
            # 必须与部分唯一索引使用同一字面量谓词；若把 `public` 参数化，
            # asyncpg 在第六次切 generic prepared plan 后无法证明索引谓词成立。
            index_where=sa.text("pool_visibility = 'public' AND fact_dedupe_key IS NOT NULL"),
            set_={
                'company_name': sa.func.coalesce(
                    excluded.company_name,
                    LeadContact.company_name,
                ),
                'website': sa.func.coalesce(excluded.website, LeadContact.website),
                'domain': sa.func.coalesce(excluded.domain, LeadContact.domain),
                'country': sa.func.coalesce(excluded.country, LeadContact.country),
                'region': sa.func.coalesce(excluded.region, LeadContact.region),
                'city': sa.func.coalesce(excluded.city, LeadContact.city),
                'industry': sa.func.coalesce(excluded.industry, LeadContact.industry),
                'last_seen_at': now,
                'updated_time': now,
            },
        ).returning(LeadContact.id)
        contact_id = int((await db.execute(statement)).scalar_one())
        contact = await db.get(
            LeadContact,
            contact_id,
            populate_existing=True,
        )
        if contact is None:
            raise errors.ServerError(
                msg='公共联系人 UPSERT 后无法读取',
                data={'error_code': 'LEAD_PUBLIC_FACT_WRITE_LOST'},
            )
        return contact

    @staticmethod
    async def _store_private_contact(
        db: AsyncSession,
        *,
        contact: LeadContact,
        item: ProjectLeadIngestItem,
        scope: GrowthScope,
    ) -> None:
        private = item.private_contact
        if private is None:
            return
        await contact_privacy_service.store_private_contact(
            db,
            keyring=require_growth_pii_keyring(),
            lead_contact_id=contact.id,
            owner_scope='enterprise' if scope.is_enterprise else 'personal',
            user_id=None if scope.is_enterprise else scope.user_id,
            enterprise_id=scope.enterprise_id if scope.is_enterprise else None,
            contact_name=(private.contact_name or '').strip() or None,
            title=(private.title or '').strip() or None,
            lawful_basis=private.lawful_basis,
            source_ref=private.source_ref,
            retention_until=private.retention_until,
            channels=[
                ContactChannelWrite(
                    channel=channel.channel,
                    value=channel.value,
                    lawful_basis=channel.lawful_basis,
                    source_ref=channel.source_ref,
                    consent_ref=channel.consent_ref,
                    verified_at=channel.verified_at,
                    fresh_until=channel.fresh_until,
                    retention_until=private.retention_until,
                )
                for channel in private.channels
            ],
            preserve_existing=True,
            allow_profile_only=True,
        )

    @staticmethod
    def _project_values(
        *,
        project: GrowthProject,
        contact: LeadContact,
        item: ProjectLeadIngestItem,
        batch_id: str,
        payload_hash: str,
        scope: GrowthScope,
        actor_kind: str,
        actor_id: str,
    ) -> dict[str, Any]:
        source_meta = redact_pii_value(item.source_meta)
        assert_growth_pii_payload_safe(source_meta)
        source_meta = {
            **source_meta,
            _BATCH_META_KEY: {
                'batch_id': batch_id,
                'client_ref': item.client_ref,
                'payload_hash': payload_hash,
                'actor_kind': actor_kind,
                'actor_id': actor_id,
            },
        }
        return {
            'growth_project_id': project.id,
            'lead_contact_id': contact.id,
            'user_id': scope.user_id,
            'owner_scope': 'enterprise' if scope.is_enterprise else 'personal',
            'enterprise_id': scope.enterprise_id if scope.is_enterprise else None,
            'assignee': (
                None
                if scope.is_enterprise and item.source_kind == 'inbound_form'
                else scope.owner_hasn_id
                if scope.is_enterprise
                else None
            ),
            'source_kind': item.source_kind,
            'source_tool': item.source_tool,
            'source_ref': item.source_ref,
            'source_meta': source_meta,
            'ingest_batch_id': batch_id,
            'ingest_client_ref': item.client_ref,
            'match_score': (Decimal(str(item.match_score)) if item.match_score is not None else None),
            'score_breakdown': {
                key: component.model_dump(mode='json') for key, component in item.score_breakdown.items()
            },
            'scoring_version': item.scoring_version,
            'evidence_fresh_at': item.evidence_fresh_at,
        }

    @staticmethod
    def _ingest_result(
        *,
        project_lead: GrowthProjectLead,
        client_ref: str,
        action: Literal['inserted', 'updated', 'skipped'],
    ) -> dict[str, Any]:
        """统一批次逐行成功形状，避免重放与新写分支产生字段漂移。"""
        return {
            'client_ref': client_ref,
            'action': action,
            'project_lead_id': project_lead.id,
            'lead_contact_id': project_lead.lead_contact_id,
            'status': project_lead.status,
            'assignee': project_lead.assignee,
        }

    @staticmethod
    def _assert_batch_replay(
        *,
        project_lead: GrowthProjectLead,
        payload_hash: str,
    ) -> None:
        """同一项目、批次和 client_ref 只能重放同一份公开事实。"""
        previous_ingest = dict(project_lead.source_meta or {}).get(_BATCH_META_KEY)
        if not isinstance(previous_ingest, dict) or previous_ingest.get('payload_hash') != payload_hash:
            raise errors.ConflictError(
                msg='同一批次条目内容发生变化',
                data={'error_code': 'LEAD_BATCH_ITEM_CONFLICT'},
            )

    @staticmethod
    async def _find_batch_item(
        db: AsyncSession,
        *,
        project_id: UUID,
        batch_id: str,
        client_ref: str,
    ) -> GrowthProjectLead | None:
        """按数据库唯一批次键读取关联行，并锁定后续状态判定。"""
        return (
            await db.execute(
                sa
                .select(GrowthProjectLead)
                .where(
                    GrowthProjectLead.growth_project_id == project_id,
                    GrowthProjectLead.ingest_batch_id == batch_id,
                    GrowthProjectLead.ingest_client_ref == client_ref,
                )
                .with_for_update()
            )
        ).scalar_one_or_none()

    async def _ingest_item(
        self,
        db: AsyncSession,
        *,
        project: GrowthProject,
        batch_id: str,
        item: ProjectLeadIngestItem,
        scope: GrowthScope,
        actor_kind: str,
        actor_id: str,
    ) -> dict[str, Any]:
        payload_hash = _canonical_payload_hash(item)
        batch_existing = await self._find_batch_item(
            db,
            project_id=project.id,
            batch_id=batch_id,
            client_ref=item.client_ref,
        )
        if batch_existing is not None:
            self._assert_batch_replay(
                project_lead=batch_existing,
                payload_hash=payload_hash,
            )
            return self._ingest_result(
                project_lead=batch_existing,
                client_ref=item.client_ref,
                action='skipped',
            )

        contact = await self._upsert_public_contact(db, item=item, scope=scope)
        values = self._project_values(
            project=project,
            contact=contact,
            item=item,
            batch_id=batch_id,
            payload_hash=payload_hash,
            scope=scope,
            actor_kind=actor_kind,
            actor_id=actor_id,
        )
        existing_by_contact = (
            await db.execute(
                sa
                .select(GrowthProjectLead)
                .where(
                    GrowthProjectLead.growth_project_id == project.id,
                    GrowthProjectLead.lead_contact_id == contact.id,
                )
                .with_for_update()
            )
        ).scalar_one_or_none()
        action: Literal['inserted', 'updated', 'skipped']
        if existing_by_contact is None:
            insert_statement = (
                pg_insert(GrowthProjectLead)
                .values(
                    **values,
                    status='new',
                    dismiss_reason=None,
                    note=None,
                    acquired_at=timezone.now(),
                )
                .on_conflict_do_nothing()
                .returning(GrowthProjectLead.id)
            )
            inserted_id = (await db.execute(insert_statement)).scalar_one_or_none()
            if inserted_id is not None:
                project_lead = await db.get(GrowthProjectLead, inserted_id)
                if project_lead is None:
                    raise errors.ServerError(
                        msg='项目线索写入后无法读取',
                        data={'error_code': 'PROJECT_LEAD_WRITE_LOST'},
                    )
                action = 'inserted'
            else:
                project_lead = await self._find_batch_item(
                    db,
                    project_id=project.id,
                    batch_id=batch_id,
                    client_ref=item.client_ref,
                )
                if project_lead is not None:
                    self._assert_batch_replay(
                        project_lead=project_lead,
                        payload_hash=payload_hash,
                    )
                    return self._ingest_result(
                        project_lead=project_lead,
                        client_ref=item.client_ref,
                        action='skipped',
                    )
                project_lead = (
                    await db.execute(
                        sa
                        .select(GrowthProjectLead)
                        .where(
                            GrowthProjectLead.growth_project_id == project.id,
                            GrowthProjectLead.lead_contact_id == contact.id,
                        )
                        .with_for_update()
                    )
                ).scalar_one()
                for field, value in values.items():
                    if field in {'growth_project_id', 'lead_contact_id'}:
                        continue
                    setattr(project_lead, field, value)
                project_lead.updated_time = timezone.now()
                action = 'updated'
        else:
            project_lead = existing_by_contact
            for field, value in values.items():
                if field in {'growth_project_id', 'lead_contact_id'}:
                    continue
                setattr(project_lead, field, value)
            project_lead.updated_time = timezone.now()
            action = 'updated'
        await self._store_private_contact(
            db,
            contact=contact,
            item=item,
            scope=scope,
        )
        await db.flush()
        await db.execute(
            pg_insert(GrowthAttributionEvent)
            .values(
                growth_project_id=project.id,
                event_type='lead_acquired',
                lead_contact_id=project_lead.lead_contact_id,
                source_kind=project_lead.source_kind,
                source_ref=project_lead.source_ref,
                campaign_ref=(project_lead.source_meta or {}).get('campaign'),
                occurred_time=project_lead.acquired_at,
                idempotency_key=f'lead_acquired:{project_lead.id}',
                meta_data={
                    'project_lead_id': project_lead.id,
                    'batch_id': batch_id,
                    'source_tool': project_lead.source_tool,
                },
            )
            .on_conflict_do_nothing(
                index_elements=[
                    GrowthAttributionEvent.growth_project_id,
                    GrowthAttributionEvent.idempotency_key,
                ]
            )
        )
        return self._ingest_result(
            project_lead=project_lead,
            client_ref=item.client_ref,
            action=action,
        )

    async def ingest_batch(
        self,
        db: AsyncSession,
        *,
        growth_project_id: str | UUID,
        batch_id: str,
        items: Sequence[dict[str, Any]],
        scope: GrowthScope,
        actor_kind: str,
        actor_id: str,
    ) -> dict[str, Any]:
        """逐条校验并写入稳定批次；已知条目错误不拖垮同批合法行。"""
        batch_id = batch_id.strip()
        if (
            not batch_id
            or len(batch_id) > 64
            or not re.fullmatch(
                r'[A-Za-z0-9][A-Za-z0-9._:-]*',
                batch_id,
            )
        ):
            raise errors.RequestError(msg='batch_id 格式无效')
        if not 1 <= len(items) <= 100:
            raise errors.RequestError(msg='单批次线索数量必须为 1–100')
        if actor_kind not in {'owner', 'agent', 'system'} or not actor_id.strip():
            raise errors.RequestError(msg='线索批次 actor 无效')
        project = await self._require_project(
            db,
            growth_project_id=growth_project_id,
            scope=scope,
            require_writable=True,
        )
        counters = {'inserted': 0, 'updated': 0, 'skipped': 0}
        results: list[dict[str, Any]] = []
        failures: list[dict[str, Any]] = []
        seen_client_refs: set[str] = set()
        for index, raw_item in enumerate(items):
            client_ref = str(raw_item.get('client_ref') or '').strip()
            try:
                item = ProjectLeadIngestItem.model_validate(raw_item)
                if item.client_ref in seen_client_refs:
                    raise ValueError('同一批次 client_ref 不能重复')
                seen_client_refs.add(item.client_ref)
                async with db.begin_nested():
                    result = await self._ingest_item(
                        db,
                        project=project,
                        batch_id=batch_id,
                        item=item,
                        scope=scope,
                        actor_kind=actor_kind,
                        actor_id=actor_id.strip(),
                    )
                counters[result['action']] += 1
                results.append(result)
            except (ValidationError, ValueError, GrowthPiiBoundaryError):
                failures.append({
                    'index': index,
                    'client_ref': client_ref or None,
                    'code': 'LEAD_ITEM_INVALID',
                    'message': '线索条目校验失败',
                })
            except (errors.RequestError, errors.ConflictError) as exc:
                error_code = exc.data.get('error_code') if isinstance(exc.data, dict) else None
                failures.append({
                    'index': index,
                    'client_ref': client_ref or None,
                    'code': error_code or 'LEAD_ITEM_REJECTED',
                    'message': exc.msg or '线索条目被拒绝',
                })
        return {
            'growth_project_id': str(project.id),
            'platform_project_id': str(project.platform_project_id),
            'batch_id': batch_id,
            **counters,
            'error_count': len(failures),
            'items': results,
            'errors': failures,
        }

    @staticmethod
    def _freshness(evidence_fresh_at: datetime | None) -> str:
        if evidence_fresh_at is None:
            return 'unknown'
        return 'fresh' if evidence_fresh_at >= timezone.now() - _FRESHNESS_WINDOW else 'stale'

    async def _lead_view(
        self,
        db: AsyncSession,
        *,
        project_lead: GrowthProjectLead,
        contact: LeadContact,
        scope: GrowthScope,
    ) -> dict[str, Any]:
        private_contact = await contact_privacy_service.find_masked_contact_for_lead(
            db,
            lead_contact_id=contact.id,
            owner_scope='enterprise' if scope.is_enterprise else 'personal',
            user_id=None if scope.is_enterprise else scope.user_id,
            enterprise_id=scope.enterprise_id if scope.is_enterprise else None,
        )
        return {
            'id': project_lead.id,
            'growth_project_id': str(project_lead.growth_project_id),
            'lead_contact_id': contact.id,
            'lead_no': contact.lead_no,
            'company_name': contact.company_name,
            'website': contact.website,
            'domain': contact.domain,
            'industry': contact.industry,
            'country': contact.country,
            'region': contact.region,
            'city': contact.city,
            'contact_name': (private_contact.get('contact_name') if private_contact else None),
            'title': private_contact.get('title') if private_contact else None,
            'channels': private_contact.get('channels', []) if private_contact else [],
            'status': project_lead.status,
            'dismiss_reason': project_lead.dismiss_reason,
            'note': project_lead.note,
            'assignee': project_lead.assignee,
            'source_kind': project_lead.source_kind,
            'source_tool': project_lead.source_tool,
            'source_ref': project_lead.source_ref,
            'source_meta': project_lead.source_meta,
            'match_score': (float(project_lead.match_score) if project_lead.match_score is not None else None),
            'score_breakdown': project_lead.score_breakdown,
            'scoring_version': project_lead.scoring_version,
            'evidence_fresh_at': _serialize_datetime(project_lead.evidence_fresh_at),
            'evidence_freshness': self._freshness(project_lead.evidence_fresh_at),
            'acquired_at': _serialize_datetime(project_lead.acquired_at),
            'updated_time': _serialize_datetime(project_lead.updated_time),
        }

    @staticmethod
    def _validate_list_filters(
        *,
        page: int,
        size: int,
        status: str | None,
        min_score: float | None,
        freshness: str | None,
    ) -> None:
        """在构造查询前统一拒绝越界分页和未知筛选值。"""
        if page < 1 or not 1 <= size <= 100:
            raise errors.RequestError(msg='分页参数无效')
        if status not in {None, 'new', 'qualified', 'dismissed'}:
            raise errors.RequestError(msg='线索状态筛选无效')
        if min_score is not None and not 0 <= min_score <= 100:
            raise errors.RequestError(msg='最低匹配分无效')
        if freshness not in {None, 'fresh', 'stale', 'unknown'}:
            raise errors.RequestError(msg='证据新鲜度筛选无效')

    @staticmethod
    def _apply_list_filters(
        statement: Any,
        *,
        scope: GrowthScope,
        status: str | None,
        query: str | None,
        min_score: float | None,
        freshness: str | None,
        assignee: str | None,
    ) -> Any:
        """把已校验筛选映射为服务端 SQL 条件，禁止前端全量加载后过滤。"""
        if status:
            statement = statement.where(GrowthProjectLead.status == status)
        if query and query.strip():
            pattern = f'%{query.strip()}%'
            statement = statement.where(
                sa.or_(
                    LeadContact.company_name.ilike(pattern),
                    LeadContact.domain.ilike(pattern),
                    LeadContact.industry.ilike(pattern),
                    LeadContact.region.ilike(pattern),
                    LeadContact.city.ilike(pattern),
                )
            )
        if min_score is not None:
            statement = statement.where(GrowthProjectLead.match_score >= min_score)
        if freshness == 'unknown':
            statement = statement.where(GrowthProjectLead.evidence_fresh_at.is_(None))
        elif freshness == 'fresh':
            statement = statement.where(GrowthProjectLead.evidence_fresh_at >= timezone.now() - _FRESHNESS_WINDOW)
        elif freshness == 'stale':
            statement = statement.where(
                GrowthProjectLead.evidence_fresh_at.is_not(None),
                GrowthProjectLead.evidence_fresh_at < timezone.now() - _FRESHNESS_WINDOW,
            )
        if assignee:
            if scope.is_enterprise and not scope.is_manager:
                raise errors.ForbiddenError(msg='仅企业经理可按负责人筛选')
            statement = statement.where(GrowthProjectLead.assignee == assignee)
        return statement

    async def list_project_leads(
        self,
        db: AsyncSession,
        *,
        growth_project_id: str | UUID,
        scope: GrowthScope,
        page: int,
        size: int,
        status: str | None = None,
        query: str | None = None,
        min_score: float | None = None,
        freshness: str | None = None,
        assignee: str | None = None,
    ) -> dict[str, Any]:
        """按项目与后端权限裁剪分页返回关联行，不把 contact 当项目私有行。"""
        project = await self._require_project(
            db,
            growth_project_id=growth_project_id,
            scope=scope,
        )
        self._validate_list_filters(
            page=page,
            size=size,
            status=status,
            min_score=min_score,
            freshness=freshness,
        )

        statement = (
            sa
            .select(GrowthProjectLead, LeadContact)
            .join(LeadContact, LeadContact.id == GrowthProjectLead.lead_contact_id)
            .where(GrowthProjectLead.growth_project_id == project.id)
        )
        statement = apply_scope(
            statement,
            GrowthProjectLead,
            user_id=scope.user_id,
            scope=scope,
        )
        statement = self._apply_list_filters(
            statement,
            scope=scope,
            status=status,
            query=query,
            min_score=min_score,
            freshness=freshness,
            assignee=assignee,
        )

        count_statement = sa.select(sa.func.count()).select_from(statement.order_by(None).subquery())
        total = int((await db.execute(count_statement)).scalar_one())
        rows = (
            await db.execute(
                statement
                .order_by(
                    GrowthProjectLead.match_score.desc().nullslast(),
                    GrowthProjectLead.id.desc(),
                )
                .offset((page - 1) * size)
                .limit(size)
            )
        ).all()
        return {
            'items': [
                await self._lead_view(
                    db,
                    project_lead=project_lead,
                    contact=contact,
                    scope=scope,
                )
                for project_lead, contact in rows
            ],
            'total': total,
            'page': page,
            'size': size,
            'scope': scope.to_meta(),
        }

    async def _load_scoped_lead(
        self,
        db: AsyncSession,
        *,
        project: GrowthProject,
        project_lead_id: int,
        scope: GrowthScope,
        write_lock: bool,
    ) -> GrowthProjectLead:
        statement = sa.select(GrowthProjectLead).where(
            GrowthProjectLead.id == project_lead_id,
            GrowthProjectLead.growth_project_id == project.id,
        )
        statement = apply_scope(
            statement,
            GrowthProjectLead,
            user_id=scope.user_id,
            scope=scope,
        )
        if write_lock:
            statement = statement.with_for_update()
        project_lead = (await db.execute(statement)).scalar_one_or_none()
        if project_lead is None:
            raise errors.NotFoundError(msg='项目线索不存在或无权访问')
        return project_lead

    async def change_lead_status(
        self,
        db: AsyncSession,
        *,
        growth_project_id: str | UUID,
        project_lead_id: int,
        action: Literal['dismiss', 'restore'],
        reason: str | None,
        scope: GrowthScope,
    ) -> dict[str, Any]:
        """忽略与恢复共用一个受控状态写点。"""
        project = await self._require_project(
            db,
            growth_project_id=growth_project_id,
            scope=scope,
            require_writable=True,
        )
        project_lead = await self._load_scoped_lead(
            db,
            project=project,
            project_lead_id=project_lead_id,
            scope=scope,
            write_lock=True,
        )
        if project_lead.status == 'qualified':
            raise errors.ConflictError(
                msg='已晋级线索不能忽略或恢复',
                data={'error_code': 'LEAD_ALREADY_QUALIFIED'},
            )
        if action == 'dismiss':
            safe_reason = _clean_text(reason)
            if not safe_reason:
                raise errors.RequestError(msg='忽略线索必须填写原因')
            project_lead.status = 'dismissed'
            project_lead.dismiss_reason = safe_reason
        elif action == 'restore':
            project_lead.status = 'new'
            project_lead.dismiss_reason = None
        else:
            raise errors.RequestError(msg='线索状态动作无效')
        project_lead.updated_time = timezone.now()
        await db.flush()
        return {
            'id': project_lead.id,
            'growth_project_id': str(project_lead.growth_project_id),
            'lead_contact_id': project_lead.lead_contact_id,
            'status': project_lead.status,
            'dismiss_reason': project_lead.dismiss_reason,
        }

    async def qualify_project_lead(
        self,
        db: AsyncSession,
        *,
        growth_project_id: str | UUID,
        project_lead_id: int,
        scope: GrowthScope,
        profile: dict[str, Any] | None,
        intent_score: float | None,
        actor_kind: Literal['owner', 'agent'],
        actor_id: str,
    ) -> dict[str, Any]:
        """在同一事务内把项目线索晋级为客户，并建立首个接续链路。"""
        normalized_actor_id = actor_id.strip()
        if actor_kind not in {'owner', 'agent'} or not normalized_actor_id:
            raise errors.RequestError(msg='晋级操作者无效')
        if intent_score is not None and not 0 <= intent_score <= 100:
            raise errors.RequestError(msg='意向分必须介于 0 到 100')

        project = await self._require_project(
            db,
            growth_project_id=growth_project_id,
            scope=scope,
            require_writable=True,
        )
        project_lead = await self._load_scoped_lead(
            db,
            project=project,
            project_lead_id=project_lead_id,
            scope=scope,
            write_lock=True,
        )
        if project_lead.status == 'dismissed':
            raise errors.ConflictError(
                msg='已忽略线索需先恢复再晋级',
                data={'error_code': 'LEAD_DISMISSED'},
            )

        contact = await db.get(LeadContact, project_lead.lead_contact_id)
        if contact is None:
            raise errors.NotFoundError(msg='项目线索关联的公共联系人不存在')

        customer = (
            await db.execute(
                sa.select(Customer).where(
                    Customer.growth_project_id == project.id,
                    Customer.lead_contact_id == project_lead.lead_contact_id,
                )
            )
        ).scalar_one_or_none()
        task_agent_id = normalized_actor_id if actor_kind == 'agent' else (project.owner_agent_id or '').strip()
        if customer is None and not task_agent_id:
            raise errors.ConflictError(
                msg='获客项目尚未绑定负责分身，无法建立接续任务',
                data={'error_code': 'GROWTH_PROJECT_AGENT_REQUIRED'},
            )

        now = timezone.now()
        next_followup_at = now + timedelta(days=1)
        safe_profile = redact_pii_value(profile or {})
        assert_growth_pii_payload_safe(safe_profile)
        playbook = (
            await db.execute(
                sa
                .select(GrowthProjectPlaybook)
                .where(
                    GrowthProjectPlaybook.growth_project_id == project.id,
                    GrowthProjectPlaybook.status == 'active',
                )
                .order_by(GrowthProjectPlaybook.id.desc())
                .limit(1)
            )
        ).scalar_one_or_none()

        if customer is None:
            score = Decimal(
                str(
                    intent_score
                    if intent_score is not None
                    else (project_lead.match_score or contact.confidence_score or 0)
                )
            )
            customer_no = 'CUS' + hashlib.sha256(f'{project.id}:{project_lead.id}'.encode()).hexdigest()[:12].upper()
            customer = Customer(
                customer_no=customer_no,
                user_id=scope.user_id,
                growth_project_id=project.id,
                lead_contact_id=project_lead.lead_contact_id,
                source_kind=_CUSTOMER_SOURCE_KIND.get(
                    project_lead.source_kind or contact.source_type or 'manual',
                    'outbound_crawl',
                ),
                company_name=contact.company_name,
                contact_name=None,
                email=None,
                phone=None,
                wechat=None,
                im_refs={},
                profile_json=safe_profile,
                intent_score=score,
                lifecycle_status='active',
                owner_agent_id=task_agent_id,
                owner_scope='enterprise' if scope.is_enterprise else 'personal',
                enterprise_id=scope.enterprise_id if scope.is_enterprise else None,
                assignee=(project_lead.assignee or scope.owner_hasn_id if scope.is_enterprise else None),
                tags=[],
                last_activity_at=now,
                next_followup_at=next_followup_at,
                silent_round_count=0,
            )
            db.add(customer)
            await db.flush()

        task_uuid = customer.followup_task_id
        if not task_uuid:
            if not task_agent_id:
                raise errors.ConflictError(
                    msg='客户尚无接续任务且项目未绑定负责分身',
                    data={'error_code': 'GROWTH_PROJECT_AGENT_REQUIRED'},
                )
            task_uuid = str(
                uuid5(
                    NAMESPACE_URL,
                    f'hasn:growth:followup:{project.id}:{project_lead.id}',
                )
            )
            task_prompt = (
                f'跟进获客项目 {project.id} 的客户 {customer.id}。'
                '先读取客户脱敏详情、最近活动和当前打法，再提出下一步合规跟进建议；'
                '任何对外发送必须进入审批流程，不得直接发送。'
            )
            await db.execute(
                pg_insert(HasnTask)
                .values(
                    owner_id=project.owner_hasn_id,
                    agent_id=task_agent_id,
                    name=f'跟进客户：{contact.company_name or customer.customer_no}'[:200],
                    description='项目线索晋级后自动建立的首次接续任务',
                    prompt=task_prompt,
                    schedule_type='once',
                    schedule_config={'run_at': next_followup_at.isoformat()},
                    schedule_display='线索晋级后次日跟进',
                    timezone='Asia/Shanghai',
                    misfire_policy='skip',
                    enabled=True,
                    state='scheduled',
                    next_run_at=next_followup_at,
                    created_by=normalized_actor_id,
                    task_uuid=task_uuid,
                    executor_policy='local_node',
                    task_revision=1,
                    created_by_kind=actor_kind,
                    risk_level='low',
                    project_id=project.platform_project_id,
                    app_id='growth',
                    execution_kind='freeform',
                    execution_spec={'prompt': task_prompt},
                )
                .on_conflict_do_nothing(index_elements=[HasnTask.task_uuid])
            )
            customer.followup_task_id = task_uuid
            if customer.next_followup_at is None:
                customer.next_followup_at = next_followup_at

        await db.execute(
            pg_insert(Activity)
            .values(
                customer_id=customer.id,
                user_id=scope.user_id,
                growth_project_id=project.id,
                growth_project_playbook_id=playbook.id if playbook else None,
                playbook_id=playbook.playbook_id if playbook else None,
                playbook_version=playbook.playbook_version if playbook else None,
                kind='qualify',
                content=f'项目线索晋级为客户（来源 {customer.source_kind}）',
                actor_kind=actor_kind,
                actor_id=normalized_actor_id,
                ref_table='growth_project_lead',
                ref_id=str(project_lead.id),
                occurred_at=now,
                owner_scope=customer.owner_scope,
                enterprise_id=customer.enterprise_id,
                assignee=customer.assignee,
            )
            .on_conflict_do_nothing()
        )
        await db.execute(
            pg_insert(GrowthAttributionEvent)
            .values(
                growth_project_id=project.id,
                event_type='qualified',
                lead_contact_id=project_lead.lead_contact_id,
                customer_id=customer.id,
                growth_project_playbook_id=playbook.id if playbook else None,
                playbook_id=playbook.playbook_id if playbook else None,
                playbook_version=playbook.playbook_version if playbook else None,
                source_kind=project_lead.source_kind,
                source_ref=project_lead.source_ref,
                campaign_ref=project_lead.source_meta.get('campaign'),
                occurred_time=now,
                idempotency_key=f'qualify:{project_lead.id}',
                meta_data={
                    'project_lead_id': project_lead.id,
                    'scoring_version': project_lead.scoring_version,
                    'match_score': (float(project_lead.match_score) if project_lead.match_score is not None else None),
                },
            )
            .on_conflict_do_nothing(
                index_elements=[
                    GrowthAttributionEvent.growth_project_id,
                    GrowthAttributionEvent.idempotency_key,
                ]
            )
        )
        project_lead.status = 'qualified'
        project_lead.dismiss_reason = None
        project_lead.updated_time = now
        await db.flush()

        result = await masked_customer_response(db, customer)
        result['project_lead_id'] = project_lead.id
        result['platform_project_id'] = str(project.platform_project_id)
        return result

    async def assign_lead(
        self,
        db: AsyncSession,
        *,
        growth_project_id: str | UUID,
        project_lead_id: int,
        assignee: str,
        scope: GrowthScope,
    ) -> dict[str, Any]:
        """仅企业 manager 可分配，且负责人必须是同企业 approved 成员。"""
        if not can_manage_assignment(scope):
            raise errors.ForbiddenError(msg='仅企业经理可分配项目线索')
        project = await self._require_project(
            db,
            growth_project_id=growth_project_id,
            scope=scope,
            require_writable=True,
        )
        normalized_assignee = assignee.strip()
        assignee_user_id = (
            await db.execute(
                sa.select(HasnHumans.user_id).where(
                    HasnHumans.hasn_id == normalized_assignee,
                    HasnHumans.status == 'active',
                )
            )
        ).scalar_one_or_none()
        membership = (
            await db.execute(
                sa.select(HasnEnterpriseMembership.id).where(
                    HasnEnterpriseMembership.enterprise_id == scope.enterprise_id,
                    HasnEnterpriseMembership.user_id == assignee_user_id,
                    HasnEnterpriseMembership.status == 'approved',
                    HasnEnterpriseMembership.role.in_((*_OWNER_ROLES, 'member')),
                )
            )
        ).scalar_one_or_none()
        if assignee_user_id is None or membership is None:
            raise errors.RequestError(
                msg='负责人必须是当前企业有效成员',
                data={'error_code': 'LEAD_ASSIGNEE_NOT_ENTERPRISE_MEMBER'},
            )
        project_lead = await self._load_scoped_lead(
            db,
            project=project,
            project_lead_id=project_lead_id,
            scope=scope,
            write_lock=True,
        )
        project_lead.assignee = normalized_assignee
        project_lead.updated_time = timezone.now()
        await db.flush()
        return {
            'id': project_lead.id,
            'growth_project_id': str(project_lead.growth_project_id),
            'lead_contact_id': project_lead.lead_contact_id,
            'status': project_lead.status,
            'assignee': project_lead.assignee,
        }


project_lead_service = ProjectLeadService()
