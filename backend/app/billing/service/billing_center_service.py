"""费用与账单中心聚合服务（doc03 · 实施/92 MK-7）。

一次读齐账单中心「概览」所需：订阅+积分快照、权益总账、到期/宽限提醒条。
权益态从「行内 status + expires_at + 档位宽限策略」实时重算（与 access_service 同口径），
不依赖 sweeper 是否已跑，保证读时诚实（过期未清也照实显示 in_grace/expired）。
"""

from __future__ import annotations

from datetime import timedelta
from typing import TYPE_CHECKING

import sqlalchemy as sa

from backend.app.billing.model.billing_offering import BillingOffering
from backend.app.billing.model.billing_plan import BillingPlan
from backend.app.billing.schema.center import (
    BillingCenterResponse,
    BillingReminder,
    EntitlementLedgerItem,
)
from backend.app.billing.service.credit_service import credit_service
from backend.app.hasn.model.hasn_app_entitlement import HasnAppEntitlement
from backend.app.hasn.service import app_access_kernel, app_catalog_service
from backend.utils.timezone import timezone

if TYPE_CHECKING:
    from datetime import datetime

    from sqlalchemy.ext.asyncio import AsyncSession


def _iso(dt: datetime | None) -> str | None:
    return dt.isoformat() if dt else None


async def _load_offering_maps(
    db: AsyncSession, feature_keys: set[str]
) -> tuple[dict[str, BillingOffering], dict[str, BillingPlan]]:
    """按一组 feature_key 批量载入商品目录及其默认档（宽限/提醒策略取自默认档）。"""
    if not feature_keys:
        return {}, {}
    offerings = (
        (
            await db.execute(
                sa.select(BillingOffering).where(BillingOffering.feature_key.in_(feature_keys))
            )
        )
        .scalars()
        .all()
    )
    off_by_fk = {o.feature_key: o for o in offerings}
    keys = [o.key for o in offerings]
    plan_by_offering: dict[str, BillingPlan] = {}
    if keys:
        plans = (
            (
                await db.execute(
                    sa.select(BillingPlan)
                    .where(BillingPlan.offering_key.in_(keys), BillingPlan.status == 'active')
                    .order_by(BillingPlan.sort_order, BillingPlan.price_amount)
                )
            )
            .scalars()
            .all()
        )
        # 每个 offering 取最低 sort_order 的 active 档为默认档
        for p in plans:
            plan_by_offering.setdefault(p.offering_key, p)
    return off_by_fk, plan_by_offering


class BillingCenterService:
    """账单中心聚合（owner 维度 + 叠加当前活跃企业维度）。"""

    async def get_center(
        self, db: AsyncSession, *, user_id: int, app_code: str = 'huanxing'
    ) -> BillingCenterResponse:
        # 订阅+积分快照（全 JSON 安全原语）
        subscription = await credit_service.get_user_credits_info(db, user_id, app_code)

        owner_hasn_id = await app_catalog_service.resolve_owner_hasn_id(db, user_id=user_id)
        if not owner_hasn_id:
            # 无 owner 身份（异常态）→ 只回订阅快照，权益/提醒留空
            return BillingCenterResponse(subscription=subscription)

        active_enterprise_id = await app_access_kernel.resolve_active_enterprise_id(db, owner_hasn_id)

        # 收集本主人 + 当前活跃企业的全部权益行（非撤销优先，撤销行也带出供总账查看）
        subject_filters = [
            sa.and_(
                HasnAppEntitlement.subject_type == 'owner',
                HasnAppEntitlement.subject_id == owner_hasn_id,
            )
        ]
        if active_enterprise_id is not None:
            subject_filters.append(
                sa.and_(
                    HasnAppEntitlement.subject_type == 'enterprise',
                    HasnAppEntitlement.subject_id == str(active_enterprise_id),
                )
            )
        rows = (
            (
                await db.execute(
                    sa.select(HasnAppEntitlement)
                    .where(sa.or_(*subject_filters))
                    .order_by(HasnAppEntitlement.granted_at.desc())
                )
            )
            .scalars()
            .all()
        )

        feature_keys = {r.feature_key for r in rows if r.feature_key}
        off_by_fk, plan_by_offering = await _load_offering_maps(db, feature_keys)

        now = timezone.now()
        entitlements: list[EntitlementLedgerItem] = []
        reminders: list[BillingReminder] = []

        for r in rows:
            off = off_by_fk.get(r.feature_key)
            plan = plan_by_offering.get(off.key) if off is not None else None
            grace_json = (plan.grace_json or {}) if plan is not None else {}
            grace_days = int(grace_json.get('grace_days', 0) or 0)
            remind_days = grace_json.get('remind_days') or []
            display_name = off.display_name if off is not None else r.feature_key

            grace_until_dt: datetime | None = None
            # 实时重算五态（不信 DB status 存量）
            if r.status == 'revoked':
                eff_status = 'revoked'
            elif r.expires_at is None or r.expires_at > now:
                eff_status = 'trialing' if r.source == 'trial' else 'active'
            else:
                # 已过期：看是否在宽限窗内
                grace_until_dt = r.expires_at + timedelta(days=grace_days) if grace_days > 0 else None
                if grace_until_dt is not None and now <= grace_until_dt:
                    eff_status = 'in_grace'
                else:
                    eff_status = 'expired'
                    grace_until_dt = None

            entitlements.append(
                EntitlementLedgerItem(
                    feature_key=r.feature_key,
                    offering_key=off.key if off is not None else None,
                    display_name=display_name,
                    offering_kind=off.kind if off is not None else None,
                    subject_type=r.subject_type,
                    source=r.source,
                    status=eff_status,
                    seats_total=r.seats_total,
                    order_ref=r.order_ref,
                    quota_snapshot=r.quota_json or {},
                    granted_at=_iso(r.granted_at),
                    expires_at=_iso(r.expires_at),
                    grace_until=_iso(grace_until_dt),
                )
            )

            # 提醒条：即将到期（active/trialing 且在提醒窗内）或已进宽限
            if eff_status in ('active', 'trialing') and r.expires_at is not None and remind_days:
                days_left = (r.expires_at - now).days
                if 0 <= days_left <= max(int(d) for d in remind_days):
                    reminders.append(
                        BillingReminder(
                            feature_key=r.feature_key,
                            display_name=display_name,
                            kind='expiring',
                            at=_iso(r.expires_at),
                            days_left=days_left,
                        )
                    )
            elif eff_status == 'in_grace' and grace_until_dt is not None:
                reminders.append(
                    BillingReminder(
                        feature_key=r.feature_key,
                        display_name=display_name,
                        kind='in_grace',
                        at=_iso(grace_until_dt),
                        days_left=max(0, (grace_until_dt - now).days),
                    )
                )

        return BillingCenterResponse(
            subscription=subscription, entitlements=entitlements, reminders=reminders
        )


billing_center_service = BillingCenterService()
