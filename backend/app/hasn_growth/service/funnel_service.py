"""获客漏斗服务（设计 07 §5/§6/§10）：线索→客户→活动时间线的业务编排。

在 codegen CRUD 之上做 owner 隔离的漏斗推进：qualify（线索晋级客户+快照+回写线索态）、
dismiss（线索不合格）、客户列表/详情/时间线、画像更新、活动记录、线索检索。
一切查询按 user_id（owner）隔离，跨户 → NotFound。读类出参经 PII 脱敏（§10.2，API 传 reveal_pii）。

商机/成交在 opportunity_flow_service；触达状态机在 outreach_service；本服务不碰对外发送。
"""

from __future__ import annotations

from typing import Any
from uuid import uuid4

import sqlalchemy as sa

from sqlalchemy.ext.asyncio import AsyncSession

from backend.app.hasn_growth.model.activity import Activity
from backend.app.hasn_growth.model.customer import Customer
from backend.app.hasn_growth.model.lead_contact import LeadContact
from backend.app.hasn_growth.service.pii import mask_contact_fields
from backend.common.exception import errors
from backend.utils.timezone import timezone

# 采集来源 → 客户来源映射（contact.source_type 多样，归一到 customer.source_kind 字典）。
_SOURCE_KIND_MAP = {
    'firecrawl': 'outbound_crawl',
    'crawl': 'outbound_crawl',
    'web': 'outbound_crawl',
    'manual': 'manual',
    'inbound_form': 'inbound_form',
    'community': 'community',
}


def _gen_no(prefix: str) -> str:
    return f'{prefix}{uuid4().hex[:12].upper()}'


def _customer_to_dict(c: Customer, *, reveal_pii: bool) -> dict[str, Any]:
    data = {
        'id': c.id,
        'customer_no': c.customer_no,
        'lead_contact_id': c.lead_contact_id,
        'source_kind': c.source_kind,
        'company_name': c.company_name,
        'contact_name': c.contact_name,
        'email': c.email,
        'phone': c.phone,
        'wechat': c.wechat,
        'im_refs': c.im_refs,
        'profile_json': c.profile_json,
        'intent_score': float(c.intent_score) if c.intent_score is not None else 0.0,
        'lifecycle_status': c.lifecycle_status,
        'owner_agent_id': c.owner_agent_id,
        'followup_task_id': c.followup_task_id,
        'tags': c.tags,
        'last_activity_at': c.last_activity_at,
        'next_followup_at': c.next_followup_at,
        'silent_round_count': c.silent_round_count,
        'created_time': c.created_time,
    }
    return mask_contact_fields(data, reveal=reveal_pii)


def _lead_to_dict(c: LeadContact, *, reveal_pii: bool) -> dict[str, Any]:
    data = {
        'lead_contact_id': c.id,
        'lead_no': c.lead_no,
        'company_name': c.company_name,
        'contact_name': c.contact_name,
        'email': c.email,
        'phone': c.phone,
        'website': c.website,
        'domain': c.domain,
        'industry': c.industry,
        'country': c.country,
        'city': c.city,
        'source_type': c.source_type,
        'keyword': c.keyword,
        'status': c.status,
        'confidence_score': float(c.confidence_score) if c.confidence_score is not None else 0.0,
    }
    return mask_contact_fields(data, reveal=reveal_pii)


