"""获客商机与成交服务（设计 07 §9）。

立商机（一个客户可多商机）、阶段推进（每次写 stage_change activity，允许倒退）、
成交登记 deal.close（won/lost + 金额 + close_note，复盘留档）。成交是事实登记不是合同动作，
统一授权开关在工具层默认 ask（M4），本服务只做业务落库 + 客户态联动。
"""

from __future__ import annotations

from typing import Any
from uuid import uuid4

import sqlalchemy as sa

from sqlalchemy.ext.asyncio import AsyncSession

from backend.app.hasn_growth.model.opportunity import Opportunity
from backend.app.hasn_growth.service.funnel_service import GrowthFunnelService
from backend.app.hasn_growth.service.scope_context import GrowthScope, apply_scope
from backend.common.exception import errors
from backend.utils.timezone import timezone

_OPEN_STAGES = ('contacted', 'replied', 'proposal', 'negotiation')
_CLOSED_STAGES = ('closed_won', 'closed_lost')
_VALID_STAGES = _OPEN_STAGES + _CLOSED_STAGES


def _gen_no(prefix: str) -> str:
    return f'{prefix}{uuid4().hex[:12].upper()}'


def _opportunity_to_dict(o: Opportunity) -> dict[str, Any]:
    return {
        'id': o.id,
        'opportunity_no': o.opportunity_no,
        'customer_id': o.customer_id,
        'name': o.name,
        'stage': o.stage,
        'amount': float(o.amount) if o.amount is not None else None,
        'currency': o.currency,
        'probability': float(o.probability) if o.probability is not None else None,
        'expected_close_at': o.expected_close_at,
        'won_at': o.won_at,
        'lost_at': o.lost_at,
        'lost_reason': o.lost_reason,
        'close_note': o.close_note,
        'created_by_kind': o.created_by_kind,
        'owner_scope': o.owner_scope,
        'enterprise_id': o.enterprise_id,
        'assignee': o.assignee,
        'created_time': o.created_time,
    }


