"""S3 获客存量项目挂靠、触达状态拆分和影子核对。"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import TYPE_CHECKING, Any
from uuid import UUID

import sqlalchemy as sa

from sqlalchemy.dialects.postgresql import insert as pg_insert

from backend.app.hasn.model.hasn_artifacts import HasnArtifacts
from backend.app.hasn.service.app_catalog_service import resolve_owner_hasn_id
from backend.app.hasn_growth.model.activity import Activity
from backend.app.hasn_growth.model.contact_channel import ContactChannel
from backend.app.hasn_growth.model.customer import Customer
from backend.app.hasn_growth.model.form_submission import FormSubmission
from backend.app.hasn_growth.model.growth_pii_migration_quarantine import (
    GrowthPiiMigrationQuarantine,
)
from backend.app.hasn_growth.model.growth_project import GrowthProject
from backend.app.hasn_growth.model.growth_project_lead import GrowthProjectLead
from backend.app.hasn_growth.model.growth_project_migration_quarantine import (
    GrowthProjectMigrationQuarantine,
)
from backend.app.hasn_growth.model.lead_contact import LeadContact
from backend.app.hasn_growth.model.lead_ref import LeadRef
from backend.app.hasn_growth.model.opportunity import Opportunity
from backend.app.hasn_growth.model.outreach_message import OutreachMessage
from backend.app.hasn_growth.model.outreach_message_event import OutreachMessageEvent
from backend.app.hasn_growth.service.growth_project_migration_quarantine_service import (
    growth_project_migration_quarantine_service,
)
from backend.app.hasn_growth.service.pii_keyring import get_growth_pii_keyring
from backend.app.hasn_growth.service.project_lead_compatibility_service import (
    COMPATIBILITY_REMOVAL_DATE,
    COMPATIBILITY_REMOVAL_OWNER,
)
from backend.app.hasn_project.model.hasn_project import HasnProject
from backend.app.hasn_project.service.project_app_service import project_service
from backend.app.hasn_task.model.task import HasnTask
from backend.common.exception import errors
from backend.core.conf import settings
from backend.utils.timezone import timezone

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession

_MIGRATION_VERSION = 'growth-project-s3-v1'
_PERSONAL_PROJECT_NAME = '历史获客数据（待整理）'

_OUTREACH_STATE_MAP: dict[str, tuple[str, str, str]] = {
    'draft': ('draft', 'not_queued', 'drafted'),
    'pending_approval': ('pending_approval', 'not_queued', 'approval_requested'),
    'approved': ('approved', 'not_queued', 'approved'),
    'rejected': ('rejected', 'not_queued', 'rejected'),
    'sending': ('approved', 'sending', 'sending'),
    'sent': ('approved', 'sent', 'sent'),
    'failed': ('approved', 'failed', 'failed'),
    'blocked_optout': ('approved', 'blocked_optout', 'blocked_optout'),
    'blocked_compliance': (
        'approved',
        'blocked_compliance',
        'blocked_compliance',
    ),
    'replied': ('approved', 'sent', 'replied'),
}


@dataclass
class ProjectMigrationResult:
    """单一 Owner 的无敏感迁移摘要。"""

    user_id: int
    owner_hasn_id: str | None
    dry_run: bool
    status: str
    next_cursor: int
    project_created: int = 0
    project_leads_upserted: int = 0
    crm_rows_updated: int = 0
    outreach_rows_mapped: int = 0
    quarantined: int = 0
    pii_audit: dict[str, Any] | None = None

    def comparable_counts(self) -> dict[str, int | str]:
        """返回 dry-run、首次写入与重跑都应一致的目标态计数。"""
        return {
            'status': self.status,
            'project_created': self.project_created,
            'project_leads_upserted': self.project_leads_upserted,
            'crm_rows_updated': self.crm_rows_updated,
            'outreach_rows_mapped': self.outreach_rows_mapped,
            'quarantined': self.quarantined,
        }


@dataclass(frozen=True)
class _Quarantine:
    source_table: str
    source_record_id: str
    reason_code: str
    owner_scope_hint: str | None
    user_id_hint: int | None
    enterprise_id_hint: int | None
    details: dict[str, Any]


class GrowthProjectMigrationService:
    """把 Owner 级旧数据安全投影到默认暂停的项目漏斗。"""

    @staticmethod
    def _request_id(owner_hasn_id: str) -> str:
        return f'growth-migrate:personal:{owner_hasn_id}'

    @staticmethod
    async def _migration_project(
        db: AsyncSession,
        *,
        owner_hasn_id: str,
        user_id: int,
        create: bool,
    ) -> tuple[HasnProject | None, GrowthProject | None]:
        request_id = GrowthProjectMigrationService._request_id(owner_hasn_id)
        platform_project = (
            await db.execute(
                sa.select(HasnProject).where(
                    HasnProject.owner_id == owner_hasn_id,
                    HasnProject.client_request_id == request_id,
                )
            )
        ).scalar_one_or_none()
        if platform_project is None and create:
            created = await project_service.create_project(
                db,
                owner=owner_hasn_id,
                data={
                    'name': _PERSONAL_PROJECT_NAME,
                    'goal': '承接项目化改造前的历史获客数据，待主人整理',
                    'client_request_id': request_id,
                },
            )
            platform_project = await db.get(HasnProject, UUID(str(created['id'])))
            if platform_project is None:
                raise errors.ServerError(msg='幂等创建平台项目后无法读取项目')
        if platform_project is None:
            return None, None
        if create:
            platform_project = (
                await db.execute(
                    sa.select(HasnProject)
                    .where(HasnProject.id == platform_project.id)
                    .with_for_update()
                )
            ).scalar_one()

        growth_project = (
            await db.execute(
                sa.select(GrowthProject).where(
                    GrowthProject.platform_project_id == platform_project.id
                )
            )
        ).scalar_one_or_none()
        if growth_project is None and create:
            growth_project = GrowthProject(
                platform_project_id=platform_project.id,
                user_id=user_id,
                owner_hasn_id=owner_hasn_id,
                owner_scope='personal',
                enterprise_id=None,
                name=_PERSONAL_PROJECT_NAME,
                tagline='项目化改造前的历史数据',
                status='paused',
                provision_status='pending',
            )
            db.add(growth_project)
            await db.flush()
        if growth_project is not None and (
            growth_project.user_id != user_id
            or growth_project.owner_hasn_id != owner_hasn_id
            or growth_project.owner_scope != 'personal'
            or growth_project.enterprise_id is not None
        ):
            raise errors.ConflictError(
                msg='历史获客项目与 Owner 归属不一致',
                data={'error_code': 'GROWTH_MIGRATION_PROJECT_OWNER_CONFLICT'},
            )
        return platform_project, growth_project

    @staticmethod
    async def _owner_rows(
        db: AsyncSession,
        *,
        user_id: int,
    ) -> tuple[
        list[LeadRef],
        list[Customer],
        list[Opportunity],
        list[OutreachMessage],
        list[Activity],
        list[FormSubmission],
    ]:
        refs = list(
            (
                await db.execute(
                    sa.select(LeadRef)
                    .where(LeadRef.user_id == user_id)
                    .order_by(LeadRef.id)
                )
            )
            .scalars()
            .all()
        )
        customers = list(
            (
                await db.execute(
                    sa.select(Customer)
                    .where(Customer.user_id == user_id)
                    .order_by(Customer.id)
                )
            )
            .scalars()
            .all()
        )
        opportunities = list(
            (
                await db.execute(
                    sa.select(Opportunity)
                    .where(Opportunity.user_id == user_id)
                    .order_by(Opportunity.id)
                )
            )
            .scalars()
            .all()
        )
        messages = list(
            (
                await db.execute(
                    sa.select(OutreachMessage)
                    .where(OutreachMessage.user_id == user_id)
                    .order_by(OutreachMessage.id)
                )
            )
            .scalars()
            .all()
        )
        activities = list(
            (
                await db.execute(
                    sa.select(Activity)
                    .where(Activity.user_id == user_id)
                    .order_by(Activity.id)
                )
            )
            .scalars()
            .all()
        )
        forms = list(
            (
                await db.execute(
                    sa.select(FormSubmission)
                    .where(FormSubmission.user_id == user_id)
                    .order_by(FormSubmission.id)
                )
            )
            .scalars()
            .all()
        )
        return refs, customers, opportunities, messages, activities, forms

    @staticmethod
    def _quarantine_for_scope(
        *,
        source_table: str,
        row: Any,
    ) -> _Quarantine | None:
        if (
            getattr(row, 'owner_scope', 'personal') == 'enterprise'
            or getattr(row, 'enterprise_id', None) is not None
        ):
            return _Quarantine(
                source_table=source_table,
                source_record_id=str(row.id),
                reason_code='enterprise_identity_gate_closed',
                owner_scope_hint=getattr(row, 'owner_scope', None),
                user_id_hint=getattr(row, 'user_id', None),
                enterprise_id_hint=getattr(row, 'enterprise_id', None),
                details={
                    'gate': 'GROWTH_PROJECT_V4_ENTERPRISE_ENABLED',
                    'gate_enabled': bool(
                        settings.GROWTH_PROJECT_V4_ENTERPRISE_ENABLED
                    ),
                },
            )
        return None

    @staticmethod
    def _quarantine_for_parent(
        *,
        source_table: str,
        row: Any,
        customers: dict[int, Customer],
        opportunities: dict[int, Opportunity],
    ) -> _Quarantine | None:
        if source_table == 'customer':
            return None
        customer_id = getattr(row, 'customer_id', None)
        if customer_id is not None:
            customer = customers.get(int(customer_id))
            if customer is None:
                return _Quarantine(
                    source_table=source_table,
                    source_record_id=str(row.id),
                    reason_code='crm_parent_missing_or_cross_owner',
                    owner_scope_hint=getattr(row, 'owner_scope', None),
                    user_id_hint=getattr(row, 'user_id', None),
                    enterprise_id_hint=getattr(row, 'enterprise_id', None),
                    details={
                        'parent_table': 'customer',
                        'parent_id': str(customer_id),
                    },
                )
            if (
                customer.owner_scope != 'personal'
                or customer.enterprise_id is not None
            ):
                return _Quarantine(
                    source_table=source_table,
                    source_record_id=str(row.id),
                    reason_code='crm_parent_scope_mismatch',
                    owner_scope_hint=getattr(row, 'owner_scope', None),
                    user_id_hint=getattr(row, 'user_id', None),
                    enterprise_id_hint=getattr(row, 'enterprise_id', None),
                    details={
                        'parent_table': 'customer',
                        'parent_id': str(customer_id),
                    },
                )
        opportunity_id = getattr(row, 'opportunity_id', None)
        if opportunity_id is None:
            return None
        opportunity = opportunities.get(int(opportunity_id))
        if (
            opportunity is None
            or opportunity.customer_id != customer_id
            or opportunity.owner_scope != 'personal'
            or opportunity.enterprise_id is not None
        ):
            return _Quarantine(
                source_table=source_table,
                source_record_id=str(row.id),
                reason_code='crm_parent_missing_or_cross_owner',
                owner_scope_hint=getattr(row, 'owner_scope', None),
                user_id_hint=getattr(row, 'user_id', None),
                enterprise_id_hint=getattr(row, 'enterprise_id', None),
                details={
                    'parent_table': 'opportunity',
                    'parent_id': str(opportunity_id),
                },
            )
        return None

    @staticmethod
    def _state_time(message: OutreachMessage) -> datetime:
        return (
            message.replied_at
            or message.sent_at
            or message.approved_at
            or message.updated_time
            or message.created_time
            or timezone.now()
        )

    async def _write_outreach_state(
        self,
        db: AsyncSession,
        *,
        message: OutreachMessage,
        growth_project: GrowthProject,
        state: tuple[str, str, str],
    ) -> None:
        approval_status, delivery_status, event_type = state
        message.approval_status = approval_status
        message.delivery_status = delivery_status
        message.approval_version = message.approval_version or 1
        message.content_version = message.content_version or 1
        if message.status == 'replied':
            replied_at = message.replied_at or self._state_time(message)
            message.replied_at = replied_at
        await db.execute(
            pg_insert(OutreachMessageEvent)
            .values(
                growth_project_id=growth_project.id,
                outreach_message_id=message.id,
                event_type=event_type,
                idempotency_key=(
                    f'migration:{_MIGRATION_VERSION}:{message.id}:{message.status}'
                ),
                occurred_time=self._state_time(message),
                actor_kind='system',
                actor_id='growth_project_migration',
                approval_status=approval_status,
                delivery_status=delivery_status,
                approval_version=message.approval_version,
                content_version=message.content_version,
                meta_data={
                    'migration_version': _MIGRATION_VERSION,
                    'legacy_status': message.status,
                },
            )
            .on_conflict_do_nothing(
                constraint='uq_growth_outreach_event_idempotency'
            )
        )
        if message.status != 'replied':
            return
        existing_reply = await db.scalar(
            sa.select(Activity.id).where(
                Activity.user_id == message.user_id,
                Activity.kind == 'reply',
                Activity.ref_table == 'outreach_message',
                Activity.ref_id == str(message.id),
            )
        )
        if existing_reply is None:
            db.add(
                Activity(
                    customer_id=message.customer_id,
                    opportunity_id=message.opportunity_id,
                    user_id=message.user_id,
                    growth_project_id=growth_project.id,
                    kind='reply',
                    content='存量触达已记录回复事实',
                    actor_kind='owner',
                    actor_id=None,
                    ref_table='outreach_message',
                    ref_id=str(message.id),
                    occurred_at=replied_at,
                    owner_scope=message.owner_scope,
                    enterprise_id=message.enterprise_id,
                    assignee=message.assignee,
                )
            )

    @staticmethod
    async def _pii_audit_summary(
        db: AsyncSession,
        *,
        user_id: int,
    ) -> dict[str, Any]:
        contact_plaintext = int(
            (
                await db.execute(
                    sa.select(sa.func.count(sa.distinct(LeadContact.id)))
                    .select_from(LeadContact)
                    .join(LeadRef, LeadRef.lead_contact_id == LeadContact.id)
                    .where(
                        LeadRef.user_id == user_id,
                        sa.or_(
                            LeadContact.contact_name.is_not(None),
                            LeadContact.email.is_not(None),
                            LeadContact.email_normalized.is_not(None),
                            LeadContact.phone.is_not(None),
                            LeadContact.phone_normalized.is_not(None),
                            LeadContact.address.is_not(None),
                        ),
                    )
                )
            ).scalar_one()
        )
        customer_plaintext = int(
            (
                await db.execute(
                    sa.select(sa.func.count())
                    .select_from(Customer)
                    .where(
                        Customer.user_id == user_id,
                        sa.or_(
                            Customer.contact_name.is_not(None),
                            Customer.email.is_not(None),
                            Customer.phone.is_not(None),
                            Customer.wechat.is_not(None),
                        ),
                    )
                )
            ).scalar_one()
        )
        form_plaintext = int(
            (
                await db.execute(
                    sa.select(sa.func.count())
                    .select_from(FormSubmission)
                    .where(
                        FormSubmission.user_id == user_id,
                        sa.or_(
                            FormSubmission.name.is_not(None),
                            FormSubmission.email.is_not(None),
                            FormSubmission.phone.is_not(None),
                        ),
                    )
                )
            ).scalar_one()
        )
        versions = list(
            (
                await db.execute(
                    sa.select(ContactChannel.hash_key_version)
                    .where(ContactChannel.user_id == user_id)
                    .distinct()
                    .order_by(ContactChannel.hash_key_version)
                )
            ).scalars()
        )
        active_version = get_growth_pii_keyring().active_hmac_version
        if active_version not in versions:
            versions.append(active_version)
            versions.sort()
        pii_quarantine_pending = int(
            (
                await db.execute(
                    sa.select(sa.func.count())
                    .select_from(GrowthPiiMigrationQuarantine)
                    .where(
                        GrowthPiiMigrationQuarantine.user_id_hint == user_id,
                        GrowthPiiMigrationQuarantine.status == 'pending',
                    )
                )
            ).scalar_one()
        )
        return {
            'plaintext_rows': (
                contact_plaintext + customer_plaintext + form_plaintext
            ),
            'plaintext_by_table': {
                'contact': contact_plaintext,
                'customer': customer_plaintext,
                'form_submission': form_plaintext,
            },
            'hmac_versions': versions,
            'pii_quarantine_pending': pii_quarantine_pending,
        }

    async def migrate_owner(  # ruff: ignore[complex-structure]
        self,
        db: AsyncSession,
        *,
        user_id: int,
        dry_run: bool,
        change_ticket: str,
    ) -> ProjectMigrationResult:
        """规划或迁移一个 Owner；调用方负责批次事务和提交。"""
        if not change_ticket.strip():
            raise errors.RequestError(msg='迁移必须提供变更单号')
        owner_hasn_id = await resolve_owner_hasn_id(db, user_id=user_id)
        result = ProjectMigrationResult(
            user_id=user_id,
            owner_hasn_id=owner_hasn_id,
            dry_run=dry_run,
            status='ready',
            next_cursor=user_id,
        )
        if owner_hasn_id is None:
            result.status = 'quarantined'
            result.quarantined = 1
            if not dry_run:
                await growth_project_migration_quarantine_service.record(
                    db,
                    source_table='owner',
                    source_record_id=str(user_id),
                    reason_code='owner_hasn_id_missing',
                    owner_scope_hint='personal',
                    user_id_hint=user_id,
                    enterprise_id_hint=None,
                    details={'change_ticket': change_ticket},
                )
            return result

        refs, customers, opportunities, messages, activities, forms = (
            await self._owner_rows(db, user_id=user_id)
        )
        _existing_platform, existing_growth = await self._migration_project(
            db,
            owner_hasn_id=owner_hasn_id,
            user_id=user_id,
            create=False,
        )
        expected_existing_id = (
            existing_growth.id if existing_growth is not None else None
        )
        customer_by_id = {row.id: row for row in customers}
        opportunity_by_id = {row.id: row for row in opportunities}
        personal_rows: list[tuple[str, Any]] = []
        quarantines: list[_Quarantine] = []
        for table_name, rows in (
            ('customer', customers),
            ('opportunity', opportunities),
            ('outreach_message', messages),
            ('activity', activities),
            ('form_submission', forms),
        ):
            for row in rows:
                quarantine = self._quarantine_for_parent(
                    source_table=table_name,
                    row=row,
                    customers=customer_by_id,
                    opportunities=opportunity_by_id,
                ) or self._quarantine_for_scope(
                    source_table=table_name, row=row
                )
                if quarantine is not None:
                    quarantines.append(quarantine)
                elif (
                    getattr(row, 'growth_project_id', None) is not None
                    and row.growth_project_id != expected_existing_id
                ):
                    quarantines.append(
                        _Quarantine(
                            source_table=table_name,
                            source_record_id=str(row.id),
                            reason_code='crm_project_conflict',
                            owner_scope_hint=getattr(
                                row, 'owner_scope', None
                            ),
                            user_id_hint=user_id,
                            enterprise_id_hint=getattr(
                                row, 'enterprise_id', None
                            ),
                            details={
                                'existing_growth_project_id': str(
                                    row.growth_project_id
                                ),
                                'migration_project_id': (
                                    str(expected_existing_id)
                                    if expected_existing_id is not None
                                    else None
                                ),
                            },
                        )
                    )
                else:
                    personal_rows.append((table_name, row))

        mapped_messages: list[
            tuple[OutreachMessage, tuple[str, str, str]]
        ] = []
        eligible_message_ids = {
            row.id
            for table_name, row in personal_rows
            if table_name == 'outreach_message'
        }
        for message in messages:
            if message.id not in eligible_message_ids:
                continue
            state = _OUTREACH_STATE_MAP.get((message.status or '').strip())
            if state is None:
                quarantines.append(
                    _Quarantine(
                        source_table='outreach_message',
                        source_record_id=str(message.id),
                        reason_code='unknown_outreach_status',
                        owner_scope_hint=message.owner_scope,
                        user_id_hint=message.user_id,
                        enterprise_id_hint=message.enterprise_id,
                        details={
                            'legacy_status': message.status or '<null>',
                            'change_ticket': change_ticket,
                        },
                    )
                )
            else:
                mapped_messages.append((message, state))

        result.project_created = int(bool(refs or personal_rows))
        result.project_leads_upserted = len(refs)
        reply_facts = sum(
            1
            for message, _state in mapped_messages
            if message.status == 'replied'
            and not any(
                activity.kind == 'reply'
                and activity.ref_table == 'outreach_message'
                and activity.ref_id == str(message.id)
                for activity in activities
            )
        )
        result.crm_rows_updated = len(personal_rows) + reply_facts
        result.outreach_rows_mapped = len(mapped_messages)
        result.quarantined = len(
            {
                (
                    item.source_table,
                    item.source_record_id,
                    item.reason_code,
                )
                for item in quarantines
            }
        )
        result.pii_audit = await self._pii_audit_summary(db, user_id=user_id)
        if result.project_created == 0:
            result.status = 'quarantined'
        if dry_run:
            return result
        if result.project_created == 0:
            for quarantine in quarantines:
                await growth_project_migration_quarantine_service.record(
                    db,
                    source_table=quarantine.source_table,
                    source_record_id=quarantine.source_record_id,
                    reason_code=quarantine.reason_code,
                    owner_scope_hint=quarantine.owner_scope_hint,
                    user_id_hint=quarantine.user_id_hint,
                    enterprise_id_hint=quarantine.enterprise_id_hint,
                    details=quarantine.details,
                )
            await db.flush()
            return result

        _platform_project, growth_project = await self._migration_project(
            db,
            owner_hasn_id=owner_hasn_id,
            user_id=user_id,
            create=True,
        )
        if growth_project is None:
            raise errors.ServerError(msg='创建历史获客漏斗失败')
        now = timezone.now()
        for ref in refs:
            await db.execute(
                pg_insert(GrowthProjectLead)
                .values(
                    growth_project_id=growth_project.id,
                    lead_contact_id=ref.lead_contact_id,
                    user_id=user_id,
                    owner_scope='personal',
                    enterprise_id=None,
                    source_kind=ref.source,
                    source_ref=f'lead_ref:{ref.id}',
                    source_meta={'migration_version': _MIGRATION_VERSION},
                    status=ref.status,
                    dismiss_reason=ref.dismiss_reason,
                    note=ref.note,
                    acquired_at=ref.acquired_at,
                )
                .on_conflict_do_update(
                    constraint='uq_growth_project_lead_contact',
                    set_={
                        'user_id': user_id,
                        'owner_scope': 'personal',
                        'enterprise_id': None,
                        'source_kind': ref.source,
                        'source_ref': f'lead_ref:{ref.id}',
                        'source_meta': {
                            'migration_version': _MIGRATION_VERSION
                        },
                        'status': ref.status,
                        'dismiss_reason': ref.dismiss_reason,
                        'note': ref.note,
                        'acquired_at': ref.acquired_at,
                        'updated_time': now,
                    },
                )
            )
        for _table_name, row in personal_rows:
            row.growth_project_id = growth_project.id
        for message, state in mapped_messages:
            await self._write_outreach_state(
                db,
                message=message,
                growth_project=growth_project,
                state=state,
            )
        for quarantine in quarantines:
            await growth_project_migration_quarantine_service.record(
                db,
                source_table=quarantine.source_table,
                source_record_id=quarantine.source_record_id,
                reason_code=quarantine.reason_code,
                owner_scope_hint=quarantine.owner_scope_hint,
                user_id_hint=quarantine.user_id_hint,
                enterprise_id_hint=quarantine.enterprise_id_hint,
                details=quarantine.details,
            )
        await db.flush()
        return result

    async def build_shadow_report(
        self,
        db: AsyncSession,
        *,
        user_id: int,
        sample_size: int = 20,
    ) -> dict[str, Any]:
        """生成只含计数和稳定键的监控 JSON，不输出联系人内容。"""
        owner_hasn_id = await resolve_owner_hasn_id(db, user_id=user_id)
        if owner_hasn_id is None:
            raise errors.NotFoundError(msg='Owner HASN 身份不存在')
        platform_project, growth_project = await self._migration_project(
            db,
            owner_hasn_id=owner_hasn_id,
            user_id=user_id,
            create=False,
        )
        if platform_project is None or growth_project is None:
            raise errors.NotFoundError(msg='历史获客项目尚未迁移')

        lead_rows = (
            await db.execute(
                sa.select(
                    LeadRef.lead_contact_id,
                    LeadRef.status.label('legacy_status'),
                    LeadRef.note.label('legacy_note'),
                    GrowthProjectLead.status.label('project_status'),
                    GrowthProjectLead.note.label('project_note'),
                )
                .select_from(LeadRef)
                .outerjoin(
                    GrowthProjectLead,
                    sa.and_(
                        GrowthProjectLead.growth_project_id
                        == growth_project.id,
                        GrowthProjectLead.lead_contact_id
                        == LeadRef.lead_contact_id,
                    ),
                )
                .where(LeadRef.user_id == user_id)
                .order_by(LeadRef.lead_contact_id)
            )
        ).all()
        missing_in_project = [
            int(row.lead_contact_id)
            for row in lead_rows
            if row.project_status is None
        ]
        status_mismatch = [
            int(row.lead_contact_id)
            for row in lead_rows
            if row.project_status is not None
            and row.project_status != row.legacy_status
        ]
        content_mismatch = [
            int(row.lead_contact_id)
            for row in lead_rows
            if row.project_status is not None
            and row.project_note != row.legacy_note
        ]
        extra_in_project = list(
            (
                await db.execute(
                    sa.select(GrowthProjectLead.lead_contact_id)
                    .outerjoin(
                        LeadRef,
                        sa.and_(
                            LeadRef.user_id == user_id,
                            LeadRef.lead_contact_id
                            == GrowthProjectLead.lead_contact_id,
                        ),
                    )
                    .where(
                        GrowthProjectLead.growth_project_id == growth_project.id,
                        LeadRef.id.is_(None),
                    )
                    .order_by(GrowthProjectLead.lead_contact_id)
                )
            ).scalars()
        )
        cross_owner = int(
            (
                await db.execute(
                    sa.select(sa.func.count())
                    .select_from(GrowthProject)
                    .join(
                        HasnProject,
                        HasnProject.id == GrowthProject.platform_project_id,
                    )
                    .where(
                        GrowthProject.id == growth_project.id,
                        GrowthProject.owner_hasn_id != HasnProject.owner_id,
                    )
                )
            ).scalar_one()
        )
        wrong_enterprise = int(
            (
                await db.execute(
                    sa.select(sa.func.count())
                    .select_from(GrowthProjectLead)
                    .where(
                        GrowthProjectLead.growth_project_id == growth_project.id,
                        sa.or_(
                            GrowthProjectLead.owner_scope != 'personal',
                            GrowthProjectLead.enterprise_id.is_not(None),
                            GrowthProjectLead.user_id != user_id,
                        ),
                    )
                )
            ).scalar_one()
        )
        orphan_crm = 0
        for model in (
            Customer,
            Opportunity,
            OutreachMessage,
            Activity,
            FormSubmission,
        ):
            orphan_crm += int(
                (
                    await db.execute(
                        sa.select(sa.func.count())
                        .select_from(model)
                        .where(
                            model.user_id == user_id,
                            getattr(model, 'owner_scope', sa.literal('personal'))
                            == 'personal',
                            sa.or_(
                                model.growth_project_id.is_(None),
                                model.growth_project_id != growth_project.id,
                            ),
                        )
                    )
                ).scalar_one()
            )
        orphan_tasks = int(
            (
                await db.execute(
                    sa.select(sa.func.count())
                    .select_from(HasnTask)
                    .where(
                        HasnTask.owner_id == owner_hasn_id,
                        HasnTask.app_id == 'growth',
                        sa.or_(
                            HasnTask.project_id.is_(None),
                            HasnTask.project_id != platform_project.id,
                        ),
                    )
                )
            ).scalar_one()
        )
        orphan_artifacts = int(
            (
                await db.execute(
                    sa.select(sa.func.count())
                    .select_from(HasnArtifacts)
                    .where(
                        HasnArtifacts.owner_hasn_id == owner_hasn_id,
                        sa.or_(
                            HasnArtifacts.resource_app_id == 'growth',
                            HasnArtifacts.source_app_id == 'growth',
                        ),
                        sa.or_(
                            HasnArtifacts.project_id.is_(None),
                            HasnArtifacts.project_id != platform_project.id,
                        ),
                    )
                )
            ).scalar_one()
        )
        unknown_statuses = int(
            (
                await db.execute(
                    sa.select(sa.func.count())
                    .select_from(GrowthProjectMigrationQuarantine)
                    .where(
                        GrowthProjectMigrationQuarantine.user_id_hint == user_id,
                        GrowthProjectMigrationQuarantine.reason_code
                        == 'unknown_outreach_status',
                        GrowthProjectMigrationQuarantine.status == 'pending',
                    )
                )
            ).scalar_one()
        )
        pii = await self._pii_audit_summary(db, user_id=user_id)
        differences = {
            'missing_in_project': len(missing_in_project),
            'extra_in_project': len(extra_in_project),
            'status_mismatch': len(status_mismatch),
            'content_mismatch': len(content_mismatch),
            'unknown_outreach_status': unknown_statuses,
            'plaintext_rows': int(pii['plaintext_rows']),
            'orphan_crm': orphan_crm,
            'orphan_tasks': orphan_tasks,
            'orphan_artifacts': orphan_artifacts,
            'cross_owner': cross_owner,
            'wrong_enterprise': wrong_enterprise,
        }
        differences['total'] = sum(differences.values())
        sample_ids = (
            missing_in_project
            + [int(value) for value in extra_in_project]
            + status_mismatch
            + content_mismatch
        )[: max(0, min(sample_size, 100))]
        return {
            'schema_version': 'growth-project-shadow-v1',
            'generated_at': timezone.now().isoformat(),
            'status': 'pass' if differences['total'] == 0 else 'fail',
            'owner_hasn_id': owner_hasn_id,
            'platform_project_id': str(platform_project.id),
            'growth_project_id': str(growth_project.id),
            'counts': {
                'legacy_leads': len(lead_rows),
                'project_leads': len(lead_rows)
                - len(missing_in_project)
                + len(extra_in_project),
            },
            'differences': differences,
            'boundaries': {
                'cross_owner': cross_owner,
                'wrong_enterprise': wrong_enterprise,
            },
            'pii': pii,
            'associations': {
                'orphan_crm': orphan_crm,
                'orphan_tasks': orphan_tasks,
                'orphan_artifacts': orphan_artifacts,
            },
            'sample': {
                'lead_contact_ids': sample_ids,
                'truncated': len(sample_ids) >= sample_size,
            },
            'compatibility': {
                'removal_owner': COMPATIBILITY_REMOVAL_OWNER,
                'remove_after': COMPATIBILITY_REMOVAL_DATE,
                'cutover_enabled': bool(
                    settings.GROWTH_PROJECT_READ_CUTOVER_ENABLED
                ),
            },
        }


growth_project_migration_service = GrowthProjectMigrationService()
