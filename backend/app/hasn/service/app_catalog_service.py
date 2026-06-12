"""应用目录 / 权益领域服务（C1 数据层）。

设计事实源：docs/hasn-node设计文档/14-AI-Native应用平台/16-应用目录与商业化管理统一设计.md

职责：
- ``ensure_catalog_seeded``：从 ``workbench_app_registry`` 幂等播种 ``hasn_app_catalog``（迁移 M2）。
  **只插入缺失行，绝不回写已存在行的 display/价格**——这是「代码不覆盖运营改动」的关键
  （区别于 manifest 的 hash 自愈逻辑，见设计 §6.1）。
- ``sweep_expired_entitlements``：把 ``expires_at < now`` 的 active 权益置 expired（设计 §5.4 定时兜底）。

生成的 ``hasn_app_catalog_service`` / ``hasn_app_entitlement_service`` 负责 Admin CRUD；
本模块只承载播种与兜底这类领域逻辑，避免改动 codegen 产物。
"""

from __future__ import annotations

from datetime import timedelta
from typing import TYPE_CHECKING

import sqlalchemy as sa

from backend.app.hasn.model.hasn_app_catalog import HasnAppCatalog
from backend.app.hasn.model.hasn_app_entitlement import HasnAppEntitlement
from backend.app.hasn.model.hasn_humans import HasnHumans
from backend.app.hasn.service.workbench_app_registry import WorkbenchApp, workbench_app_registry
from backend.app.user_tier.model import UserSubscription
from backend.common.exception import errors
from backend.utils.timezone import timezone

if TYPE_CHECKING:
    from datetime import datetime

    from sqlalchemy.ext.asyncio import AsyncSession

# 默认应用标识（订阅档与 billing 同源，见 [[project_billing_newapi_authoritative_source]]）。
_DEFAULT_APP_CODE = 'huanxing'

# 订阅档高低序（设计 §5.4 枚举 free/pro/advanced/flagship）。未知档位保守按最低 free 处理。
_TIER_RANK: dict[str, int] = {'free': 0, 'pro': 1, 'advanced': 2, 'flagship': 3}

# 工作台排序（小在前）。未列出的 app 落到默认值之后。
_CATALOG_SORT_ORDER: dict[str, int] = {
    'knowledge': 10,
    'community': 20,
    'deck': 35,
    'publish': 40,
    'growth': 45,  # 获客（设计 §3.2 约 40，置于 publish 之后；default_mount=FALSE 由 install_policy=manual 推导）
}
_DEFAULT_SORT_ORDER = 100


def _catalog_row_from_workbench_app(app: WorkbenchApp) -> dict:
    """把 WorkbenchApp 映射为 catalog 行的默认值（迁移期单一来源）。

    新增字段（source/status/商业化…）取保守默认：全部内置、已上架、免费。
    """
    return {
        'app_id': app.id,
        'name': app.name,
        'icon': app.icon,
        'icon_asset_uri': None,
        'description': app.description,
        'source': 'builtin',
        'status': 'published',
        'execution_mode': app.execution_mode,
        'scope': list(app.scope),
        'collaboration_mode': app.collaboration_mode,
        'entry_route': app.entry_route,
        'sort_order': _CATALOG_SORT_ORDER.get(app.id, _DEFAULT_SORT_ORDER),
        'default_mount': app.install_policy == 'auto',
        'requires_role': app.requires_role,
        # 商业化默认：保持现状全免费（迁移 M2 不变量）。
        'access_type': 'free',
        'min_tier': None,
        'price_amount': None,
        'price_unit': 'cny',
        'billing_cycle': 'once',
        'trial_days': 0,
        'sku_ref': None,
        # 现有 builtin（knowledge/community/deck）都有对应 code manifest。
        'manifest_present': True,
    }


async def ensure_catalog_seeded(db: AsyncSession) -> int:
    """幂等播种 catalog：仅插入缺失的 app_id 行，已存在行原样保留。

    返回新插入的行数。可在部署 reconcile / 测试夹具中调用。
    """
    existing = set((await db.execute(sa.select(HasnAppCatalog.app_id))).scalars().all())
    inserted = 0
    for app in workbench_app_registry.list():
        if app.id in existing:
            continue
        db.add(HasnAppCatalog(**_catalog_row_from_workbench_app(app)))
        inserted += 1
    if inserted:
        await db.flush()
    return inserted


