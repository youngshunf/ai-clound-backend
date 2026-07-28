"""获客漏斗服务（设计 07 §5/§6/§10）：线索→客户→活动时间线的业务编排。

在 codegen CRUD 之上做 owner 隔离的漏斗推进：qualify（线索晋级客户+快照+回写线索态）、
dismiss（线索不合格）、客户列表/详情/时间线、画像更新、活动记录、线索检索。
一切查询按 user_id（owner）隔离，跨户 → NotFound。读类出参经 PII 脱敏（§10.2，API 传 reveal_pii）。

商机/成交在 opportunity_flow_service；触达状态机在 outreach_service；本服务不碰对外发送。
"""

from __future__ import annotations

from datetime import timedelta
from decimal import Decimal
from typing import Any
from uuid import uuid4

import sqlalchemy as sa

from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from backend.app.hasn_growth.model.activity import Activity
from backend.app.hasn_growth.model.customer import Customer
from backend.app.hasn_growth.model.lead_contact import LeadContact
from backend.app.hasn_growth.model.lead_ref import LeadRef
from backend.app.hasn_growth.model.opportunity import Opportunity
from backend.app.hasn_growth.model.outreach_message import OutreachMessage
from backend.app.hasn_growth.service.contact_privacy_service import contact_privacy_service
from backend.app.hasn_growth.service.lead_ingestion_privacy_service import (
    PrivateLeadWrite,
    lead_ingestion_privacy_service,
)
from backend.app.hasn_growth.service.pii import mask_contact_fields, redact_pii_value
from backend.app.hasn_growth.service.pii_keyring import require_growth_pii_keyring
from backend.app.hasn_growth.service.scope_context import GrowthScope, apply_scope, can_manage_assignment
from backend.common.exception import errors
from backend.core.conf import settings
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


def _customer_to_dict(c: Customer, *, reveal_pii: bool = False) -> dict[str, Any]:
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
        'owner_scope': c.owner_scope,
        'enterprise_id': c.enterprise_id,
        'assignee': c.assignee,
        'followup_task_id': c.followup_task_id,
        'tags': c.tags,
        'last_activity_at': c.last_activity_at,
        'next_followup_at': c.next_followup_at,
        'silent_round_count': c.silent_round_count,
        'created_time': c.created_time,
    }
    # 历史 reveal_pii 参数只为稳定内部签名保留；普通读永远不得绕过专用单渠道 reveal。
    return mask_contact_fields(data, reveal=False)


def _lead_to_dict(c: LeadContact, *, ref: LeadRef | None = None, reveal_pii: bool = False) -> dict[str, Any]:
    # 统一线索池：用户级状态（new/qualified/dismissed）与备注来自 lead_ref（非池行）；
    # 无 ref（如刚 request 交付的池行）默认 'new'。
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
        'status': ref.status if ref is not None else 'new',
        'ref_source': ref.source if ref is not None else None,
        'note': ref.note if ref is not None else None,
        'confidence_score': float(c.confidence_score) if c.confidence_score is not None else 0.0,
    }
    # 历史 reveal_pii 参数只为稳定内部签名保留；普通读永远不得绕过专用单渠道 reveal。
    return mask_contact_fields(data, reveal=False)


def _apply_masked_private_contact(
    lead: dict[str, Any],
    private_contact: dict[str, Any] | None,
) -> dict[str, Any]:
    """把私有表生成的脱敏视图覆盖到响应，不把明文写回公共 ORM。"""
    if private_contact is None:
        return lead
    lead['contact_name'] = private_contact.get('contact_name')
    for channel in private_contact.get('channels', []):
        channel_name = channel.get('channel')
        if channel_name in {'email', 'phone', 'wechat'}:
            lead[channel_name] = channel.get('masked_value')
    return lead


async def _masked_contact_for_lead(
    db: AsyncSession,
    *,
    lead_contact_id: int,
    owner_scope: str,
    user_id: int | None,
    enterprise_id: int | None,
) -> dict[str, Any] | None:
    """读取同一授权主体的脱敏私有资料，不允许个人与企业资料互相回退。"""
    return await contact_privacy_service.find_masked_contact_for_lead(
        db,
        lead_contact_id=lead_contact_id,
        owner_scope=owner_scope,
        user_id=user_id,
        enterprise_id=enterprise_id,
    )