class GrowthFunnelService:
    """漏斗推进领域服务（owner 隔离 + PII 脱敏）。"""

    # ----------------------------- 线索池 -----------------------------

    @staticmethod
    async def search_leads(
        db: AsyncSession,
        *,
        user_id: int,
        query: str | None = None,
        min_score: float | None = None,
        limit: int = 20,
        reveal_pii: bool = False,
    ) -> list[dict[str, Any]]:
        """检索线索池（owner 自己的线索 + 全局未归属池），关键词/评分过滤，默认脱敏。"""
        stmt = sa.select(LeadContact).where(
            sa.or_(LeadContact.user_id == user_id, LeadContact.user_id.is_(None)),
            LeadContact.status != 'rejected',
        )
        if query:
            like = f'%{query}%'
            stmt = stmt.where(
                sa.or_(
                    LeadContact.company_name.ilike(like),
                    LeadContact.industry.ilike(like),
                    LeadContact.keyword.ilike(like),
                    LeadContact.contact_name.ilike(like),
                )
            )
        if min_score is not None:
            stmt = stmt.where(LeadContact.confidence_score >= min_score)
        stmt = stmt.order_by(LeadContact.confidence_score.desc().nullslast()).limit(min(limit, 100))
        rows = (await db.execute(stmt)).scalars().all()
        return [_lead_to_dict(r, reveal_pii=reveal_pii) for r in rows]

    @staticmethod
    async def get_lead(
        db: AsyncSession, *, user_id: int, lead_contact_id: int, reveal_pii: bool = False
    ) -> dict[str, Any]:
        lead = await GrowthFunnelService._load_lead(db, user_id=user_id, lead_contact_id=lead_contact_id)
        return _lead_to_dict(lead, reveal_pii=reveal_pii)

    @staticmethod
    async def _load_lead(db: AsyncSession, *, user_id: int, lead_contact_id: int) -> LeadContact:
        lead = (
            await db.execute(
                sa.select(LeadContact).where(
                    LeadContact.id == lead_contact_id,
                    sa.or_(LeadContact.user_id == user_id, LeadContact.user_id.is_(None)),
                )
            )
        ).scalar_one_or_none()
        if not lead:
            raise errors.NotFoundError(msg='线索不存在或无权访问')
        return lead

    @staticmethod
    async def create_manual_lead(
        db: AsyncSession,
        *,
        user_id: int,
        company_name: str | None = None,
        contact_name: str | None = None,
        email: str | None = None,
        phone: str | None = None,
        website: str | None = None,
        industry: str | None = None,
        city: str | None = None,
        note: str | None = None,
        confidence_score: float | None = None,
    ) -> dict[str, Any]:
        """主人在 UI 手动新建线索（owner 私有池，source_type=manual）。

        AI-native 宗旨：UI 给人操作。主人手动建线索与分身 `collect` 采集互补；至少公司名或联系人名
        之一，否则线索无意义。明文 PII 落库（owner 自己的数据），出参对自己 reveal。
        """

        def _clean(v: str | None) -> str | None:
            v = v.strip() if v else None
            return v or None

        company_name = _clean(company_name)
        contact_name = _clean(contact_name)
        if not company_name and not contact_name:
            raise errors.RequestError(msg='请至少填写公司名或联系人名')

        lead = LeadContact(
            lead_no=_gen_no('LEAD'),
            lead_scope='user',
            user_id=user_id,
            company_name=company_name,
            contact_name=contact_name,
            email=_clean(email),
            phone=_clean(phone),
            website=_clean(website),
            industry=_clean(industry),
            city=_clean(city),
            source_type='manual',
            status='active',
            confidence_score=confidence_score if confidence_score is not None else 60,
            meta_data={'note': note.strip()} if note and note.strip() else {},
        )
        db.add(lead)
        await db.flush()
        return _lead_to_dict(lead, reveal_pii=True)

    # ----------------------------- 晋级 / 淘汰 -----------------------------

    @staticmethod
    async def qualify_lead(
        db: AsyncSession,
        *,
        user_id: int,
        lead_contact_id: int,
        profile: dict | None = None,
        intent_score: float | None = None,
        owner_agent_id: str | None = None,
    ) -> dict[str, Any]:
        """线索晋级为客户（建 customer + 画像快照 + 回写线索态 + qualify 活动）。幂等：已晋级返回既有客户。"""
        lead = await GrowthFunnelService._load_lead(db, user_id=user_id, lead_contact_id=lead_contact_id)

        existing = (
            await db.execute(
                sa.select(Customer).where(
                    Customer.user_id == user_id, Customer.lead_contact_id == lead_contact_id
                )
            )
        ).scalar_one_or_none()
        if existing:
            return _customer_to_dict(existing, reveal_pii=False)

        score = intent_score if intent_score is not None else (float(lead.confidence_score or 0))
        customer = Customer(
            customer_no=_gen_no('CUS'),
            user_id=user_id,
            lead_contact_id=lead_contact_id,
            source_kind=_SOURCE_KIND_MAP.get(lead.source_type or 'manual', 'outbound_crawl'),
            company_name=lead.company_name,
            contact_name=lead.contact_name,
            email=lead.email,
            phone=lead.phone,
            im_refs={},
            profile_json=profile or {},
            intent_score=score,
            lifecycle_status='active',
            owner_agent_id=owner_agent_id,
            tags=[],
            silent_round_count=0,
            last_activity_at=timezone.now(),
        )
        db.add(customer)
        lead.status = 'qualified'
        await db.flush()
        await GrowthFunnelService._add_activity(
            db,
            user_id=user_id,
            customer_id=customer.id,
            kind='qualify',
            content=f'线索晋级为客户（来源 {customer.source_kind}）',
            actor_kind='agent' if owner_agent_id else 'owner',
            actor_id=owner_agent_id,
        )
        return _customer_to_dict(customer, reveal_pii=False)

    @staticmethod
    async def dismiss_lead(
        db: AsyncSession, *, user_id: int, lead_contact_id: int, reason: str
    ) -> dict[str, Any]:
        """标记线索不合格（status=rejected + reason 入 meta，不再推荐）。"""
        lead = await GrowthFunnelService._load_lead(db, user_id=user_id, lead_contact_id=lead_contact_id)
        lead.status = 'rejected'
        meta = dict(lead.meta_data or {})
        meta['dismiss_reason'] = reason
        meta['dismissed_at'] = timezone.now().isoformat()
        lead.meta_data = meta
        await db.flush()
        return {'lead_contact_id': lead.id, 'status': 'rejected', 'reason': reason}

    # ----------------------------- 客户 -----------------------------

    @staticmethod
    async def list_customers(
        db: AsyncSession,
        *,
        user_id: int,
        lifecycle_status: str | None = None,
        limit: int = 20,
        reveal_pii: bool = False,
    ) -> list[dict[str, Any]]:
        stmt = sa.select(Customer).where(Customer.user_id == user_id)
        if lifecycle_status:
            stmt = stmt.where(Customer.lifecycle_status == lifecycle_status)
        stmt = stmt.order_by(Customer.intent_score.desc(), Customer.id.desc()).limit(min(limit, 100))
        rows = (await db.execute(stmt)).scalars().all()
        return [_customer_to_dict(r, reveal_pii=reveal_pii) for r in rows]

    @staticmethod
    async def _load_customer(db: AsyncSession, *, user_id: int, customer_id: int) -> Customer:
        customer = (
            await db.execute(
                sa.select(Customer).where(Customer.id == customer_id, Customer.user_id == user_id)
            )
        ).scalar_one_or_none()
        if not customer:
            raise errors.NotFoundError(msg='客户不存在或无权访问')
        return customer

    @staticmethod
    async def get_customer(
        db: AsyncSession, *, user_id: int, customer_id: int, reveal_pii: bool = False
    ) -> dict[str, Any]:
        customer = await GrowthFunnelService._load_customer(db, user_id=user_id, customer_id=customer_id)
        return _customer_to_dict(customer, reveal_pii=reveal_pii)

    @staticmethod
    async def customer_timeline(
        db: AsyncSession, *, user_id: int, customer_id: int, limit: int = 30
    ) -> list[dict[str, Any]]:
        await GrowthFunnelService._load_customer(db, user_id=user_id, customer_id=customer_id)
        rows = (
            await db.execute(
                sa.select(Activity)
                .where(Activity.user_id == user_id, Activity.customer_id == customer_id)
                .order_by(Activity.occurred_at.desc(), Activity.id.desc())
                .limit(min(limit, 100))
            )
        ).scalars().all()
        return [
            {
                'id': a.id,
                'kind': a.kind,
                'content': a.content,
                'actor_kind': a.actor_kind,
                'actor_id': a.actor_id,
                'opportunity_id': a.opportunity_id,
                'occurred_at': a.occurred_at,
            }
            for a in rows
        ]

    @staticmethod
    async def update_customer_profile(
        db: AsyncSession,
        *,
        user_id: int,
        customer_id: int,
        profile: dict | None = None,
        intent_score: float | None = None,
        tags: list | None = None,
        lifecycle_status: str | None = None,
        followup_task_id: str | None = None,
    ) -> dict[str, Any]:
        customer = await GrowthFunnelService._load_customer(db, user_id=user_id, customer_id=customer_id)
        if profile is not None:
            customer.profile_json = profile
        if intent_score is not None:
            customer.intent_score = intent_score
        if tags is not None:
            customer.tags = tags
        if lifecycle_status is not None:
            customer.lifecycle_status = lifecycle_status
            # 止损标 silent：累计静默轮次（设计 §7 止损）
            if lifecycle_status == 'silent':
                customer.silent_round_count = (customer.silent_round_count or 0) + 1
        if followup_task_id is not None:
            # 绑定/换绑当前跟进任务（J3 即时跟进据此触发 run_now）；空串解绑。
            customer.followup_task_id = followup_task_id or None
        customer.last_activity_at = timezone.now()
        await db.flush()
        return _customer_to_dict(customer, reveal_pii=False)

    @staticmethod
    async def log_activity(
        db: AsyncSession,
        *,
        user_id: int,
        customer_id: int,
        kind: str,
        content: str,
        opportunity_id: int | None = None,
        actor_kind: str = 'agent',
        actor_id: str | None = None,
    ) -> dict[str, Any]:
        await GrowthFunnelService._load_customer(db, user_id=user_id, customer_id=customer_id)
        activity = await GrowthFunnelService._add_activity(
            db,
            user_id=user_id,
            customer_id=customer_id,
            kind=kind,
            content=content,
            opportunity_id=opportunity_id,
            actor_kind=actor_kind,
            actor_id=actor_id,
        )
        return {'id': activity.id, 'kind': activity.kind, 'occurred_at': activity.occurred_at}

    @staticmethod
    async def _add_activity(
        db: AsyncSession,
        *,
        user_id: int,
        customer_id: int,
        kind: str,
        content: str | None,
        opportunity_id: int | None = None,
        actor_kind: str = 'agent',
        actor_id: str | None = None,
        ref_table: str | None = None,
        ref_id: str | None = None,
    ) -> Activity:
        activity = Activity(
            customer_id=customer_id,
            opportunity_id=opportunity_id,
            user_id=user_id,
            kind=kind,
            content=content,
            actor_kind=actor_kind,
            actor_id=actor_id,
            ref_table=ref_table,
            ref_id=ref_id,
            occurred_at=timezone.now(),
        )
        db.add(activity)
        # 同步客户最近活动游标
        await db.execute(
            sa.update(Customer)
            .where(Customer.id == customer_id, Customer.user_id == user_id)
            .values(last_activity_at=timezone.now())
        )
        await db.flush()
        return activity


growth_funnel_service = GrowthFunnelService()
