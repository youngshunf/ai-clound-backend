"""获客落地页表单回流服务（设计 07 §8.4，inbound 主路径之一）。

落地页（模块 18 发布）表单 POST → 反滥用（蜜罐/最小字段）→ 解析 publish_ref 归属 owner →
建客户（source_kind=inbound_form，直通免 qualify）→ 留痕。客户主动留资，合规最优。
"""

from __future__ import annotations

from typing import Any
from uuid import uuid4

import sqlalchemy as sa

from sqlalchemy.ext.asyncio import AsyncSession

from backend.app.hasn.model.hasn_humans import HasnHumans
from backend.app.hasn_growth.model.customer import Customer
from backend.app.hasn_growth.model.form_submission import FormSubmission
from backend.app.hasn_growth.service.funnel_service import GrowthFunnelService
from backend.app.publish.model.site import Site
from backend.common.exception import errors
from backend.utils.timezone import timezone


def _gen_no(prefix: str) -> str:
    return f'{prefix}{uuid4().hex[:12].upper()}'


class GrowthFormService:
    """表单回流：publish_ref → owner → 客户。"""

    @staticmethod
    async def _resolve_owner(db: AsyncSession, *, publish_ref: str) -> tuple[int, str]:
        """落地页 slug → 发布者 owner（user_id, hasn_id）。未发布/已删/已过期 → NotFound。"""
        site = (
            await db.execute(
                sa.select(Site).where(
                    Site.slug == publish_ref, Site.status == 'active', Site.deleted_time.is_(None)
                )
            )
        ).scalar_one_or_none()
        if not site:
            raise errors.NotFoundError(msg='落地页不存在或已下线')
        human = (
            await db.execute(sa.select(HasnHumans).where(HasnHumans.hasn_id == site.owner_id))
        ).scalar_one_or_none()
        if not human:
            raise errors.NotFoundError(msg='落地页归属主人不存在')
        return human.user_id, site.owner_id

    @classmethod
    async def submit_form(
        cls,
        db: AsyncSession,
        *,
        publish_ref: str,
        data: dict[str, Any],
        source_meta: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """处理一次落地页表单提交。返回 {status, customer_id?, form_submission_id}。"""
        user_id, _owner = await cls._resolve_owner(db, publish_ref=publish_ref)
        source_meta = source_meta or {}

        email = (data.get('email') or '').strip() or None
        phone = (data.get('phone') or '').strip() or None
        wechat = (data.get('wechat') or '').strip() or None
        company = (data.get('company_name') or '').strip() or None
        contact = (data.get('contact_name') or '').strip() or None

        # 反滥用：蜜罐字段被填 → spam；或无任何联系方式 → spam（无效留资）。
        honeypot_filled = bool((data.get('website_url') or '').strip())
        no_contact = not (email or phone or wechat)
        is_spam = honeypot_filled or no_contact

        submission = FormSubmission(
            user_id=user_id,
            publish_ref=publish_ref,
            payload=data,
            email=email,
            phone=phone,
            name=contact,
            company=company,
            status='spam' if is_spam else 'pending',
            source_meta=source_meta,
        )
        db.add(submission)
        await db.flush()

        if is_spam:
            return {'status': 'spam', 'form_submission_id': submission.id, 'customer_id': None}

        customer = await cls._upsert_inbound_customer(
            db,
            user_id=user_id,
            company=company,
            contact=contact,
            email=email,
            phone=phone,
            wechat=wechat,
            message=data.get('message'),
        )
        submission.customer_id = customer['id']
        submission.status = 'converted'
        await db.flush()
        return {'status': 'converted', 'customer_id': customer['id'], 'form_submission_id': submission.id}

    @staticmethod
    async def _upsert_inbound_customer(
        db: AsyncSession,
        *,
        user_id: int,
        company: str | None,
        contact: str | None,
        email: str | None,
        phone: str | None,
        wechat: str | None,
        message: str | None,
    ) -> dict[str, Any]:
        """按 owner+email 去重；命中复用，否则建 inbound_form 客户（直通免 qualify）。"""
        existing: Customer | None = None
        if email:
            existing = (
                await db.execute(
                    sa.select(Customer).where(Customer.user_id == user_id, Customer.email == email)
                )
            ).scalar_one_or_none()
        if existing is not None:
            customer = existing
            note = f'落地页再次留资：{message[:200] if message else "（无留言）"}'
        else:
            customer = Customer(
                customer_no=_gen_no('CUS'),
                user_id=user_id,
                source_kind='inbound_form',
                company_name=company,
                contact_name=contact,
                email=email,
                phone=phone,
                wechat=wechat,
                im_refs={},
                profile_json={'inbound_message': message} if message else {},
                intent_score=50,  # 主动留资意向中等偏上起步
                lifecycle_status='engaged',
                tags=[],
                silent_round_count=0,
                last_activity_at=timezone.now(),
            )
            db.add(customer)
            await db.flush()
            note = f'落地页留资转化为客户：{message[:200] if message else "（无留言）"}'

        await GrowthFunnelService._add_activity(
            db,
            user_id=user_id,
            customer_id=customer.id,
            kind='reply',
            content=note,
            actor_kind='owner',
            actor_id=None,
            ref_table='form_submission',
        )
        from backend.app.hasn_growth.service.funnel_service import _customer_to_dict

        return _customer_to_dict(customer, reveal_pii=True)


growth_form_service = GrowthFormService()
