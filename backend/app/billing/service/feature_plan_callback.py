"""通用 `feature_plan` 商品履约（MK-2 通用路）——付款成功 → 发/续该商品对应的 feature 权益。

`offering.kind='feature_plan'` 是**一整类**加购型商品的发货轴（首个落地商品是云端常驻节点
`cloud:node`，配额键 `max_cloud_nodes`）。注册进 `register_fulfillment(KIND_FEATURE_PLAN, …)`
之后，**所有** `kind='feature_plan'` 的订单都进这一个处理器，所以本模块刻意**不认识任何具体商品**，
全部数据驱动：

- `feature_key` 从订单指向的 `billing_offering.feature_key` 读，不硬编码；
- 配额从 `billing_plan.quota_json` **整包合并**进权益快照（数值键累加、非数值键覆盖）；
- 到期按 `billing_plan.cycle` 推：`month`/`year` 顺延一个自然周期，`once` = 买断 → `expires_at=NULL`
  （`access_service._active_entitlement_fk` 把空 `expires_at` 视为永久）。

于是将来新增一个加购型商品，后端**只需**：库里加 offering/plan 行 + 在
`billing.core.feature_registry` 注册它的 `feature_key`（这一行改代码是刻意保留的防漂移闸，
不是遗漏），**不必再写一个 handler**。

**一个 feature_key 每主体只发一条权益行（铁律）**：`access_service._active_entitlement_fk` 取的是
`.first()`，只认一条——买 N 份必须在**同一行**上把数值配额累加，插 N 行的话第 2 行起永远不会被
读到，用户付了钱拿不到配额。

幂等与退款依据：`PayOrderService.handle_pay_notify` 已对订单 `status==1` 短路 + `FOR UPDATE` 锁；
本模块额外把「本单实际发了什么」写进 `pay_order.extra_data.fulfilled_grant` 作为**发货回执**——
既是重放去重标记，也是退款回收的精确依据（退的是**本单当初发的量**，不受期间改价影响）。

参数缺失一律抛错让订单进 `fulfillment_status=dead`，绝不「打日志后 return」把订单留在假成功。
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from datetime import datetime
from typing import TYPE_CHECKING, Any, Final

import sqlalchemy as sa

from dateutil.relativedelta import relativedelta

from backend.app.billing.core import feature_registry
from backend.app.billing.core.fulfillment import KIND_FEATURE_PLAN
from backend.app.billing.model.billing_offering import BillingOffering
from backend.app.billing.model.billing_plan import BillingPlan
from backend.app.hasn.model.hasn_app_entitlement import HasnAppEntitlement
from backend.common.exception import errors
from backend.common.log import log
from backend.utils.timezone import timezone

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession

#: 权益主体：加购型商品目前只卖给主人（owner）；企业维度另有席位轴（`kind='seat'`）。
SUBJECT_TYPE_OWNER: Final[str] = 'owner'

#: 发货回执写在 `pay_order.extra_data` 的这个键下（重放去重 + 退款回收依据）。
FULFILLED_GRANT_KEY: Final[str] = 'fulfilled_grant'

#: `billing_plan.cycle='once'` = 一次性买断，权益永不过期（`expires_at=NULL`）。
CYCLE_ONCE: Final[str] = 'once'

#: `billing_plan.cycle` → 顺延步长。用 `relativedelta` 而非固定天数：
#: 「+1 个月」是自然月语义，用 30 天近似会让 1 月购买的年付在闰年少一天。
_CYCLE_DELTAS: Final[dict[str, relativedelta]] = {
    'month': relativedelta(months=1),
    'monthly': relativedelta(months=1),
    'year': relativedelta(years=1),
    'yearly': relativedelta(years=1),
}

#: feature 用量探针：`feature_key` -> `async probe(db, owner_hasn_id) -> {配额键: 已用量}`。
#: 退款回收后用来对账「配额是否已低于在用量」并如实告警。用量口径属于各业务域
#: （云端节点数在 hasn_hosting、席位在 hasn），本模块不该知道，故做成注册表。
_usage_probes: dict[str, Callable[..., Awaitable[dict[str, int]]]] = {}


def register_feature_usage_probe(
    feature_key: str, probe: Callable[..., Awaitable[dict[str, int]]]
) -> None:
    """注册某 feature 的用量探针（各业务域启动时调用）。

    探针签名：``async probe(db, *, owner_hasn_id) -> {配额键: 已用量}``。
    只用于退款后的缺口告警，**不参与准入判定**——判定的唯一入口仍是 `resolve_access`。
    """
    _usage_probes[feature_key] = probe
    log.info(f'[FeaturePlan] 注册用量探针: feature_key={feature_key}, probe={probe.__name__}')


def get_registered_usage_probe_keys() -> set[str]:
    """已注册用量探针的 feature_key 集合（自省 / 守卫用）。"""
    return set(_usage_probes)


# ─── 订单解析（缺任何一项都抛错进死信，绝不猜） ───


def _order_no(order: Any) -> str:
    return str(getattr(order, 'order_no', '') or '?')


def _parse_offering_ref(order: Any) -> tuple[str, str]:
    """从订单快照解析 `(offering_key, plan_key)`。

    `plan_key` 缺失时回落 `order.billing_cycle`（下单入口若只填了周期列，档位键与之同名）。
    两者都拿不到就抛错——猜一个档位等于按错价发货。
    """
    ref = getattr(order, 'offering_ref', None) or {}
    offering_key = ref.get('offering_key')
    if not offering_key:
        raise errors.RequestError(msg=f'订单 {_order_no(order)} 的商品快照缺少 offering_key，拒绝静默放行')
    plan_key = ref.get('plan_key') or getattr(order, 'billing_cycle', None)
    if not plan_key:
        raise errors.RequestError(msg=f'订单 {_order_no(order)} 缺少 plan_key（所购档位），拒绝静默放行')
    return str(offering_key), str(plan_key)


def _parse_quantity(order: Any) -> int:
    """本单购买份数（`extra_data.quantity`，缺省 1 份）。非正整数一律抛错。"""
    raw = (getattr(order, 'extra_data', None) or {}).get('quantity', 1)
    try:
        quantity = int(raw)
    except (TypeError, ValueError):
        raise errors.RequestError(msg=f'订单 {_order_no(order)} 的购买份数非法: {raw!r}')
    if quantity <= 0:
        raise errors.RequestError(msg=f'订单 {_order_no(order)} 的购买份数必须为正: {quantity}')
    return quantity


async def _load_offering(db: AsyncSession, *, offering_key: str, order_no: str) -> BillingOffering:
    """取商品行（`feature_key` 的权威来源）。

    **不校验 `status`**：商品下架后已付款的订单仍须履约，否则用户付了钱拿不到货。
    校验 `kind`：订单快照说是 `feature_plan`、live 行却不是，说明目录漂移了，
    此时按错误的轴发货比不发更糟 → 抛错进死信。
    """
    off = (
        await db.execute(sa.select(BillingOffering).where(BillingOffering.key == offering_key))
    ).scalars().first()
    if off is None:
        raise errors.RequestError(msg=f'订单 {order_no} 指向的商品 {offering_key} 不存在，拒绝静默放行')
    if off.kind != KIND_FEATURE_PLAN:
        raise errors.RequestError(
            msg=f'订单 {order_no} 的商品 {offering_key} 种类为 {off.kind!r}（非 feature_plan），拒绝按错误轴发货'
        )
    if not feature_registry.is_registered(off.feature_key):
        # 防漂移闸：新商品必须先在 feature_registry 登记 feature_key，
        # 否则付费墙那侧根本认不出它，会出现「发了权益但永远判不出准入」的幽灵权益。
        raise errors.RequestError(
            msg=f'商品 {offering_key} 的 feature_key {off.feature_key!r} 未在 feature_registry 注册，拒绝发货'
        )
    return off


async def _load_plan(db: AsyncSession, *, offering_key: str, plan_key: str, order_no: str) -> BillingPlan:
    """取档位行（配额快照与计费周期的权威来源）。"""
    plan = (
        await db.execute(
            sa.select(BillingPlan).where(
                BillingPlan.offering_key == offering_key, BillingPlan.plan_key == plan_key
            )
        )
    ).scalars().first()
    if plan is None:
        raise errors.RequestError(msg=f'订单 {order_no} 指向的档位 {offering_key}/{plan_key} 不存在，拒绝静默放行')
    return plan


async def _resolve_owner(db: AsyncSession, *, order: Any) -> str:
    """`order.user_id` → owner hasn_id（权益主体）。无映射即抛错：发给空气不如进死信。"""
    from backend.app.hasn.service import app_catalog_service

    owner_hasn_id = await app_catalog_service.resolve_owner_hasn_id(db, user_id=order.user_id)
    if not owner_hasn_id:
        raise errors.RequestError(
            msg=f'订单 {_order_no(order)} 的用户 {order.user_id} 无 owner 映射，无法确定权益主体'
        )
    return str(owner_hasn_id)


# ─── 配额合并（数值累加 / 非数值覆盖） ───


def _as_number(value: Any) -> float:
    """把快照里的值当数值读；布尔与非数值一律按 0（布尔是开关不是名额）。"""
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return 0
    return value


def _split_quota(quota_json: dict[str, Any] | None) -> tuple[dict[str, float], dict[str, Any]]:
    """把档位配额切成「可累加的数值键」与「只能覆盖的描述性键」。

    判据与 `access_service._quota_exceeded` 完全一致（布尔不算数值），
    这样「发货时按数值累加」与「判定时按数值比对」用的是同一套键，不会一边加一边看不见。
    """
    numeric: dict[str, float] = {}
    descriptors: dict[str, Any] = {}
    for key, value in (quota_json or {}).items():
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            descriptors[key] = value
        else:
            numeric[key] = value
    return numeric, descriptors


def _merge_quota(
    snapshot: dict[str, Any], *, numeric: dict[str, float], quantity: int, descriptors: dict[str, Any]
) -> tuple[dict[str, Any], dict[str, float], dict[str, Any]]:
    """把本单档位合并进权益快照，返回 `(新快照, 本单数值增量, 被覆盖的描述性旧值)`。

    - **数值键累加** `档位值 × 份数`：买 N 份就是 N 倍名额，这是加购型商品的全部意义；
    - **非数值键覆盖**：`tier`、显示名之类的描述性快照不可相加，以最近一单为准；
      覆盖前的旧值记进回执，退款时**仅当当前值仍是本单写入的那个**才还原
      （更晚的订单已经覆盖过就不该被本次退款回卷）。
    """
    merged = dict(snapshot)
    delta: dict[str, float] = {}
    for key, unit in numeric.items():
        added = unit * quantity
        # int × int 仍是 int；JSONB 里保持整数形态，避免 1 变成 1.0
        delta[key] = added
        merged[key] = _as_number(merged.get(key)) + added
    overwritten: dict[str, Any] = {}
    for key, value in descriptors.items():
        if key in merged:
            overwritten[key] = merged[key]
        merged[key] = value
    return merged, delta, overwritten


def _next_expiry(current: datetime | None, *, cycle: str, now: datetime, order_no: str) -> datetime | None:
    """按计费周期推新的到期时刻。

    - `once` → `None`（永久买断）；
    - `month`/`year` → **在现有到期时刻基础上顺延**（续购是加时长，不是重置）；
      已过期（或尚无权益）则从 `now` 起算，否则顺延到一个仍在过去的时刻等于白买。
    - 未知周期 → 抛错。臆造一个时长会让用户多拿或少拿，两种都是事故。
    """
    if cycle == CYCLE_ONCE:
        return None
    delta = _CYCLE_DELTAS.get(cycle)
    if delta is None:
        raise errors.RequestError(msg=f'订单 {order_no} 的计费周期 {cycle!r} 不可识别，拒绝臆造权益时长')
    if current is None:
        # 已是永久买断：只累加名额，不把永久降级成有期限
        return None
    return max(now, current) + delta


# ─── 权益行读写 ───


async def _active_entitlement(
    db: AsyncSession, *, feature_key: str, subject_type: str, subject_id: str
) -> HasnAppEntitlement | None:
    """取该主体在该 feature 上的 active 权益行（**不过滤到期**）。

    刻意不加 `expires_at > now`：已过期但还挂 `active` 的行（定时 sweep 尚未跑到）必须被复用，
    否则插新行会撞 `uq_app_entitlement_active` partial unique。

    `app_id` 一律用 `feature_key` 占位（通用特征没有 catalog app_id），与
    `access_service.grant_trial` 同款写法——正是这个占位让 partial unique 保证「每主体每 feature
    至多一条 active 行」，也就是本模块「只累加不插行」的底层依据。
    """
    rows = (
        await db.execute(
            sa.select(HasnAppEntitlement)
            .where(
                HasnAppEntitlement.feature_key == feature_key,
                HasnAppEntitlement.subject_type == subject_type,
                HasnAppEntitlement.subject_id == subject_id,
                HasnAppEntitlement.status == 'active',
            )
            .order_by(HasnAppEntitlement.id)
        )
    ).scalars().all()
    if len(rows) > 1:
        # 不变量破坏：`resolve_access` 只 `.first()` 认一条，多出来的行等于用户白买
        log.error(
            f'[FeaturePlan] 同一主体在 {feature_key} 上存在 {len(rows)} 条 active 权益（应为 1），'
            f'subject={subject_type}:{subject_id}；本次累加落在 id={rows[0].id}'
        )
    return rows[0] if rows else None


def _receipt_of(order: Any) -> dict[str, Any] | None:
    """读订单上的发货回执（有回执 = 本单已发过货）。"""
    receipt = (getattr(order, 'extra_data', None) or {}).get(FULFILLED_GRANT_KEY)
    return receipt if isinstance(receipt, dict) else None


def _write_receipt(order: Any, receipt: dict[str, Any]) -> None:
    """把发货回执写回订单 `extra_data`（JSONB 无 Mutable 跟踪，必须整体重新赋值才会 UPDATE）。"""
    extra = dict(getattr(order, 'extra_data', None) or {})
    extra[FULFILLED_GRANT_KEY] = receipt
    order.extra_data = extra


# ─── 发货 ───


async def apply_feature_plan(db: AsyncSession, *, order: Any) -> HasnAppEntitlement:
    """发/续一条 feature 权益并返回权益行（发货核心，抽出便于真实联调）。

    幂等：订单已带发货回执 → 直接返回既有权益行，不二次累加。
    """
    order_no = _order_no(order)
    offering_key, plan_key = _parse_offering_ref(order)
    quantity = _parse_quantity(order)
    offering = await _load_offering(db, offering_key=offering_key, order_no=order_no)
    plan = await _load_plan(db, offering_key=offering_key, plan_key=plan_key, order_no=order_no)
    owner_hasn_id = await _resolve_owner(db, order=order)
    feature_key = offering.feature_key

    ent = await _active_entitlement(
        db, feature_key=feature_key, subject_type=SUBJECT_TYPE_OWNER, subject_id=owner_hasn_id
    )
    if _receipt_of(order) is not None:
        log.warning(f'[FeaturePlan] 订单已发过货，跳过二次累加: order_no={order_no}, feature={feature_key}')
        if ent is None:
            # 回执在、权益没了（被退款/撤销）→ 不变量破坏，如实报错但不重发（重发会绕过退款）
            log.error(f'[FeaturePlan] 订单 {order_no} 有发货回执却无 active 权益，需人工核对')
            raise errors.RequestError(msg=f'订单 {order_no} 已发货但权益缺失，拒绝重复发放')
        return ent

    now = timezone.now()
    if ent is None:
        ent = HasnAppEntitlement(
            app_id=feature_key,
            feature_key=feature_key,
            subject_type=SUBJECT_TYPE_OWNER,
            subject_id=owner_hasn_id,
            source='purchase',
            status='active',
            order_ref=order_no,
            quota_json={},
            granted_at=now,
            expires_at=now,  # 下方按 cycle 顺延；once 会被置回 None（永久）
        )
        db.add(ent)

    numeric, descriptors = _split_quota(plan.quota_json)
    merged, delta, overwritten = _merge_quota(
        dict(ent.quota_json or {}), numeric=numeric, quantity=quantity, descriptors=descriptors
    )
    expires_before = ent.expires_at
    cycle = str(plan.cycle or '')
    ent.quota_json = merged
    ent.expires_at = _next_expiry(ent.expires_at, cycle=cycle, now=now, order_no=order_no)
    ent.status = 'active'
    ent.order_ref = order_no
    ent.updated_time = now
    await db.flush()

    _write_receipt(
        order,
        {
            'offering_key': offering_key,
            'plan_key': plan_key,
            'feature_key': feature_key,
            'subject_type': SUBJECT_TYPE_OWNER,
            'subject_id': owner_hasn_id,
            'quantity': quantity,
            'cycle': cycle,
            'quota_delta': delta,
            'descriptors': descriptors,
            'descriptors_before': overwritten,
            'expires_at_before': expires_before.isoformat() if expires_before else None,
            'expires_at_after': ent.expires_at.isoformat() if ent.expires_at else None,
            'entitlement_id': ent.id,
            'granted_at': now.isoformat(),
        },
    )
    log.info(
        f'[FeaturePlan] 权益已发放: feature={feature_key}, owner={owner_hasn_id}, '
        f'plan={offering_key}/{plan_key} ×{quantity}, 增量={delta}, '
        f'快照={merged}, expires_at={ent.expires_at}, order_no={order_no}'
    )
    return ent


async def handle_feature_plan_paid(db: AsyncSession, *, order: Any) -> None:
    """`kind=feature_plan` 商品支付成功回调 → 发/续对应 feature 权益（同一支付事务内）。"""
    log.info(
        f'[FeaturePlan] 加购支付成功: user_id={order.user_id}, '
        f'offering_ref={getattr(order, "offering_ref", None)}, '
        f'amount={order.pay_amount}分, order_no={order.order_no}'
    )
    await apply_feature_plan(db, order=order)
    # 权益就在本事务内落库，不走 outbox：履约状态可以如实置终态（doc94 C1「额度真正到账的时刻」）
    order.fulfillment_status = 'succeeded'
    order.fulfilled_at = timezone.now()


# ─── 退款回收 ───


def _rollback_quota(snapshot: dict[str, Any], receipt: dict[str, Any]) -> dict[str, Any]:
    """按回执把本单发出的配额扣回，返回新快照。"""
    rolled = dict(snapshot)
    for key, added in (receipt.get('quota_delta') or {}).items():
        rolled[key] = max(0, _as_number(rolled.get(key)) - _as_number(added))
    before = receipt.get('descriptors_before') or {}
    for key, written in (receipt.get('descriptors') or {}).items():
        # 只还原「当前值仍是本单写的那个」——更晚的订单已覆盖过就不该被本次退款回卷
        if rolled.get(key) != written:
            continue
        if key in before:
            rolled[key] = before[key]
        else:
            rolled.pop(key, None)
    return rolled


def _rollback_expiry(current: datetime | None, receipt: dict[str, Any], *, order_no: str) -> datetime | None:
    """按回执回退到期时刻：`once` 还原买断前的时刻，周期档减去一个周期。"""
    cycle = str(receipt.get('cycle') or '')
    if cycle == CYCLE_ONCE:
        raw = receipt.get('expires_at_before')
        return timezone.from_datetime(datetime.fromisoformat(raw)) if raw else None
    if current is None:
        # 期间被别的买断单转成了永久：本单只是一个周期，回退不了永久 → 如实告警，保留永久
        log.warning(f'[FeaturePlan] 订单 {order_no} 退款时权益已是永久买断，到期时刻不回退')
        return None
    delta = _CYCLE_DELTAS.get(cycle)
    if delta is None:
        log.error(f'[FeaturePlan] 订单 {order_no} 回执里的计费周期 {cycle!r} 不可识别，到期时刻未回退')
        return current
    return current - delta


async def _warn_usage_gap(db: AsyncSession, *, feature_key: str, subject_id: str, snapshot: dict[str, Any]) -> None:
    """退款后对账：配额若已低于在用量，如实 `warn` 记录缺口。

    **本 handler 不替运营做停机决策**——停不停机是运营/生命周期 sweep 的事，
    这里只保证缺口不被静默吞掉。探针本身失败不得拖垮已经发生的退款。
    """
    probe = _usage_probes.get(feature_key)
    if probe is None:
        return
    try:
        usage = await probe(db, owner_hasn_id=subject_id)
    except Exception as exc:
        log.error(f'[FeaturePlan] 用量探针执行失败: feature={feature_key}, subject={subject_id}, err={exc}')
        return
    for key, used in (usage or {}).items():
        quota = _as_number(snapshot.get(key))
        if used > quota:
            log.warning(
                f'[FeaturePlan] 退款后配额低于在用量: feature={feature_key}, subject={subject_id}, '
                f'{key} 在用 {used} > 剩余配额 {quota}（缺口 {used - quota}）；'
                f'是否停机由运营/生命周期 sweep 决策，本次退款不代为处置'
            )


async def revoke_feature_plan(db: AsyncSession, *, order: Any, refund_no: str | None = None) -> None:
    """`kind=feature_plan` 退款回收（`apply_feature_plan` 的逆操作 · MK-9 退款编排）。

    据订单上的**发货回执**精确扣回本单发出的配额并回退到期时刻——退的是本单当初发的量，
    不受期间改价影响。**收 `db` 参与 refund_order 单一事务**，与订单状态翻转原子提交。

    回执/权益缺失时如实 `error` 后返回（与 `revoke_lead_pack` 同口径）：拿不到依据就回收，
    只会把别的订单发的额度也扣掉。
    """
    order_no = _order_no(order)
    receipt = _receipt_of(order)
    if receipt is None:
        log.error(f'[FeaturePlan] 退款回收缺少发货回执，无法确定回收量: order_no={order_no}')
        return
    feature_key = str(receipt.get('feature_key') or '')
    subject_id = str(receipt.get('subject_id') or '')
    subject_type = str(receipt.get('subject_type') or SUBJECT_TYPE_OWNER)
    if not feature_key or not subject_id:
        log.error(f'[FeaturePlan] 发货回执残缺（feature_key/subject_id 缺失）: order_no={order_no}')
        return

    ent = await _active_entitlement(
        db, feature_key=feature_key, subject_type=subject_type, subject_id=subject_id
    )
    if ent is None:
        log.error(
            f'[FeaturePlan] 退款回收找不到 active 权益: feature={feature_key}, '
            f'subject={subject_type}:{subject_id}, order_no={order_no}'
        )
        return

    rolled = _rollback_quota(dict(ent.quota_json or {}), receipt)
    ent.quota_json = rolled
    ent.expires_at = _rollback_expiry(ent.expires_at, receipt, order_no=order_no)
    numeric_left = [v for v in rolled.values() if not isinstance(v, bool) and isinstance(v, (int, float))]
    if numeric_left and not any(v > 0 for v in numeric_left):
        # 名额全部清零：整行撤销。留一条 0 名额的 active 空壳，会让
        # `resolve_access` 继续判「持有权益」，进而让各业务的存活 sweep 误判用户仍有资格。
        ent.status = 'revoked'
    ent.updated_time = timezone.now()
    order.fulfillment_status = 'reversed'
    await db.flush()

    log.info(
        f'[FeaturePlan] 退款已回收权益: feature={feature_key}, subject={subject_type}:{subject_id}, '
        f'回收={receipt.get("quota_delta")}, 剩余快照={rolled}, expires_at={ent.expires_at}, '
        f'status={ent.status}, order_no={order_no}, refund_no={refund_no}'
    )
    await _warn_usage_gap(db, feature_key=feature_key, subject_id=subject_id, snapshot=rolled)


def register_feature_plan_callback() -> None:
    """注册通用 feature_plan 履约与退款回收处理器 — 在应用启动时调用（registrar）。"""
    from backend.app.billing.core.callback import register_pay_callback
    from backend.app.billing.core.fulfillment import (
        ORDER_TYPE_FEATURE_PLAN,
        register_fulfillment,
        register_refund_handler,
    )

    register_pay_callback(ORDER_TYPE_FEATURE_PLAN, handle_feature_plan_paid)  # order_type 轴（存量兼容）
    register_fulfillment(KIND_FEATURE_PLAN, handle_feature_plan_paid)  # MK-3：offering.kind=feature_plan 发货轴
    register_refund_handler(KIND_FEATURE_PLAN, revoke_feature_plan)  # MK-9：退款回收轴
    log.info('[FeaturePlan] 已注册通用 feature_plan 履约 (feature_plan) + 退款回收处理器')
