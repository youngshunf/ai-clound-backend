"""Growth 落地页状态、Publish 对账与去标识留资摘要。"""

from __future__ import annotations

from typing import Any
from uuid import UUID

import sqlalchemy as sa

from sqlalchemy.ext.asyncio import AsyncSession

from backend.app.hasn_growth.model.form_submission import FormSubmission
from backend.app.hasn_growth.model.growth_attribution_event import GrowthAttributionEvent
from backend.app.hasn_growth.model.growth_project import GrowthProject
from backend.app.hasn_publish.provider.client import publish_provider
from backend.app.hasn_task.model.task import HasnTask
from backend.common.exception import errors
from backend.core.conf import settings
from backend.utils.timezone import timezone


class GrowthLandingService:
    """Growth 只经 Publish provider 读取站点，不直接查询 Publish 表。"""

    @staticmethod
    async def _require_project(
        db: AsyncSession,
        *,
        owner_hasn_id: str,
        growth_project_id: str | UUID,
        writable: bool = False,
    ) -> GrowthProject:
        project = (
            await db.execute(
                sa.select(GrowthProject).where(
                    GrowthProject.id == growth_project_id,
                    GrowthProject.owner_hasn_id == owner_hasn_id,
                    GrowthProject.owner_scope == 'personal',
                    GrowthProject.enterprise_id.is_(None),
                )
            )
        ).scalar_one_or_none()
        if project is None:
            raise errors.NotFoundError(msg='获客项目不存在或无权访问')
        if writable and project.status != 'active':
            raise errors.ConflictError(msg='获客项目当前不可修改落地页绑定')
        return project

    @staticmethod
    async def _recent_submissions(
        db: AsyncSession,
        *,
        project: GrowthProject,
        limit: int = 20,
    ) -> list[dict[str, Any]]:
        task_state = (
            sa.select(HasnTask.state)
            .where(HasnTask.task_uuid == FormSubmission.task_id)
            .correlate(FormSubmission)
            .scalar_subquery()
        )
        rows = (
            await db.execute(
                sa.select(FormSubmission, task_state.label('task_state'))
                .where(FormSubmission.growth_project_id == project.id)
                .order_by(FormSubmission.id.desc())
                .limit(limit)
            )
        ).all()
        return [
            {
                'id': submission.id,
                'status': submission.status,
                'spam_status': submission.spam_status,
                'spam_reason': submission.spam_reason,
                'privacy_notice_version': submission.privacy_notice_version,
                'consent_purpose': submission.consent_purpose,
                'consent_at': timezone.to_str(submission.consent_at) if submission.consent_at else None,
                'project_lead_id': submission.project_lead_id,
                'customer_id': submission.customer_id,
                'task_id': submission.task_id,
                'task_state': task_status,
                'task_health': (
                    'missing'
                    if submission.status == 'converted' and not submission.task_id
                    else 'failed'
                    if task_status == 'error'
                    else 'ready'
                    if submission.task_id
                    else 'not_applicable'
                ),
                'created_time': timezone.to_str(submission.created_time) if submission.created_time else None,
            }
            for submission, task_status in rows
        ]

    @staticmethod
    async def _attribution_summary(
        db: AsyncSession,
        *,
        project: GrowthProject,
    ) -> dict[str, Any]:
        """汇总公开表单的首触与末触事实，不让客户端从留资列表猜归因。"""
        touch_model = GrowthAttributionEvent.meta_data['touch_model'].astext
        first_touch = (
            sa.func.count(GrowthAttributionEvent.id)
            .filter(touch_model == 'first_touch')
            .label('first_touch_count')
        )
        last_touch = (
            sa.func.count(GrowthAttributionEvent.id)
            .filter(touch_model == 'last_touch')
            .label('last_touch_count')
        )
        first_touch_count, last_touch_count, latest_touch_at = (
            await db.execute(
                sa.select(
                    first_touch,
                    last_touch,
                    sa.func.max(GrowthAttributionEvent.occurred_time).label('latest_touch_at'),
                ).where(
                    GrowthAttributionEvent.growth_project_id == project.id,
                    GrowthAttributionEvent.event_type == 'inbound',
                    GrowthAttributionEvent.source_kind == 'inbound_form',
                )
            )
        ).one()
        return {
            'first_touch_count': int(first_touch_count or 0),
            'last_touch_count': int(last_touch_count or 0),
            'latest_touch_at': timezone.to_str(latest_touch_at) if latest_touch_at else None,
        }

    @staticmethod
    def _site_state(site: dict[str, Any] | None) -> str:
        if site is None:
            return 'unpublished'
        if site.get('status') != 'active':
            return 'revoked'
        if site.get('current_revision_id') is None:
            return 'failed'
        return 'published'

    async def status(
        self,
        db: AsyncSession,
        *,
        owner_hasn_id: str,
        growth_project_id: str | UUID,
    ) -> dict[str, Any]:
        project = await self._require_project(
            db,
            owner_hasn_id=owner_hasn_id,
            growth_project_id=growth_project_id,
        )
        dependency: dict[str, Any] = {
            'feature_enabled': settings.GROWTH_PUBLISH_LANDING_ENABLED,
            'status': 'ready',
            'error_code': None,
            'message': None,
        }
        site: dict[str, Any] | None = None
        if not settings.GROWTH_PUBLISH_LANDING_ENABLED:
            dependency.update(
                status='disabled',
                error_code='GROWTH_PUBLISH_LANDING_DISABLED',
                message='落地页能力尚未开放',
            )
        else:
            try:
                site = await publish_provider.get_growth_site_status(
                    owner_hasn_id=project.owner_hasn_id,
                    platform_project_id=str(project.platform_project_id),
                    growth_project_id=str(project.id),
                )
            except errors.RequestError as exc:
                if exc.code != 503:
                    raise
                dependency.update(
                    status='unavailable',
                    error_code=(exc.data or {}).get('error_code') if isinstance(exc.data, dict) else None,
                    message=exc.msg,
                )
        resource_uri = site.get('resource_uri') if site else None
        return {
            'growth_project_id': str(project.id),
            'platform_project_id': str(project.platform_project_id),
            'dependency': dependency,
            'site_state': self._site_state(site),
            'site': site,
            'binding': {
                'resource_uri': project.landing_site_ref,
                'in_sync': bool(resource_uri and project.landing_site_ref == resource_uri),
            },
            'attribution_summary': await self._attribution_summary(db, project=project),
            'recent_submissions': await self._recent_submissions(db, project=project),
        }

    async def reconcile(
        self,
        db: AsyncSession,
        *,
        owner_hasn_id: str,
        growth_project_id: str | UUID,
    ) -> dict[str, Any]:
        if not settings.GROWTH_PUBLISH_LANDING_ENABLED:
            raise errors.ConflictError(
                msg='落地页能力尚未开放',
                data={'error_code': 'GROWTH_PUBLISH_LANDING_DISABLED'},
            )
        project = await self._require_project(
            db,
            owner_hasn_id=owner_hasn_id,
            growth_project_id=growth_project_id,
            writable=True,
        )
        site = await publish_provider.get_growth_site_status(
            owner_hasn_id=project.owner_hasn_id,
            platform_project_id=str(project.platform_project_id),
            growth_project_id=str(project.id),
        )
        if site is None:
            raise errors.NotFoundError(
                msg='尚未发现该项目的 Publish 站点',
                data={'error_code': 'GROWTH_LANDING_SITE_NOT_FOUND'},
            )
        if self._site_state(site) != 'published':
            raise errors.ConflictError(
                msg='Publish 站点尚未形成可用版本',
                data={'error_code': 'GROWTH_LANDING_SITE_NOT_READY'},
            )
        project.landing_site_ref = str(site['resource_uri'])
        await db.flush()
        return await self.status(
            db,
            owner_hasn_id=owner_hasn_id,
            growth_project_id=project.id,
        )


growth_landing_service = GrowthLandingService()
