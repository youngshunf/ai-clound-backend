"""统一判定入口 resolve_access（商业化内核唯一付费墙判定 · doc02 §3 · 实施/92 MK-2）。

一切「某主体对某 feature_key 是否准入」的问题都从这里进出，返回统一 AccessDecision。
两条判定路：
- **app: 特征**（应用工具闸）→ 委托既有 app_access_kernel（catalog 驱动的 free/tier/purchase/
  beta/enterprise+席位 门，G3 口径逐位不变），再叠加 offer/quota/grace 富化；
- **通用特征**（llm:tier / credits:topup / 其它）→ 走 offering+plan+entitlement+grace+quota 通用门。

设计事实源：docs/hasn-node设计文档/16-订阅与积分计费/02-统一商业化内核设计.md §3.2/§4。
"""

from __future__ import annotations

from datetime import datetime, timedelta
from typing import TYPE_CHECKING

import sqlalchemy as sa

from backend.app.billing.model.billing_offering import BillingOffering
from backend.app.billing.model.billing_plan import BillingPlan
from backend.app.billing.schema.access import AccessDecision, AccessGrace, AccessOffer, AccessQuota
from backend.app.hasn.model.hasn_app_entitlement import HasnAppEntitlement
from backend.app.hasn.service import app_access_kernel, app_catalog_service
from backend.common.exception import errors
from backend.utils.timezone import timezone

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession


def _iso(dt: datetime | None) -> str | None:
    return dt.isoformat() if dt else None


async def _offering_and_plan(
    db: AsyncSession, feature_key: str
) -> tuple[BillingOffering | None, BillingPlan | None]:
    """按 feature_key 取商品目录 offering 及其默认档（最低 sort_order 的 active plan）。"""
    off = (
        await db.execute(sa.select(BillingOffering).where(BillingOffering.feature_key == feature_key))
    ).scalars().first()
    if off is None:
        return None, None
    plan = (
        await db.execute(
            sa.select(BillingPlan)
            .where(BillingPlan.offering_key == off.key, BillingPlan.status == 'active')
            .order_by(BillingPlan.sort_order, BillingPlan.price_amount)
        )
    ).scalars().first()
    return off, plan


def _build_offer(off: BillingOffering, plan: BillingPlan | None) -> AccessOffer:
    return AccessOffer(
        offering_key=off.key,
        plan_key=plan.plan_key if plan else None,
        display_name=off.display_name,
        price=float(plan.price_amount) if plan and plan.price_amount is not None else None,
        price_unit=(plan.price_unit if plan else 'cny') or 'cny',
        cycle=(plan.cycle if plan else 'once') or 'once',
        trial=(plan.trial_json if plan else {}) or {},
        purchase_uri=f'hasn://billing/offering/{off.key}',
    )


def _quota_exceeded(snapshot: dict | None, usage: dict | None) -> bool:
    """任一数值配额被用量超过即判超额（snapshot/usage 为空则不判超额）。"""
    if not snapshot or not usage:
        return False
    for key, limit in snapshot.items():
        if isinstance(limit, bool) or not isinstance(limit, (int, float)):
            continue
        used = usage.get(key)
        if isinstance(used, (int, float)) and not isinstance(used, bool) and used > limit:
            return True
    return False


async def _active_entitlement_fk(
    db: AsyncSession, *, feature_key: str, subject_type: str, subject_id: str
) -> HasnAppEntitlement | None:
    """按 feature_key 取有效权益（active 且未过期；expires_at 为空视为永久）。"""
    now = timezone.now()
    stmt = sa.select(HasnAppEntitlement).where(
        HasnAppEntitlement.feature_key == feature_key,
        HasnAppEntitlement.subject_type == subject_type,
        HasnAppEntitlement.subject_id == subject_id,
        HasnAppEntitlement.status == 'active',
        sa.or_(HasnAppEntitlement.expires_at.is_(None), HasnAppEntitlement.expires_at > now),
    )
    return (await db.execute(stmt)).scalars().first()


async def _latest_expired_fk(
    db: AsyncSession, *, feature_key: str, subject_type: str, subject_id: str
) -> HasnAppEntitlement | None:
    """按 feature_key 取最近一条「有到期时间且已过期」的权益（宽限期判定用）。"""
    now = timezone.now()
    stmt = (
        sa.select(HasnAppEntitlement)
        .where(
            HasnAppEntitlement.feature_key == feature_key,
            HasnAppEntitlement.subject_type == subject_type,
            HasnAppEntitlement.subject_id == subject_id,
            HasnAppEntitlement.expires_at.is_not(None),
            HasnAppEntitlement.expires_at <= now,
        )
        .order_by(HasnAppEntitlement.expires_at.desc())
    )
    return (await db.execute(stmt)).scalars().first()


