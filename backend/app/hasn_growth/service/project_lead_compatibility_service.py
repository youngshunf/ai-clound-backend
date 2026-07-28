"""S3 线索引用双写、新表优先读取与受审计旧读回落。"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING
from uuid import UUID

import sqlalchemy as sa

from sqlalchemy.dialects.postgresql import insert as pg_insert

from backend.app.hasn_growth.model.growth_project import GrowthProject
from backend.app.hasn_growth.model.growth_project_lead import GrowthProjectLead
from backend.app.hasn_growth.model.lead_audit_log import LeadAuditLog
from backend.app.hasn_growth.model.lead_ref import LeadRef
from backend.app.hasn_project.model.hasn_project import HasnProject
from backend.common.exception import errors
from backend.core.conf import settings
from backend.database.result import affected_rows
from backend.utils.timezone import timezone

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession

COMPATIBILITY_REMOVAL_OWNER = 'Growth 后端值班'
COMPATIBILITY_REMOVAL_DATE = '2026-10-31'


@dataclass(frozen=True)
class CompatibleLeadReference:
    """统一暴露旧引用和项目引用的最小稳定字段。"""

    source_table: str
    status: str
    source: str | None
    dismiss_reason: str | None
    note: str | None


class ProjectLeadCompatibilityService:
    """迁移窗口内维持旧引用可回滚，同时让项目引用成为首选事实。"""

    @staticmethod
    async def _owned_project(
        db: AsyncSession,
        *,
        user_id: int,
        growth_project_id: str | UUID | None,
    ) -> GrowthProject:
        statement = sa.select(GrowthProject).where(
            GrowthProject.user_id == user_id,
            GrowthProject.owner_scope == 'personal',
            GrowthProject.enterprise_id.is_(None),
        )
        if growth_project_id is not None:
            statement = statement.where(GrowthProject.id == growth_project_id)
        else:
            statement = statement.join(
                HasnProject,
                HasnProject.id == GrowthProject.platform_project_id,
            ).where(
                HasnProject.client_request_id
                == sa.func.concat(
                    'growth-migrate:personal:',
                    GrowthProject.owner_hasn_id,
                )
            )
        row = (await db.execute(statement)).scalar_one_or_none()
        if row is None:
            raise errors.NotFoundError(msg='获客项目不存在或无权访问')
        return row

    async def require_owned_project(
        self,
        db: AsyncSession,
        *,
        user_id: int,
        growth_project_id: str | UUID,
    ) -> UUID:
        """在执行列表查询前先验证项目归属，空结果也不能掩盖越权访问。"""
        project = await self._owned_project(
            db,
            user_id=user_id,
            growth_project_id=growth_project_id,
        )
        return project.id

    async def upsert_reference(
        self,
        db: AsyncSession,
        *,
        user_id: int,
        lead_contact_id: int,
        source: str,
        status: str,
        dismiss_reason: str | None = None,
        note: str | None = None,
        growth_project_id: str | UUID | None = None,
        update_existing: bool = True,
        update_source: bool = True,
    ) -> bool:
        """始终维护可回滚旧引用；双写开关开启后原子维护项目引用。"""
        now = timezone.now()
        insert_statement = pg_insert(LeadRef).values(
            user_id=user_id,
            lead_contact_id=lead_contact_id,
            source=source,
            status=status,
            dismiss_reason=dismiss_reason,
            note=note,
        )
        if update_existing:
            update_values = {
                'status': status,
                'dismiss_reason': dismiss_reason,
                'note': note,
                'updated_time': now,
            }
            if update_source:
                update_values['source'] = source
            insert_statement = insert_statement.on_conflict_do_update(
                constraint='uq_growth_lead_ref_user_lead',
                set_=update_values,
            )
        else:
            insert_statement = insert_statement.on_conflict_do_nothing(
                constraint='uq_growth_lead_ref_user_lead'
            )
        write_result = await db.execute(insert_statement)
        changed = affected_rows(write_result) > 0
        legacy_ref = (
            await db.execute(
                sa.select(LeadRef)
                .where(
                    LeadRef.user_id == user_id,
                    LeadRef.lead_contact_id == lead_contact_id,
                )
                .execution_options(populate_existing=True)
            )
        ).scalar_one()
        if not settings.GROWTH_PROJECT_DUAL_WRITE_ENABLED:
            return changed
        project = await self._owned_project(
            db,
            user_id=user_id,
            growth_project_id=growth_project_id,
        )
        await db.execute(
            pg_insert(GrowthProjectLead)
            .values(
                growth_project_id=project.id,
                lead_contact_id=lead_contact_id,
                user_id=user_id,
                owner_scope='personal',
                enterprise_id=None,
                source_kind=legacy_ref.source,
                status=legacy_ref.status,
                dismiss_reason=legacy_ref.dismiss_reason,
                note=legacy_ref.note,
                acquired_at=legacy_ref.acquired_at,
            )
            .on_conflict_do_update(
                constraint='uq_growth_project_lead_contact',
                set_={
                    'user_id': user_id,
                    'owner_scope': 'personal',
                    'enterprise_id': None,
                    'source_kind': legacy_ref.source,
                    'status': legacy_ref.status,
                    'dismiss_reason': legacy_ref.dismiss_reason,
                    'note': legacy_ref.note,
                    'acquired_at': legacy_ref.acquired_at,
                    'updated_time': now,
                },
            )
        )
        await db.flush()
        return changed

    async def get_reference(
        self,
        db: AsyncSession,
        *,
        user_id: int,
        lead_contact_id: int,
        growth_project_id: str | UUID,
    ) -> CompatibleLeadReference | None:
        """优先读项目引用；切只读新表前允许一次有记录的旧表回落。"""
        await self._owned_project(
            db,
            user_id=user_id,
            growth_project_id=growth_project_id,
        )
        project_ref = (
            await db.execute(
                sa.select(GrowthProjectLead).where(
                    GrowthProjectLead.growth_project_id == growth_project_id,
                    GrowthProjectLead.lead_contact_id == lead_contact_id,
                    GrowthProjectLead.user_id == user_id,
                )
            )
        ).scalar_one_or_none()
        if project_ref is not None:
            return CompatibleLeadReference(
                source_table='growth_project_lead',
                status=project_ref.status,
                source=project_ref.source_kind,
                dismiss_reason=project_ref.dismiss_reason,
                note=project_ref.note,
            )
        if settings.GROWTH_PROJECT_READ_CUTOVER_ENABLED:
            return None

        legacy_ref = (
            await db.execute(
                sa.select(LeadRef).where(
                    LeadRef.user_id == user_id,
                    LeadRef.lead_contact_id == lead_contact_id,
                )
            )
        ).scalar_one_or_none()
        if legacy_ref is None:
            return None
        db.add(
            LeadAuditLog(
                event_type='project_read_fallback',
                actor_user_id=user_id,
                actor_role='system',
                target_table='lead_ref',
                target_count=1,
                target_ref=str(lead_contact_id),
                payload={
                    'growth_project_id': str(growth_project_id),
                    'reason_code': 'project_lead_missing',
                },
                result='success',
            )
        )
        await db.flush()
        return CompatibleLeadReference(
            source_table='lead_ref',
            status=legacy_ref.status,
            source=legacy_ref.source,
            dismiss_reason=legacy_ref.dismiss_reason,
            note=legacy_ref.note,
        )


project_lead_compatibility_service = ProjectLeadCompatibilityService()
