"""项目客户查询服务。

客户及其活动、任务、商机、触达和归因必须以 ``growth_project_id`` 为共同边界。
普通读取只返回脱敏资料；渠道明文仍只能走 Owner 单渠道 reveal。
"""

from __future__ import annotations

from decimal import Decimal
from typing import Any
from uuid import UUID

import sqlalchemy as sa

from sqlalchemy.ext.asyncio import AsyncSession

from backend.app.hasn_growth.model.activity import Activity
from backend.app.hasn_growth.model.customer import Customer
from backend.app.hasn_growth.model.growth_attribution_event import (
    GrowthAttributionEvent,
)
from backend.app.hasn_growth.model.opportunity import Opportunity
from backend.app.hasn_growth.model.outreach_message import OutreachMessage
from backend.app.hasn_growth.service.funnel_service import masked_customer_response
from backend.app.hasn_growth.service.pii import redact_pii_value
from backend.app.hasn_growth.service.project_lead_service import project_lead_service
from backend.app.hasn_growth.service.scope_context import GrowthScope, apply_scope
from backend.app.hasn_task.model.task import HasnTask
from backend.common.exception import errors

_LIFECYCLE_STATUSES = {
    'active',
    'engaged',
    'opportunity',
    'silent',
    'won',
    'lost',
    'archived',
}


def _number(value: Decimal | None) -> float | None:
    return float(value) if value is not None else None