async def sweep_expired_entitlements(db: AsyncSession) -> int:
    """把已过期的 active 权益置为 expired（定时兜底，与订阅过期兜底同构）。

    返回受影响行数。``expires_at IS NULL`` 视为永久买断，不受影响。
    """
    now = timezone.now()
    result = await db.execute(
        sa.update(HasnAppEntitlement)
        .where(
            HasnAppEntitlement.status == 'active',
            HasnAppEntitlement.expires_at.is_not(None),
            HasnAppEntitlement.expires_at < now,
        )
        .values(status='expired', updated_time=now)
    )
    return result.rowcount or 0


# ============================ C2：catalog 作为展示权威 ============================


def catalog_to_manifest(cat: HasnAppCatalog, *, registry_app: WorkbenchApp | None = None) -> dict:
    """把 catalog 行映射为工作台 manifest（与 ``WorkbenchApp.to_manifest`` 同形 + ``icon_asset_uri``）。

    launch 字段（ui_kind/window_url/window_origin）catalog 不存——迁移期从本地 ``registry_app``
    overlay；registry 在 C6 退役后由 daemon 本地提供（对齐设计 §3 边界「本地 builtin 只保留 launch 字段」）。
    """
    return {
        'id': cat.app_id,
        'name': cat.name,
        'icon': cat.icon,
        'icon_asset_uri': cat.icon_asset_uri,
        'description': cat.description,
        'scope': list(cat.scope or []),
        'collaboration_mode': cat.collaboration_mode,
        'entry_route': cat.entry_route,
        'install_policy': 'auto' if cat.default_mount else 'manual',
        'requires_role': cat.requires_role,
        'execution_mode': cat.execution_mode,
        'ui_kind': registry_app.ui_kind if registry_app else None,
        'window_url': registry_app.window_url if registry_app else None,
        'window_origin': registry_app.window_origin if registry_app else None,
    }


async def list_published_catalog(db: AsyncSession, *, kind: str | None = None) -> list[HasnAppCatalog]:
    """已上架 catalog 行（按 sort_order 升序），可选按可挂载空间类型（personal/enterprise）过滤。"""
    stmt = (
        sa.select(HasnAppCatalog)
        .where(HasnAppCatalog.status == 'published')
        .order_by(HasnAppCatalog.sort_order, HasnAppCatalog.id)
    )
    rows = list((await db.execute(stmt)).scalars().all())
    if kind is not None:
        rows = [r for r in rows if kind in (r.scope or [])]
    return rows


async def get_published_catalog(db: AsyncSession, *, app_id: str) -> HasnAppCatalog | None:
    """取单个**已上架** catalog 行；不存在或已下架返回 None（下架即任何人不可用）。"""
    stmt = sa.select(HasnAppCatalog).where(
        HasnAppCatalog.app_id == app_id,
        HasnAppCatalog.status == 'published',
    )
    return (await db.execute(stmt)).scalars().first()


async def get_catalog(db: AsyncSession, *, app_id: str) -> HasnAppCatalog | None:
    """取单个 catalog 行（**任意状态**）；用于准入闸门——下架的付费 app 须 deny 而非静默放行。"""
    stmt = sa.select(HasnAppCatalog).where(HasnAppCatalog.app_id == app_id)
    return (await db.execute(stmt)).scalars().first()


async def resolve_owner_hasn_id(db: AsyncSession, *, user_id: int) -> str | None:
    """唤星平台 user_id → owner hasn_id（h_xxx）；无映射返回 None。"""
    stmt = sa.select(HasnHumans.hasn_id).where(HasnHumans.user_id == user_id)
    return (await db.execute(stmt)).scalars().first()


# ============================ C4：商业化准入（resolve_app_access + 三闸门） ============================


def _tier_rank(tier: str | None) -> int:
    """订阅档高低序；未知/None 保守按最低 free=0。"""
    return _TIER_RANK.get((tier or 'free'), 0)


async def _resolve_owner_user_id(db: AsyncSession, owner_hasn_id: str) -> int | None:
    """owner hasn_id（h_xxx）→ 唤星平台 user_id；无映射返回 None（按 free 兜底）。"""
    stmt = sa.select(HasnHumans.user_id).where(HasnHumans.hasn_id == owner_hasn_id)
    user_id = (await db.execute(stmt)).scalars().first()
    return int(user_id) if user_id else None


async def owner_effective_tier(db: AsyncSession, *, owner_hasn_id: str) -> str:
    """owner 的**有效订阅档**（实时读，零新增存储；复用 billing UserSubscription）。

    存储的 ``tier`` 字段过期不降级（只 ``status`` 翻 expired，见 credit_service.get_user_credits_info）；
    准入须按日期重算：``status`` 已过期或订阅结束日已过 → 有效档位回落 ``free``。免费档无结束日永不过期。
    """
    user_id = await _resolve_owner_user_id(db, owner_hasn_id)
    if user_id is None:
        return 'free'
    stmt = sa.select(UserSubscription).where(
        UserSubscription.user_id == user_id,
        UserSubscription.app_code == _DEFAULT_APP_CODE,
    )
    sub = (await db.execute(stmt)).scalars().first()
    if sub is None or sub.tier == 'free' or not sub.tier:
        return 'free'
    now = timezone.now()
    if sub.status == 'expired':
        return 'free'
    if sub.subscription_end_date is not None and now > sub.subscription_end_date:
        return 'free'
    return sub.tier