async def _has_used_trial_fk(
    db: AsyncSession, *, feature_key: str, subject_type: str, subject_id: str
) -> bool:
    stmt = sa.select(HasnAppEntitlement.id).where(
        HasnAppEntitlement.feature_key == feature_key,
        HasnAppEntitlement.subject_type == subject_type,
        HasnAppEntitlement.subject_id == subject_id,
        HasnAppEntitlement.source == 'trial',
    )
    return (await db.execute(stmt)).scalars().first() is not None


def _norm_app_reason(base: dict) -> str:
    """把 app_access_kernel 的 AppAccess reason 归一到 AccessDecision 十态。"""
    if base.get('allowed'):
        reason = base.get('reason')
        return 'free' if reason in (None, 'not_in_catalog') else reason
    # 未准入但可开试用 → 统一收敛为 trial_available（前端渲染试用入口）
    if base.get('trial_available'):
        return 'trial_available'
    return base.get('reason') or 'need_purchase'


async def _resolve_app(
    db: AsyncSession,
    *,
    feature_key: str,
    owner_hasn_id: str,
    active_enterprise_id: int | None,
    usage: dict | None,
) -> AccessDecision:
    """app: 特征 → 委托既有 kernel（G3 口径不变）+ offer/quota 富化。"""
    app_id = feature_key.split(':', 1)[1]
    base = await app_access_kernel.resolve_merged_app_access(
        db, app_id=app_id, owner_hasn_id=owner_hasn_id, active_enterprise_id=active_enterprise_id
    )
    off, plan = await _offering_and_plan(db, feature_key)
    offer = _build_offer(off, plan) if off is not None else None

    decision = AccessDecision(
        allowed=bool(base.get('allowed')),
        reason=_norm_app_reason(base),
        feature_key=feature_key,
        requires=base.get('requires'),
        min_tier=base.get('min_tier'),
        trial_available=bool(base.get('trial_available')),
        entitlement_expires_at=base.get('entitlement_expires_at'),
        offer=offer,
    )
    # 已 entitled/trialing → 附配额快照（owner 维度权益行）
    if decision.allowed and base.get('reason') in ('entitled', 'trialing'):
        ent = await app_catalog_service.get_active_entitlement(
            db, app_id=app_id, subject_type='owner', subject_id=owner_hasn_id
        )
        if ent is not None:
            snapshot = ent.quota_json or {}
            decision.quota = AccessQuota(snapshot=snapshot, usage=usage or {})
            if _quota_exceeded(snapshot, usage):
                decision.allowed = False
                decision.reason = 'quota_exceeded'
                decision.requires = 'upgrade'
    return decision


async def _resolve_generic(
    db: AsyncSession,
    *,
    feature_key: str,
    subject_type: str,
    subject_id: str,
    usage: dict | None,
) -> AccessDecision:
    """通用特征（llm:tier / credits:topup / …）→ offering+entitlement+grace+quota 门。"""
    off, plan = await _offering_and_plan(db, feature_key)
    if off is None:
        # 未配置商品的特征 → 非门控放行（免费）
        return AccessDecision(allowed=True, reason='free', feature_key=feature_key)
    offer = _build_offer(off, plan)
    if off.status != 'active':
        return AccessDecision(allowed=False, reason='disabled', feature_key=feature_key, offer=offer)

    ent = await _active_entitlement_fk(db, feature_key=feature_key, subject_type=subject_type, subject_id=subject_id)
    if ent is not None:
        snapshot = ent.quota_json or {}
        quota = AccessQuota(snapshot=snapshot, usage=usage or {})
        if _quota_exceeded(snapshot, usage):
            return AccessDecision(
                allowed=False, reason='quota_exceeded', feature_key=feature_key,
                requires='upgrade', quota=quota, offer=offer,
                entitlement_expires_at=_iso(ent.expires_at),
            )
        reason = 'trialing' if ent.source == 'trial' else 'entitled'
        return AccessDecision(
            allowed=True, reason=reason, feature_key=feature_key, quota=quota, offer=offer,
            entitlement_expires_at=_iso(ent.expires_at),
        )

    # 无有效权益 → 宽限期判定
    expired = await _latest_expired_fk(db, feature_key=feature_key, subject_type=subject_type, subject_id=subject_id)
    grace_days = int((plan.grace_json or {}).get('grace_days', 0)) if plan else 0
    if expired is not None and grace_days > 0 and expired.expires_at is not None:
        until = expired.expires_at + timedelta(days=grace_days)
        if timezone.now() <= until:
            return AccessDecision(
                allowed=True, reason='expired_in_grace', feature_key=feature_key,
                grace=AccessGrace(until=_iso(until), recoverable=True), offer=offer,
                entitlement_expires_at=_iso(expired.expires_at),
            )

    # 无权益、无宽限 → 可试用则引导试用，否则引导购买
    trial_enabled = bool((plan.trial_json or {}).get('enabled')) if plan else False
    if trial_enabled and not await _has_used_trial_fk(
        db, feature_key=feature_key, subject_type=subject_type, subject_id=subject_id
    ):
        return AccessDecision(
            allowed=False, reason='trial_available', feature_key=feature_key,
            requires='purchase', trial_available=True, offer=offer,
        )
    return AccessDecision(
        allowed=False, reason='need_purchase', feature_key=feature_key, requires='purchase', offer=offer
    )