async def _lead_response(
    db: AsyncSession,
    *,
    contact: LeadContact,
    ref: LeadRef | None,
    user_id: int,
) -> dict[str, Any]:
    data = _lead_to_dict(contact, ref=ref, reveal_pii=False)
    private_contact = await _masked_contact_for_lead(
        db,
        lead_contact_id=contact.id,
        owner_scope='personal',
        user_id=user_id,
        enterprise_id=None,
    )
    return _apply_masked_private_contact(data, private_contact)


async def _customer_response(db: AsyncSession, customer: Customer) -> dict[str, Any]:
    """客户普通读优先投影同主体私有资料；历史公共联系人只在响应边界脱敏。"""
    data = _customer_to_dict(customer, reveal_pii=False)
    if customer.lead_contact_id is None:
        return data
    private_contact = await _masked_contact_for_lead(
        db,
        lead_contact_id=customer.lead_contact_id,
        owner_scope=customer.owner_scope,
        user_id=customer.user_id if customer.owner_scope == 'personal' else None,
        enterprise_id=customer.enterprise_id if customer.owner_scope == 'enterprise' else None,
    )
    if private_contact is not None:
        return _apply_masked_private_contact(data, private_contact)
    legacy_contact = await db.get(LeadContact, customer.lead_contact_id)
    if legacy_contact is None:
        return data
    legacy_view = mask_contact_fields(
        {
            'contact_name': legacy_contact.contact_name,
            'email': legacy_contact.email,
            'phone': legacy_contact.phone,
        },
        reveal=False,
    )
    data['contact_name'] = legacy_view['contact_name']
    data['email'] = legacy_view['email']
    data['phone'] = legacy_view['phone']
    return data


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
        """检索「该用户引用的线索」（lead_ref JOIN contact），关键词/评分过滤，默认脱敏。已忽略的不返回。"""
        stmt = (
            sa
            .select(LeadContact, LeadRef)
            .join(LeadRef, LeadRef.lead_contact_id == LeadContact.id)
            .where(LeadRef.user_id == user_id, LeadRef.status != 'dismissed')
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
        rows = (await db.execute(stmt)).all()
        return [
            await _lead_response(db, contact=contact, ref=ref, user_id=user_id)
            for contact, ref in rows
        ]

    @staticmethod
    async def get_lead(
        db: AsyncSession, *, user_id: int, lead_contact_id: int, reveal_pii: bool = False
    ) -> dict[str, Any]:
        contact, ref = await GrowthFunnelService._load_lead(db, user_id=user_id, lead_contact_id=lead_contact_id)
        return await _lead_response(db, contact=contact, ref=ref, user_id=user_id)

    @staticmethod
    async def _load_lead(db: AsyncSession, *, user_id: int, lead_contact_id: int) -> tuple[LeadContact, LeadRef]:
        """加载「该用户引用的某条线索」(contact, ref)；无引用 → NotFound（统一池：拥有 = 有 lead_ref）。"""
        row = (
            await db.execute(
                sa
                .select(LeadContact, LeadRef)
                .join(LeadRef, LeadRef.lead_contact_id == LeadContact.id)
                .where(LeadRef.user_id == user_id, LeadRef.lead_contact_id == lead_contact_id)
            )
        ).first()
        if not row:
            raise errors.NotFoundError(msg='线索不存在或无权访问')
        return row[0], row[1]

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
        """主人手工登记线索；池行只留公开事实，姓名和联系方式进入 Owner 私有表。"""
        from urllib.parse import urlparse

        from backend.app.hasn_growth.service.cleaner_service import normalize_email, normalize_phone

        def _clean(v: str | None) -> str | None:
            v = v.strip() if v else None
            return v or None

        company_name = _clean(company_name)
        contact_name = _clean(contact_name)
        if not company_name and not contact_name:
            raise errors.RequestError(msg='请至少填写公司名或联系人名')
        if not settings.GROWTH_PII_NEW_WRITE_ENABLED:
            raise errors.ConflictError(
                msg='联系人 PII 新写尚未启用',
                data={'error_code': 'GROWTH_PII_NEW_WRITE_DISABLED'},
            )

        email = _clean(email)
        phone = _clean(phone)
        website = _clean(website)
        email_n = normalize_email(email) if email else None
        phone_n = normalize_phone(phone, country_hint='CN') if phone else None
        domain = None
        if website:
            netloc = urlparse(website if '://' in website else f'http://{website}').netloc
            domain = netloc.removeprefix('www.').lower() or None
        if email and email_n is None:
            raise errors.RequestError(msg='邮箱格式无效')
        if phone and phone_n is None:
            raise errors.RequestError(msg='电话格式无效')
        write_result = await lead_ingestion_privacy_service.upsert(
            db,
            keyring=require_growth_pii_keyring(),
            write=PrivateLeadWrite(
                user_id=user_id,
                pool_visibility='private',
                company_name=company_name,
                contact_name=contact_name,
                email=email_n,
                phone=phone_n,
                address=None,
                website=website,
                domain=domain,
                country=None,
                region=None,
                city=_clean(city),
                industry=_clean(industry),
                source_type='manual',
                source_url=None,
                lawful_basis='owner_provided',
                source_ref='manual_entry',
                retention_until=timezone.now() + timedelta(days=365),
                confidence_score=Decimal(
                    str(confidence_score if confidence_score is not None else 60)
                ),
                public_metadata={},
                preserve_existing_private=False,
            ),
        )
        contact = write_result.contact

        # 建用户引用（幂等：已引用则跳过）；note 为用户私有备注，落引用层。
        safe_note = redact_pii_value(note.strip()) if note and note.strip() else None
        await db.execute(
            pg_insert(LeadRef)
            .values(
                user_id=user_id,
                lead_contact_id=contact.id,
                source='manual',
                status='new',
                note=safe_note,
            )
            .on_conflict_do_nothing(constraint='uq_growth_lead_ref_user_lead')
        )
        await db.flush()
        ref = await db.scalar(
            sa.select(LeadRef).where(LeadRef.user_id == user_id, LeadRef.lead_contact_id == contact.id)
        )
        return _apply_masked_private_contact(
            _lead_to_dict(contact, ref=ref, reveal_pii=False),
            write_result.masked_private_contact,
        )

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
        scope: GrowthScope | None = None,
    ) -> dict[str, Any]:
        """线索晋级为客户（建 customer + 画像快照 + 回写线索态 + qualify 活动）。幂等：已晋级返回既有客户。

        enterprise 上下文：客户落 owner_scope='enterprise'+enterprise_id，assignee 默认=晋级人（经理可后续转移）；
        去重键随之改为 (enterprise_id, lead_contact_id)。personal 行为不变。
        """
        contact, ref = await GrowthFunnelService._load_lead(db, user_id=user_id, lead_contact_id=lead_contact_id)

        enterprise_scope = scope if scope is not None and scope.is_enterprise else None
        # 去重键双模：enterprise 按 (enterprise_id, lead)，personal 按 (user_id, lead)；
        # 不含 assignee（同企业同线索唯一）。
        dedupe_stmt = sa.select(Customer).where(Customer.lead_contact_id == lead_contact_id)
        if enterprise_scope is not None:
            dedupe_stmt = dedupe_stmt.where(
                Customer.owner_scope == 'enterprise',
                Customer.enterprise_id == enterprise_scope.enterprise_id,
            )
        else:
            dedupe_stmt = dedupe_stmt.where(Customer.owner_scope == 'personal', Customer.user_id == user_id)
        existing = (await db.execute(dedupe_stmt)).scalar_one_or_none()
        if existing:
            return await _customer_response(db, existing)

        score = Decimal(str(intent_score if intent_score is not None else (contact.confidence_score or 0)))
        customer = Customer(
            customer_no=_gen_no('CUS'),
            user_id=user_id,
            lead_contact_id=lead_contact_id,
            source_kind=_SOURCE_KIND_MAP.get(contact.source_type or 'manual', 'outbound_crawl'),
            company_name=contact.company_name,
            contact_name=None,
            email=None,
            phone=None,
            im_refs={},
            profile_json=redact_pii_value(profile or {}),
            intent_score=score,
            lifecycle_status='active',
            owner_agent_id=owner_agent_id,
            owner_scope='enterprise' if enterprise_scope is not None else 'personal',
            enterprise_id=enterprise_scope.enterprise_id if enterprise_scope is not None else None,
            assignee=enterprise_scope.owner_hasn_id if enterprise_scope is not None else None,
            tags=[],
            silent_round_count=0,
            last_activity_at=timezone.now(),
        )
        db.add(customer)
        ref.status = 'qualified'  # 用户级状态落引用层，不污染公共池行
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
        return await _customer_response(db, customer)

    @staticmethod
    async def dismiss_lead(db: AsyncSession, *, user_id: int, lead_contact_id: int, reason: str) -> dict[str, Any]:
        """用户忽略该线索（ref.status=dismissed + dismiss_reason，落引用层不污染公共池行；列表/检索不再返回）。"""
        contact, ref = await GrowthFunnelService._load_lead(db, user_id=user_id, lead_contact_id=lead_contact_id)
        ref.status = 'dismissed'
        safe_reason = redact_pii_value(reason)
        ref.dismiss_reason = safe_reason
        await db.flush()
        return {'lead_contact_id': contact.id, 'status': 'dismissed', 'reason': safe_reason}

    # ----------------------------- 客户 -----------------------------

    @staticmethod
    async def list_customers(
        db: AsyncSession,
        *,
        user_id: int,
        lifecycle_status: str | None = None,
        assignee: str | None = None,
        limit: int = 20,
        reveal_pii: bool = False,
        scope: GrowthScope | None = None,
    ) -> list[dict[str, Any]]:
        stmt = apply_scope(sa.select(Customer), Customer, user_id=user_id, scope=scope)
        if lifecycle_status:
            stmt = stmt.where(Customer.lifecycle_status == lifecycle_status)
        # 负责人筛选器（GE5.4）：经理可在企业全量基础上按某成员过滤；个人/销售传入无害（与 scope 取交集）。
        if assignee and scope is not None and scope.is_enterprise:
            stmt = stmt.where(Customer.assignee == assignee)
        stmt = stmt.order_by(Customer.intent_score.desc(), Customer.id.desc()).limit(min(limit, 100))
        rows = (await db.execute(stmt)).scalars().all()
        return [await _customer_response(db, customer) for customer in rows]

    @staticmethod
    async def _load_customer(
        db: AsyncSession, *, user_id: int, customer_id: int, scope: GrowthScope | None = None
    ) -> Customer:
        stmt = apply_scope(
            sa.select(Customer).where(Customer.id == customer_id), Customer, user_id=user_id, scope=scope
        )
        customer = (await db.execute(stmt)).scalar_one_or_none()
        if not customer:
            raise errors.NotFoundError(msg='客户不存在或无权访问')
        return customer

    @staticmethod
    async def get_customer(
        db: AsyncSession, *, user_id: int, customer_id: int, reveal_pii: bool = False, scope: GrowthScope | None = None
    ) -> dict[str, Any]:
        customer = await GrowthFunnelService._load_customer(db, user_id=user_id, customer_id=customer_id, scope=scope)
        return await _customer_response(db, customer)

    @staticmethod
    async def customer_timeline(
        db: AsyncSession, *, user_id: int, customer_id: int, limit: int = 30, scope: GrowthScope | None = None
    ) -> list[dict[str, Any]]:
        # 先 _load_customer（按 scope 校验可见性），通过后按 customer_id 取时间线（已门控，无需再按 user_id 隔离）。
        await GrowthFunnelService._load_customer(db, user_id=user_id, customer_id=customer_id, scope=scope)
        rows = (
            (
                await db.execute(
                    sa
                    .select(Activity)
                    .where(Activity.customer_id == customer_id)
                    .order_by(Activity.occurred_at.desc(), Activity.id.desc())
                    .limit(min(limit, 100))
                )
            )
            .scalars()
            .all()
        )
        return [
            {
                'id': a.id,
                'kind': a.kind,
                'content': redact_pii_value(a.content),
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
        scope: GrowthScope | None = None,
    ) -> dict[str, Any]:
        customer = await GrowthFunnelService._load_customer(db, user_id=user_id, customer_id=customer_id, scope=scope)
        if profile is not None:
            customer.profile_json = redact_pii_value(profile)
        if intent_score is not None:
            customer.intent_score = Decimal(str(intent_score))
        if tags is not None:
            customer.tags = redact_pii_value(tags)
        if lifecycle_status is not None:
            customer.lifecycle_status = lifecycle_status
            # 止损标 silent：累计静默轮次（设计 §7 止损）
            if lifecycle_status == 'silent':
                customer.silent_round_count = (customer.silent_round_count or 0) + 1
        if followup_task_id is not None:
            # 绑定/换绑当前跟进任务（J3 即时跟进据此触发 run_now）；空串解绑。
            normalized_task_id = followup_task_id.strip()
            if normalized_task_id and (
                len(normalized_task_id) > 64
                or not all(character.isalnum() or character in {'_', '-'} for character in normalized_task_id)
            ):
                raise errors.RequestError(
                    msg='跟进任务 ID 格式无效',
                    data={'error_code': 'GROWTH_FOLLOWUP_TASK_ID_INVALID'},
                )
            customer.followup_task_id = normalized_task_id or None
        customer.last_activity_at = timezone.now()
        await db.flush()
        return await _customer_response(db, customer)

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
        scope: GrowthScope | None = None,
    ) -> dict[str, Any]:
        await GrowthFunnelService._load_customer(db, user_id=user_id, customer_id=customer_id, scope=scope)
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
    async def reassign_customer(
        db: AsyncSession,
        *,
        user_id: int,
        customer_id: int,
        new_assignee: str,
        scope: GrowthScope | None = None,
    ) -> dict[str, Any]:
        """分配/转移负责人（GE4，仅企业经理）：改 customer.assignee 并级联下游 opportunity/outreach/activity，留痕。

        非经理（个人/销售）→ ForbiddenError（前端不渲染入口，后端仍是唯一防线）。
        """
        if not can_manage_assignment(scope):
            raise errors.ForbiddenError(msg='需要经理权限才能分配/转移负责人')
        new_assignee = (new_assignee or '').strip()
        if not new_assignee:
            raise errors.RequestError(msg='负责人不能为空')
        # 经理按企业全量加载（view=team），不受 restrict_to_self 限制。
        customer = await GrowthFunnelService._load_customer(db, user_id=user_id, customer_id=customer_id, scope=scope)
        customer.assignee = new_assignee
        await db.flush()
        # 级联：同客户的商机/触达/活动 assignee 一并更新，保持企业归属一致（看板/审批队列即时反映转移）。
        for model in (Opportunity, OutreachMessage, Activity):
            await db.execute(sa.update(model).where(model.customer_id == customer_id).values(assignee=new_assignee))
        await db.flush()
        await GrowthFunnelService._add_activity(
            db,
            user_id=user_id,
            customer_id=customer_id,
            kind='note',
            content=f'负责人转移为 {new_assignee}',
            actor_kind='owner',
            actor_id=scope.owner_hasn_id if scope else None,
        )
        return await _customer_response(db, customer)

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
        # 活动继承客户归属（owner_scope/enterprise_id/assignee），便于企业全量时间线聚合；
        # 单条轻量 SELECT，免去把 scope 透传到每个调用点（DRY）。
        cust = (
            await db.execute(
                sa.select(Customer.owner_scope, Customer.enterprise_id, Customer.assignee).where(
                    Customer.id == customer_id
                )
            )
        ).first()
        o_scope, ent_id, assignee = cust or ('personal', None, None)
        activity = Activity(
            customer_id=customer_id,
            opportunity_id=opportunity_id,
            user_id=user_id,
            kind=kind,
            content=redact_pii_value(content),
            actor_kind=actor_kind,
            actor_id=actor_id,
            ref_table=ref_table,
            ref_id=ref_id,
            owner_scope=o_scope,
            enterprise_id=ent_id,
            assignee=assignee,
            occurred_at=timezone.now(),
        )
        db.add(activity)
        # 同步客户最近活动游标（按 id，已由调用方门控可见性；不再按 user_id 过滤以兼容企业客户）。
        await db.execute(sa.update(Customer).where(Customer.id == customer_id).values(last_activity_at=timezone.now()))
        await db.flush()
        return activity


growth_funnel_service = GrowthFunnelService()