async def get_active_entitlement(
    db: AsyncSession, *, app_id: str, subject_type: str, subject_id: str
) -> HasnAppEntitlement | None:
    """取该主体对该 app 的**有效**权益行（active 且未过期；``expires_at IS NULL`` 视为永久买断）。"""
    now = timezone.now()
    stmt = sa.select(HasnAppEntitlement).where(
        HasnAppEntitlement.app_id == app_id,
        HasnAppEntitlement.subject_type == subject_type,
        HasnAppEntitlement.subject_id == subject_id,
        HasnAppEntitlement.status == 'active',
        sa.or_(
            HasnAppEntitlement.expires_at.is_(None),
            HasnAppEntitlement.expires_at > now,
        ),
    )
    return (await db.execute(stmt)).scalars().first()


async def _has_used_trial(
    db: AsyncSession, *, app_id: str, subject_type: str, subject_id: str
) -> bool:
    """该主体是否已对此 app 用过试用（任何状态的 source=trial 行都算，强制「只能开一次」）。"""
    stmt = sa.select(HasnAppEntitlement.id).where(
        HasnAppEntitlement.app_id == app_id,
        HasnAppEntitlement.subject_type == subject_type,
        HasnAppEntitlement.subject_id == subject_id,
        HasnAppEntitlement.source == 'trial',
    )
    return (await db.execute(stmt)).scalars().first() is not None


def _price_payload(cat: HasnAppCatalog) -> dict | None:
    """购买价信息（设计 §5.2 price 字段）；无价返回 None。"""
    if cat.price_amount is None:
        return None
    return {
        'amount': float(cat.price_amount),
        'unit': cat.price_unit or 'cny',
        'cycle': cat.billing_cycle or 'once',
    }


async def resolve_app_access(
    db: AsyncSession,
    *,
    catalog: HasnAppCatalog,
    owner_hasn_id: str,
    subject_type: str = 'owner',
) -> dict:
    """统一准入决策函数（设计 §5.2）。返回 AppAccess dict。

    判定顺序（§5.2）：
      1. status != published → disabled（下架，任何人不可用）
      2. free → allowed/free
      3. tier → owner 有效档位 ≥ min_tier ? allowed/tier_ok : need_upgrade（附 trial_available）
      4. purchase → 有 active 权益 ? allowed/entitled（trial 来源则 trialing）: need_purchase（附 trial_available）

    subject_id 取 owner_hasn_id（owner 维度）；企业维度由调用方传 subject_type='enterprise' + 对应 subject_id。
    """
    subject_id = owner_hasn_id

    def _access(
        *,
        allowed: bool,
        reason: str,
        requires: str | None = None,
        trial_available: bool = False,
        entitlement_expires_at: datetime | None = None,
    ) -> dict:
        return {
            'allowed': allowed,
            'reason': reason,
            'requires': requires,
            'min_tier': catalog.min_tier,
            'price': _price_payload(catalog) if requires == 'purchase' else None,
            'trial_available': trial_available,
            'entitlement_expires_at': (
                entitlement_expires_at.isoformat() if entitlement_expires_at else None
            ),
        }

    if catalog.status != 'published':
        return _access(allowed=False, reason='disabled')

    access_type = catalog.access_type or 'free'
    if access_type == 'free':
        return _access(allowed=True, reason='free')

    if access_type == 'tier':
        effective_tier = await owner_effective_tier(db, owner_hasn_id=owner_hasn_id)
        if _tier_rank(effective_tier) >= _tier_rank(catalog.min_tier):
            return _access(allowed=True, reason='tier_ok')
        trial_available = bool(catalog.trial_days) and not await _has_used_trial(
            db, app_id=catalog.app_id, subject_type=subject_type, subject_id=subject_id
        )
        return _access(allowed=False, reason='need_upgrade', requires='upgrade', trial_available=trial_available)

    if access_type == 'purchase':
        ent = await get_active_entitlement(
            db, app_id=catalog.app_id, subject_type=subject_type, subject_id=subject_id
        )
        if ent is not None:
            reason = 'trialing' if ent.source == 'trial' else 'entitled'
            return _access(allowed=True, reason=reason, entitlement_expires_at=ent.expires_at)
        trial_available = bool(catalog.trial_days) and not await _has_used_trial(
            db, app_id=catalog.app_id, subject_type=subject_type, subject_id=subject_id
        )
        return _access(allowed=False, reason='need_purchase', requires='purchase', trial_available=trial_available)

    # 未知 access_type 保守拒绝（不静默放行付费能力）。
    return _access(allowed=False, reason='disabled')