async def resolve_access(
    db: AsyncSession,
    *,
    feature_key: str,
    subject_type: str = 'owner',
    subject_id: str,
    active_enterprise_id: int | None = None,
    usage: dict | None = None,
) -> AccessDecision:
    """统一准入判定入口。subject 为主人（owner_hasn_id）；企业维度经 active_enterprise_id 叠加。"""
    if feature_key.startswith('app:'):
        return await _resolve_app(
            db, feature_key=feature_key, owner_hasn_id=subject_id,
            active_enterprise_id=active_enterprise_id, usage=usage,
        )
    return await _resolve_generic(
        db, feature_key=feature_key, subject_type=subject_type, subject_id=subject_id, usage=usage
    )


async def resolve_access_batch(
    db: AsyncSession,
    *,
    feature_keys: frozenset[str],
    subject_type: str = 'owner',
    subject_id: str,
    active_enterprise_id: int | None = None,
) -> dict[str, AccessDecision]:
    """批量判定一组 feature_key（per-request 预取，G3 工具闸逐工具零 IO 求值用）。"""
    result: dict[str, AccessDecision] = {}
    for fk in feature_keys:
        result[fk] = await resolve_access(
            db, feature_key=fk, subject_type=subject_type, subject_id=subject_id,
            active_enterprise_id=active_enterprise_id,
        )
    return result


async def grant_trial(
    db: AsyncSession, *, feature_key: str, subject_type: str = 'owner', subject_id: str
) -> AccessDecision:
    """发放一次试用并返回判定。app: 委托既有 open_trial（catalog 校验不变）；通用特征走内核试用。"""
    if feature_key.startswith('app:'):
        app_id = feature_key.split(':', 1)[1]
        cat = await app_catalog_service.get_catalog(db, app_id=app_id)
        if cat is None:
            raise errors.RequestError(msg='应用不存在')
        await app_catalog_service.open_trial(db, catalog=cat, owner_hasn_id=subject_id, subject_type=subject_type)
        await db.flush()
        return await resolve_access(db, feature_key=feature_key, subject_type=subject_type, subject_id=subject_id)

    off, plan = await _offering_and_plan(db, feature_key)
    trial = (plan.trial_json or {}) if plan else {}
    if off is None or not trial.get('enabled'):
        raise errors.RequestError(msg='该商品不支持试用')
    if await _has_used_trial_fk(db, feature_key=feature_key, subject_type=subject_type, subject_id=subject_id):
        raise errors.RequestError(msg='试用机会已用过')
    if await _active_entitlement_fk(db, feature_key=feature_key, subject_type=subject_type, subject_id=subject_id):
        raise errors.RequestError(msg='已有有效权益，无需试用')
    days = int(trial.get('days', 0)) or 7
    now = timezone.now()
    ent = HasnAppEntitlement(
        # 通用特征无 catalog app_id，用 feature_key 占位保唯一（避免 app_id='' 撞唯一约束）
        app_id=feature_key,
        feature_key=feature_key,
        subject_type=subject_type,
        subject_id=subject_id,
        source='trial',
        status='active',
        quota_json=(plan.quota_json or {}) if plan else {},
        granted_at=now,
        expires_at=now + timedelta(days=days),
    )
    db.add(ent)
    await db.flush()
    return await resolve_access(db, feature_key=feature_key, subject_type=subject_type, subject_id=subject_id)