class ProjectCustomerService:
    """按获客项目读取客户及全部接续事实。"""

    @staticmethod
    def _validate_page(*, page: int, size: int, lifecycle_status: str | None) -> None:
        if page < 1 or not 1 <= size <= 100:
            raise errors.RequestError(msg='分页参数无效')
        if lifecycle_status is not None and lifecycle_status not in _LIFECYCLE_STATUSES:
            raise errors.RequestError(msg='客户状态筛选无效')

    async def _load_customer(
        self,
        db: AsyncSession,
        *,
        project_id: UUID,
        customer_id: int,
        scope: GrowthScope,
    ) -> Customer:
        statement = sa.select(Customer).where(
            Customer.id == customer_id,
            Customer.growth_project_id == project_id,
        )
        statement = apply_scope(
            statement,
            Customer,
            user_id=scope.user_id,
            scope=scope,
        )
        customer = (await db.execute(statement)).scalar_one_or_none()
        if customer is None:
            raise errors.NotFoundError(msg='客户不存在或无权访问')
        return customer

    async def list_customers(
        self,
        db: AsyncSession,
        *,
        growth_project_id: str | UUID,
        scope: GrowthScope,
        page: int,
        size: int,
        lifecycle_status: str | None = None,
        assignee: str | None = None,
    ) -> dict[str, Any]:
        """服务端分页返回当前项目客户，绝不跨项目拼接旧客户池。"""
        self._validate_page(
            page=page,
            size=size,
            lifecycle_status=lifecycle_status,
        )
        project = await project_lead_service.require_project(
            db,
            growth_project_id=growth_project_id,
            scope=scope,
        )
        statement = sa.select(Customer).where(Customer.growth_project_id == project.id)
        statement = apply_scope(
            statement,
            Customer,
            user_id=scope.user_id,
            scope=scope,
        )
        if lifecycle_status is not None:
            statement = statement.where(Customer.lifecycle_status == lifecycle_status)
        if assignee:
            if scope.is_enterprise and not scope.is_manager:
                raise errors.ForbiddenError(msg='仅企业经理可按负责人筛选')
            statement = statement.where(Customer.assignee == assignee)
        total = int(
            (
                await db.execute(
                    sa.select(sa.func.count()).select_from(
                        statement.order_by(None).subquery()
                    )
                )
            ).scalar_one()
        )
        customers = (
            (
                await db.execute(
                    statement
                    .order_by(Customer.intent_score.desc(), Customer.id.desc())
                    .offset((page - 1) * size)
                    .limit(size)
                )
            )
            .scalars()
            .all()
        )
        return {
            'items': [
                await masked_customer_response(db, customer)
                for customer in customers
            ],
            'total': total,
            'page': page,
            'size': size,
            'scope': scope.to_meta(),
        }

    async def get_customer(
        self,
        db: AsyncSession,
        *,
        growth_project_id: str | UUID,
        customer_id: int,
        scope: GrowthScope,
    ) -> dict[str, Any]:
        """读取当前项目的单个脱敏客户。"""
        project = await project_lead_service.require_project(
            db,
            growth_project_id=growth_project_id,
            scope=scope,
        )
        customer = await self._load_customer(
            db,
            project_id=project.id,
            customer_id=customer_id,
            scope=scope,
        )
        return await masked_customer_response(db, customer)

    async def get_customer_detail(
        self,
        db: AsyncSession,
        *,
        growth_project_id: str | UUID,
        customer_id: int,
        scope: GrowthScope,
    ) -> dict[str, Any]:
        """聚合客户画像与全部接续事实，每张表都显式校验项目键。"""
        project = await project_lead_service.require_project(
            db,
            growth_project_id=growth_project_id,
            scope=scope,
        )
        customer = await self._load_customer(
            db,
            project_id=project.id,
            customer_id=customer_id,
            scope=scope,
        )
        activities = (
            (
                await db.execute(
                    sa
                    .select(Activity)
                    .where(
                        Activity.growth_project_id == project.id,
                        Activity.customer_id == customer.id,
                    )
                    .order_by(Activity.occurred_at.desc(), Activity.id.desc())
                    .limit(100)
                )
            )
            .scalars()
            .all()
        )
        opportunities = (
            (
                await db.execute(
                    sa
                    .select(Opportunity)
                    .where(
                        Opportunity.growth_project_id == project.id,
                        Opportunity.customer_id == customer.id,
                    )
                    .order_by(Opportunity.id.desc())
                    .limit(100)
                )
            )
            .scalars()
            .all()
        )
        outreach = (
            (
                await db.execute(
                    sa
                    .select(OutreachMessage)
                    .where(
                        OutreachMessage.growth_project_id == project.id,
                        OutreachMessage.customer_id == customer.id,
                    )
                    .order_by(OutreachMessage.id.desc())
                    .limit(100)
                )
            )
            .scalars()
            .all()
        )
        attribution = (
            (
                await db.execute(
                    sa
                    .select(GrowthAttributionEvent)
                    .where(
                        GrowthAttributionEvent.growth_project_id == project.id,
                        GrowthAttributionEvent.customer_id == customer.id,
                    )
                    .order_by(
                        GrowthAttributionEvent.occurred_time.desc(),
                        GrowthAttributionEvent.id.desc(),
                    )
                    .limit(100)
                )
            )
            .scalars()
            .all()
        )
        tasks: list[HasnTask] = []
        if customer.followup_task_id:
            tasks = list(
                (
                    await db.execute(
                        sa.select(HasnTask).where(
                            HasnTask.task_uuid == customer.followup_task_id,
                            HasnTask.project_id == project.platform_project_id,
                            HasnTask.app_id == 'growth',
                            HasnTask.deleted_at.is_(None),
                        )
                    )
                )
                .scalars()
                .all()
            )
        return {
            'growth_project_id': str(project.id),
            'customer': await masked_customer_response(db, customer),
            'activities': [
                {
                    'id': row.id,
                    'kind': row.kind,
                    'content': redact_pii_value(row.content),
                    'actor_kind': row.actor_kind,
                    'actor_id': row.actor_id,
                    'opportunity_id': row.opportunity_id,
                    'occurred_at': row.occurred_at,
                }
                for row in activities
            ],
            'followup_tasks': [
                {
                    'task_uuid': row.task_uuid,
                    'name': row.name,
                    'state': row.state,
                    'next_run_at': row.next_run_at,
                    'last_status': row.last_status,
                    'agent_id': row.agent_id,
                }
                for row in tasks
            ],
            'opportunities': [
                {
                    'id': row.id,
                    'name': row.name,
                    'stage': row.stage,
                    'amount': _number(row.amount),
                    'currency': row.currency,
                    'probability': _number(row.probability),
                    'expected_close_at': row.expected_close_at,
                }
                for row in opportunities
            ],
            'outreach': [
                {
                    'id': row.id,
                    'direction': row.direction,
                    'channel': row.channel,
                    'subject': redact_pii_value(row.subject),
                    'content': redact_pii_value(row.content),
                    'approval_status': row.approval_status,
                    'delivery_status': row.delivery_status,
                    'status': row.status,
                    'sent_at': row.sent_at,
                    'replied_at': row.replied_at,
                }
                for row in outreach
            ],
            'attribution': [
                {
                    'id': row.id,
                    'event_type': row.event_type,
                    'source_kind': row.source_kind,
                    'source_ref': row.source_ref,
                    'campaign_ref': row.campaign_ref,
                    'playbook_ref': row.playbook_ref,
                    'amount': _number(row.amount),
                    'currency': row.currency,
                    'occurred_time': row.occurred_time,
                }
                for row in attribution
            ],
        }


project_customer_service = ProjectCustomerService()