# ============================ C5：权益写操作（试用 / 购买 / admin 授予 / 撤销） ============================

# 购买周期 → 权益时长（天）。``once`` = 永久买断（expires_at=None）。
_PURCHASE_CYCLE_DAYS: dict[str, int] = {'month': 30, 'monthly': 30, 'year': 365, 'yearly': 365}


def purchase_expiry(billing_cycle: str | None):
    """按 billing_cycle 计算购买权益到期时间；``once``/未知周期 → None（永久买断）。"""
    days = _PURCHASE_CYCLE_DAYS.get(billing_cycle or 'once')
    return timezone.now() + timedelta(days=days) if days else None


async def open_trial(
    db: AsyncSession, *, catalog: HasnAppCatalog, owner_hasn_id: str, subject_type: str = 'owner'
) -> HasnAppEntitlement:
    """owner 主动开通一次试用（设计 §5.1）。写 source=trial 权益，``expires_at = now + trial_days``。

    校验：app 须 published + 付费(tier/purchase) + trial_days>0 + 未用过试用 + 无 active 权益。
    违反则抛 ForbiddenError/RequestError（调用方转 4xx）。
    """
    if catalog.status != 'published':
        raise errors.ForbiddenError(msg='应用已下架')
    if (catalog.access_type or 'free') == 'free' or not catalog.trial_days:
        raise errors.RequestError(msg='该应用不支持试用')
    subject_id = owner_hasn_id
    if await _has_used_trial(db, app_id=catalog.app_id, subject_type=subject_type, subject_id=subject_id):
        raise errors.RequestError(msg='试用机会已用过')
    if await get_active_entitlement(db, app_id=catalog.app_id, subject_type=subject_type, subject_id=subject_id):
        raise errors.RequestError(msg='已有有效权益，无需试用')
    now = timezone.now()
    ent = HasnAppEntitlement(
        app_id=catalog.app_id,
        subject_type=subject_type,
        subject_id=subject_id,
        source='trial',
        status='active',
        granted_at=now,
        expires_at=now + timedelta(days=catalog.trial_days),
    )
    db.add(ent)
    await db.flush()
    return ent


async def grant_entitlement(
    db: AsyncSession,
    *,
    app_id: str,
    subject_type: str,
    subject_id: str,
    source: str,
    order_ref: str | None = None,
    expires_at=None,
) -> HasnAppEntitlement:
    """写一条 active 权益（购买回调 / admin 授予共用）。已有 active 则幂等返回（不重复发）。

    唯一约束 ``uq_app_entitlement_active`` 保证每主体每 app 至多一条 active，故先查后写。
    """
    existing = await get_active_entitlement(db, app_id=app_id, subject_type=subject_type, subject_id=subject_id)
    if existing is not None:
        return existing
    ent = HasnAppEntitlement(
        app_id=app_id,
        subject_type=subject_type,
        subject_id=subject_id,
        source=source,
        status='active',
        order_ref=order_ref,
        granted_at=timezone.now(),
        expires_at=expires_at,
    )
    db.add(ent)
    await db.flush()
    return ent


async def revoke_entitlement(db: AsyncSession, *, entitlement_id: int) -> bool:
    """撤销权益（置 status=revoked）。返回是否实际改动。"""
    result = await db.execute(
        sa.update(HasnAppEntitlement)
        .where(HasnAppEntitlement.id == entitlement_id, HasnAppEntitlement.status == 'active')
        .values(status='revoked', updated_time=timezone.now())
    )
    return (result.rowcount or 0) > 0


async def list_entitlements(
    db: AsyncSession, *, subject_type: str, subject_id: str, active_only: bool = False
) -> list[HasnAppEntitlement]:
    """列某主体的权益（owner「我的已购」/ admin 查某主体）。"""
    stmt = sa.select(HasnAppEntitlement).where(
        HasnAppEntitlement.subject_type == subject_type,
        HasnAppEntitlement.subject_id == subject_id,
    )
    if active_only:
        now = timezone.now()
        stmt = stmt.where(
            HasnAppEntitlement.status == 'active',
            sa.or_(HasnAppEntitlement.expires_at.is_(None), HasnAppEntitlement.expires_at > now),
        )
    stmt = stmt.order_by(HasnAppEntitlement.id.desc())
    return list((await db.execute(stmt)).scalars().all())