class GrowthOpportunityService:
    """商机/成交，全 user_id 隔离，跨户 → NotFound。"""

    @staticmethod
    async def _load(
        db: AsyncSession, *, user_id: int, opportunity_id: int, scope: GrowthScope | None = None
    ) -> Opportunity:
        stmt = apply_scope(
            sa.select(Opportunity).where(Opportunity.id == opportunity_id), Opportunity, user_id=user_id, scope=scope
        )
        o = (await db.execute(stmt)).scalar_one_or_none()
        if not o:
            raise errors.NotFoundError(msg='商机不存在或无权访问')
        return o

    @staticmethod
    async def create_opportunity(
        db: AsyncSession,
        *,
        user_id: int,
        customer_id: int,
        name: str,
        amount: float | None = None,
        currency: str = 'CNY',
        stage: str = 'contacted',
        probability: float | None = None,
        expected_close_at: Any = None,
        created_by_kind: str = 'agent',
        actor_id: str | None = None,
        scope: GrowthScope | None = None,
    ) -> dict[str, Any]:
        """立商机：建 opportunity + 客户态置 opportunity + 时间线留痕。商机继承客户归属（owner_scope/enterprise_id/assignee）。"""
        if stage not in _VALID_STAGES:
            raise errors.RequestError(msg=f'非法商机阶段：{stage}')
        customer = await GrowthFunnelService._load_customer(db, user_id=user_id, customer_id=customer_id, scope=scope)
        opp = Opportunity(
            opportunity_no=_gen_no('OPP'),
            customer_id=customer_id,
            user_id=user_id,
            name=name,
            stage=stage,
            amount=amount,
            currency=currency,
            probability=probability,
            expected_close_at=expected_close_at,
            created_by_kind=created_by_kind,
            owner_scope=customer.owner_scope,
            enterprise_id=customer.enterprise_id,
            assignee=customer.assignee,
        )
        db.add(opp)
        # 客户进入「已立商机」生命周期（不回退已成交/流失）。
        if customer.lifecycle_status not in ('won', 'lost', 'archived'):
            customer.lifecycle_status = 'opportunity'
        await db.flush()
        await GrowthFunnelService._add_activity(
            db,
            user_id=user_id,
            customer_id=customer_id,
            kind='stage_change',
            content=f'立商机「{name}」（阶段 {stage}）',
            opportunity_id=opp.id,
            actor_kind=created_by_kind,
            actor_id=actor_id,
            ref_table='opportunity',
            ref_id=str(opp.id),
        )
        return _opportunity_to_dict(opp)

    @classmethod
    async def update_stage(
        cls,
        db: AsyncSession,
        *,
        user_id: int,
        opportunity_id: int,
        stage: str,
        note: str | None = None,
        actor_kind: str = 'agent',
        actor_id: str | None = None,
        scope: GrowthScope | None = None,
    ) -> dict[str, Any]:
        """推进/倒退商机阶段（允许倒退；每次写 stage_change 谁/何时/为何）。成交收口走 close_deal。"""
        if stage not in _OPEN_STAGES:
            raise errors.RequestError(msg=f'阶段流转仅限 {_OPEN_STAGES}；成交/流失请用 close_deal')
        o = await cls._load(db, user_id=user_id, opportunity_id=opportunity_id, scope=scope)
        if o.stage in _CLOSED_STAGES:
            raise errors.ForbiddenError(msg=f'商机已收口（{o.stage}），不可再改阶段')
        old = o.stage
        o.stage = stage
        await db.flush()
        await GrowthFunnelService._add_activity(
            db,
            user_id=user_id,
            customer_id=o.customer_id,
            kind='stage_change',
            content=f'阶段 {old} → {stage}' + (f'：{note}' if note else ''),
            opportunity_id=o.id,
            actor_kind=actor_kind,
            actor_id=actor_id,
            ref_table='opportunity',
            ref_id=str(o.id),
        )
        return _opportunity_to_dict(o)

    @classmethod
    async def close_deal(
        cls,
        db: AsyncSession,
        *,
        user_id: int,
        opportunity_id: int,
        result: str,
        amount: float | None = None,
        close_note: str | None = None,
        lost_reason: str | None = None,
        actor_kind: str = 'agent',
        actor_id: str | None = None,
        scope: GrowthScope | None = None,
    ) -> dict[str, Any]:
        """成交/流失登记（result='won'|'lost'）。won 入 funnel 金额统计；lost 留败因复盘。"""
        if result not in ('won', 'lost'):
            raise errors.RequestError(msg="result 只能是 'won' 或 'lost'")
        o = await cls._load(db, user_id=user_id, opportunity_id=opportunity_id, scope=scope)
        if o.stage in _CLOSED_STAGES:
            raise errors.ForbiddenError(msg=f'商机已收口（{o.stage}），不可重复登记')
        now = timezone.now()
        if result == 'won':
            o.stage = 'closed_won'
            o.won_at = now
            if amount is not None:
                o.amount = amount
            o.close_note = close_note
        else:
            o.stage = 'closed_lost'
            o.lost_at = now
            o.lost_reason = lost_reason or close_note
            o.close_note = close_note
        await db.flush()
        # 客户态联动。
        customer = await GrowthFunnelService._load_customer(db, user_id=user_id, customer_id=o.customer_id, scope=scope)
        customer.lifecycle_status = 'won' if result == 'won' else 'lost'
        await db.flush()
        await GrowthFunnelService._add_activity(
            db,
            user_id=user_id,
            customer_id=o.customer_id,
            kind='close',
            content=(
                f'成交「{o.name}」金额 {o.amount or 0} {o.currency}' + (f'：{close_note}' if close_note else '')
                if result == 'won'
                else f'流失「{o.name}」：{lost_reason or close_note or "未注明"}'
            ),
            opportunity_id=o.id,
            actor_kind=actor_kind,
            actor_id=actor_id,
            ref_table='opportunity',
            ref_id=str(o.id),
        )
        return _opportunity_to_dict(o)

    @classmethod
    async def get_opportunity(
        cls, db: AsyncSession, *, user_id: int, opportunity_id: int, scope: GrowthScope | None = None
    ) -> dict[str, Any]:
        o = await cls._load(db, user_id=user_id, opportunity_id=opportunity_id, scope=scope)
        return _opportunity_to_dict(o)

    @staticmethod
    async def list_opportunities(
        db: AsyncSession,
        *,
        user_id: int,
        customer_id: int | None = None,
        stage: str | None = None,
        open_only: bool = False,
        assignee: str | None = None,
        limit: int = 50,
        scope: GrowthScope | None = None,
    ) -> list[dict[str, Any]]:
        stmt = apply_scope(sa.select(Opportunity), Opportunity, user_id=user_id, scope=scope)
        if assignee and scope is not None and scope.is_enterprise:
            stmt = stmt.where(Opportunity.assignee == assignee)
        if customer_id is not None:
            stmt = stmt.where(Opportunity.customer_id == customer_id)
        if stage is not None:
            stmt = stmt.where(Opportunity.stage == stage)
        if open_only:
            stmt = stmt.where(Opportunity.stage.in_(_OPEN_STAGES))
        stmt = stmt.order_by(Opportunity.id.desc()).limit(min(limit, 200))
        rows = (await db.execute(stmt)).scalars().all()
        return [_opportunity_to_dict(o) for o in rows]


growth_opportunity_service = GrowthOpportunityService()
