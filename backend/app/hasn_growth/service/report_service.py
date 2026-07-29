"""获客漏斗报表服务（设计 07 §11 总览卡）。

漏斗总览：线索池 / 跟进中 / 商机(数+金额) / 本月成交(数+金额) + 待审触达；
商机阶段分布、客户生命周期分布。全 user_id 隔离。
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any
from uuid import UUID

import sqlalchemy as sa

from sqlalchemy.ext.asyncio import AsyncSession

from backend.app.hasn_growth.model.activity import Activity
from backend.app.hasn_growth.model.customer import Customer
from backend.app.hasn_growth.model.growth_attribution_event import (
    GrowthAttributionEvent,
)
from backend.app.hasn_growth.model.growth_project import GrowthProject
from backend.app.hasn_growth.model.growth_project_lead import GrowthProjectLead
from backend.app.hasn_growth.model.lead_ref import LeadRef
from backend.app.hasn_growth.model.opportunity import Opportunity
from backend.app.hasn_growth.model.outreach_message import OutreachMessage
from backend.app.hasn_growth.service.scope_context import GrowthScope, apply_scope
from backend.common.exception import errors
from backend.utils.timezone import timezone

_OPEN_STAGES = ('contacted', 'replied', 'proposal', 'negotiation')
# 跟进中：未成交/流失/归档的客户。
_FOLLOWING_LIFECYCLE = ('active', 'engaged', 'silent', 'opportunity')


class GrowthReportService:
    """漏斗统计，全 user_id 隔离。"""

    @staticmethod
    async def funnel_overview(db: AsyncSession, *, user_id: int, scope: GrowthScope | None = None) -> dict[str, Any]:
        # 线索池：本户引用中仍待跟进的线索（统一池——归属/状态落 lead_ref，status='new' 即未晋级未忽略）。
        # 恒按 user_id（owner 引用），本里程碑未企业化。
        lead_pool = (
            await db.execute(
                sa
                .select(sa.func.count())
                .select_from(LeadRef)
                .where(
                    LeadRef.user_id == user_id,
                    LeadRef.status == 'new',
                )
            )
        ).scalar_one()

        following = (
            await db.execute(
                apply_scope(
                    sa.select(sa.func.count()).select_from(Customer),
                    Customer,
                    user_id=user_id,
                    scope=scope,
                ).where(Customer.lifecycle_status.in_(_FOLLOWING_LIFECYCLE))
            )
        ).scalar_one()

        opp_count, opp_amount = (
            await db.execute(
                apply_scope(
                    sa.select(sa.func.count(), sa.func.coalesce(sa.func.sum(Opportunity.amount), 0)),
                    Opportunity,
                    user_id=user_id,
                    scope=scope,
                ).where(Opportunity.stage.in_(_OPEN_STAGES))
            )
        ).one()

        month_start = timezone.now().replace(day=1, hour=0, minute=0, second=0, microsecond=0)
        won_count, won_amount = (
            await db.execute(
                apply_scope(
                    sa.select(sa.func.count(), sa.func.coalesce(sa.func.sum(Opportunity.amount), 0)),
                    Opportunity,
                    user_id=user_id,
                    scope=scope,
                ).where(Opportunity.stage == 'closed_won', Opportunity.won_at >= month_start)
            )
        ).one()

        pending_approvals = (
            await db.execute(
                apply_scope(
                    sa.select(sa.func.count()).select_from(OutreachMessage),
                    OutreachMessage,
                    user_id=user_id,
                    scope=scope,
                ).where(OutreachMessage.status == 'pending_approval')
            )
        ).scalar_one()

        return {
            'lead_pool': int(lead_pool),
            'following': int(following),
            'opportunities': {'count': int(opp_count), 'amount': float(opp_amount or 0)},
            'won_this_month': {'count': int(won_count), 'amount': float(won_amount or 0)},
            'pending_approvals': int(pending_approvals),
        }

    @staticmethod
    async def stage_distribution(db: AsyncSession, *, user_id: int, scope: GrowthScope | None = None) -> dict[str, int]:
        rows = (
            await db.execute(
                apply_scope(
                    sa.select(Opportunity.stage, sa.func.count()), Opportunity, user_id=user_id, scope=scope
                ).group_by(Opportunity.stage)
            )
        ).all()
        return {stage: int(count) for stage, count in rows}

    @staticmethod
    async def lifecycle_distribution(
        db: AsyncSession,
        *,
        user_id: int,
        scope: GrowthScope | None = None,
    ) -> dict[str, int]:
        rows = (
            await db.execute(
                apply_scope(
                    sa.select(Customer.lifecycle_status, sa.func.count()),
                    Customer,
                    user_id=user_id,
                    scope=scope,
                ).group_by(Customer.lifecycle_status)
            )
        ).all()
        return {status: int(count) for status, count in rows}

    @staticmethod
    async def project_overview(
        db: AsyncSession,
        *,
        owner_hasn_id: str,
        growth_project_id: str | UUID,
    ) -> dict[str, Any]:
        """按 Growth UUID 汇总本周期经营事实，成本未记录时保持 null。"""
        try:
            project_id = growth_project_id if isinstance(growth_project_id, UUID) else UUID(str(growth_project_id))
        except (TypeError, ValueError) as exc:
            raise errors.NotFoundError(msg='获客项目不存在') from exc
        growth = (
            await db.execute(
                sa.select(GrowthProject).where(
                    GrowthProject.id == project_id,
                    GrowthProject.owner_hasn_id == owner_hasn_id,
                )
            )
        ).scalar_one_or_none()
        if growth is None:
            raise errors.NotFoundError(msg='获客项目不存在')

        lead_total = await db.scalar(
            sa
            .select(sa.func.count())
            .select_from(GrowthProjectLead)
            .where(GrowthProjectLead.growth_project_id == growth.id)
        )
        lead_pool = await db.scalar(
            sa
            .select(sa.func.count())
            .select_from(GrowthProjectLead)
            .where(
                GrowthProjectLead.growth_project_id == growth.id,
                GrowthProjectLead.status == 'new',
            )
        )
        customer_total = await db.scalar(
            sa.select(sa.func.count()).select_from(Customer).where(Customer.growth_project_id == growth.id)
        )
        following = await db.scalar(
            sa
            .select(sa.func.count())
            .select_from(Customer)
            .where(
                Customer.growth_project_id == growth.id,
                Customer.lifecycle_status.in_(_FOLLOWING_LIFECYCLE),
            )
        )
        opportunity_total = await db.scalar(
            sa.select(sa.func.count()).select_from(Opportunity).where(Opportunity.growth_project_id == growth.id)
        )
        open_count, open_amount = (
            await db.execute(
                sa.select(
                    sa.func.count(),
                    sa.func.coalesce(sa.func.sum(Opportunity.amount), 0),
                ).where(
                    Opportunity.growth_project_id == growth.id,
                    Opportunity.stage.in_(_OPEN_STAGES),
                )
            )
        ).one()
        month_start = datetime.now(UTC).replace(
            day=1,
            hour=0,
            minute=0,
            second=0,
            microsecond=0,
        )
        won_count, won_amount = (
            await db.execute(
                sa.select(
                    sa.func.count(),
                    sa.func.coalesce(sa.func.sum(Opportunity.amount), 0),
                ).where(
                    Opportunity.growth_project_id == growth.id,
                    Opportunity.stage == 'closed_won',
                    Opportunity.won_at >= month_start,
                )
            )
        ).one()
        pending_approvals = await db.scalar(
            sa
            .select(sa.func.count())
            .select_from(OutreachMessage)
            .where(
                OutreachMessage.growth_project_id == growth.id,
                OutreachMessage.status == 'pending_approval',
            )
        )
        cost_count, cost_amount = (
            await db.execute(
                sa.select(
                    sa.func.count(),
                    sa.func.coalesce(
                        sa.func.sum(GrowthAttributionEvent.amount),
                        0,
                    ),
                ).where(
                    GrowthAttributionEvent.growth_project_id == growth.id,
                    GrowthAttributionEvent.event_type == 'cost',
                    GrowthAttributionEvent.occurred_time >= month_start,
                )
            )
        ).one()
        recent_rows = (
            (
                await db.execute(
                    sa
                    .select(Activity)
                    .where(Activity.growth_project_id == growth.id)
                    .order_by(Activity.occurred_at.desc(), Activity.id.desc())
                    .limit(5)
                )
            )
            .scalars()
            .all()
        )

        def _conversion(numerator: int, denominator: int) -> dict[str, Any]:
            return {
                'numerator': numerator,
                'denominator': denominator,
                'rate': (round(numerator / denominator, 4) if denominator > 0 else None),
            }

        return {
            'growth_project_id': str(growth.id),
            'cycle': {
                'kind': 'month',
                'started_time': month_start.isoformat(),
            },
            'funnel': {
                'lead_pool': int(lead_pool or 0),
                'following': int(following or 0),
                'opportunities': {
                    'count': int(open_count),
                    'amount': float(open_amount or 0),
                },
                'won': {
                    'count': int(won_count),
                    'amount': float(won_amount or 0),
                },
            },
            'pending_approvals': int(pending_approvals or 0),
            'conversion': {
                'lead_to_customer': _conversion(
                    int(customer_total or 0),
                    int(lead_total or 0),
                ),
                'customer_to_opportunity': _conversion(
                    int(opportunity_total or 0),
                    int(customer_total or 0),
                ),
                'opportunity_to_won': _conversion(
                    int(won_count),
                    int(opportunity_total or 0),
                ),
            },
            'revenue': {
                'amount': float(won_amount or 0),
                'currency': growth.budget_currency,
            },
            'cost': {
                'recorded': int(cost_count) > 0,
                'amount': (float(cost_amount or 0) if int(cost_count) > 0 else None),
                'currency': growth.budget_currency,
            },
            'recent_activity': [
                {
                    'id': row.id,
                    'kind': row.kind,
                    'actor_kind': row.actor_kind,
                    'actor_id': row.actor_id,
                    'occurred_at': row.occurred_at.isoformat(),
                }
                for row in recent_rows
            ],
        }


growth_report_service = GrowthReportService()
