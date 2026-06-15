"""获客漏斗报表服务（设计 07 §11 总览卡）。

漏斗总览：线索池 / 跟进中 / 商机(数+金额) / 本月成交(数+金额) + 待审触达；
商机阶段分布、客户生命周期分布。全 user_id 隔离。
"""

from __future__ import annotations

from typing import Any

import sqlalchemy as sa

from sqlalchemy.ext.asyncio import AsyncSession

from backend.app.hasn_growth.model.customer import Customer
from backend.app.hasn_growth.model.lead_contact import LeadContact
from backend.app.hasn_growth.model.opportunity import Opportunity
from backend.app.hasn_growth.model.outreach_message import OutreachMessage
from backend.app.hasn_growth.service.scope_context import GrowthScope, apply_scope
from backend.utils.timezone import timezone

_OPEN_STAGES = ('contacted', 'replied', 'proposal', 'negotiation')
# 跟进中：未成交/流失/归档的客户。
_FOLLOWING_LIFECYCLE = ('active', 'engaged', 'silent', 'opportunity')


class GrowthReportService:
    """漏斗统计，全 user_id 隔离。"""

    @staticmethod
    async def funnel_overview(db: AsyncSession, *, user_id: int, scope: GrowthScope | None = None) -> dict[str, Any]:
        # 线索池：本户线索中未拒绝未晋级的（仍待跟进）。线索池本里程碑未企业化，恒按 user_id（owner 池）。
        lead_pool = (
            await db.execute(
                sa.select(sa.func.count())
                .select_from(LeadContact)
                .where(
                    LeadContact.user_id == user_id,
                    LeadContact.status.notin_(['rejected', 'qualified']),
                )
            )
        ).scalar_one()

        following = (
            await db.execute(
                apply_scope(sa.select(sa.func.count()).select_from(Customer), Customer, user_id=user_id, scope=scope).where(
                    Customer.lifecycle_status.in_(_FOLLOWING_LIFECYCLE)
                )
            )
        ).scalar_one()

        opp_count, opp_amount = (
            await db.execute(
                apply_scope(
                    sa.select(sa.func.count(), sa.func.coalesce(sa.func.sum(Opportunity.amount), 0)),
                    Opportunity, user_id=user_id, scope=scope,
                ).where(Opportunity.stage.in_(_OPEN_STAGES))
            )
        ).one()

        month_start = timezone.now().replace(day=1, hour=0, minute=0, second=0, microsecond=0)
        won_count, won_amount = (
            await db.execute(
                apply_scope(
                    sa.select(sa.func.count(), sa.func.coalesce(sa.func.sum(Opportunity.amount), 0)),
                    Opportunity, user_id=user_id, scope=scope,
                ).where(Opportunity.stage == 'closed_won', Opportunity.won_at >= month_start)
            )
        ).one()

        pending_approvals = (
            await db.execute(
                apply_scope(
                    sa.select(sa.func.count()).select_from(OutreachMessage), OutreachMessage, user_id=user_id, scope=scope
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
                apply_scope(sa.select(Opportunity.stage, sa.func.count()), Opportunity, user_id=user_id, scope=scope)
                .group_by(Opportunity.stage)
            )
        ).all()
        return {stage: int(count) for stage, count in rows}

    @staticmethod
    async def lifecycle_distribution(db: AsyncSession, *, user_id: int, scope: GrowthScope | None = None) -> dict[str, int]:
        rows = (
            await db.execute(
                apply_scope(sa.select(Customer.lifecycle_status, sa.func.count()), Customer, user_id=user_id, scope=scope)
                .group_by(Customer.lifecycle_status)
            )
        ).all()
        return {status: int(count) for status, count in rows}


growth_report_service = GrowthReportService()
