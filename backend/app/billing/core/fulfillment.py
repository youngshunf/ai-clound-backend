"""统一发货分发器（商业化内核唯一履约入口 · MK-3）。

支付成功后「发什么货」由商品目录 ``offering.kind`` 决定，而非散落各处的 ``order_type`` 字符串——
本模块提供一张「按 kind 索引」的履约处理器注册表 + dispatch，收编 pay/app/seat/lead 四处发货口径。

- ``register_fulfillment(kind, handler)``：各业务模块启动时把「某 kind 该怎么发货」注册进来
  （与既有 ``register_pay_callback(order_type, handler)`` 并存，逐步把发货轴从 order_type 收敛到 kind）。
- ``dispatch_fulfillment(db, order)``：读 ``order.offering_ref.kind`` → 命中处理器，在调用方事务内
  写一条待投递的履约命令；无 ``offering_ref`` 或该 kind 未注册时**直接抛错**（fail-closed），
  订单进 ``fulfillment_status=dead`` 等人工处置，绝不静默放行。
- ``build_offering_ref(order_type, ...)``：下单入口用来把「买的是什么」快照进 ``pay_order.offering_ref``，
  ``kind`` 由 ``order_type`` 唯一确定（``ORDER_TYPE_TO_KIND``）。

设计事实源：docs/hasn-node设计文档/16-订阅与积分计费/02-统一商业化内核设计.md §4（订单 kind 分发）。
"""

from collections.abc import Callable, Coroutine
from typing import Any

from backend.common.log import log

# 商品目录 offering.kind 规范取值（与 billing_offering.kind / seed 迁移一致）。
KIND_LLM_TIER = 'llm_tier'  # LLM 订阅（升级订阅档 + 发放月度积分）
KIND_CREDIT_PACK = 'credit_pack'  # 积分充值包（一次性入账）
KIND_APP = 'app'  # AI-Native 应用购买（owner 维度权益）
KIND_SEAT = 'seat'  # 企业席位购买（累加 seats_total）
KIND_FEATURE_PLAN = 'feature_plan'  # 通用 feature 订阅/买断（MK-2 通用路预留）
KIND_LEAD_PACK = 'lead_pack'  # 获客线索包（hasn_growth 额度）

# order_type（存量下单分支）→ offering.kind（发货轴）的唯一映射。
ORDER_TYPE_TO_KIND: dict[str, str] = {
    'subscribe': KIND_LLM_TIER,
    'credit_pack': KIND_CREDIT_PACK,
    'app_purchase': KIND_APP,
    'app_seat': KIND_SEAT,
    'lead_pack': KIND_LEAD_PACK,
}

# 履约处理器：offering.kind -> async handler(order)
_fulfillment_handlers: dict[str, Callable[..., Coroutine[Any, Any, None]]] = {}
# 退款回收处理器：offering.kind -> async handler(db, *, order)
# 与发货处理器对称：发货「授予权益/入账额度」，退款则「回收权益/扣减额度」。
# 注意签名差异——退款回收**须与订单状态翻转同事务**（避免退钱不回收/回收不退钱），
# 故收 session 参数 ``db`` 参与 refund_order 的单一事务；发货沿用各自独立 session 的存量范式。
_refund_handlers: dict[str, Callable[..., Coroutine[Any, Any, None]]] = {}


def register_fulfillment(kind: str, handler: Callable[..., Coroutine[Any, Any, None]]) -> None:
    """注册某 kind 的发货处理器（各业务模块启动时调用）。"""
    _fulfillment_handlers[kind] = handler
    log.info(f'[fulfillment] 注册发货处理器: kind={kind}, handler={handler.__name__}')


def register_refund_handler(kind: str, handler: Callable[..., Coroutine[Any, Any, None]]) -> None:
    """注册某 kind 的退款回收处理器（各业务模块启动时与发货处理器成对注册）。

    handler 签名：``async handler(db, *, order) -> None``——在 refund_order 的事务内回收
    该 kind 发出的权益/额度（应用撤权益、席位减 seats_total、积分/线索扣减）。
    """
    _refund_handlers[kind] = handler
    log.info(f'[fulfillment] 注册退款回收处理器: kind={kind}, handler={handler.__name__}')


def get_registered_kinds() -> set[str]:
    """已注册发货处理器的 kind 集合（一致性守卫 / 自省用）。"""
    return set(_fulfillment_handlers)


def get_registered_refund_kinds() -> set[str]:
    """已注册退款回收处理器的 kind 集合（一致性守卫 / 自省用）。"""
    return set(_refund_handlers)


def build_offering_ref(
    order_type: str,
    *,
    offering_key: str | None = None,
    plan_key: str | None = None,
) -> dict | None:
    """据 ``order_type`` 组装 ``pay_order.offering_ref`` 快照（下单入口用）。

    ``kind`` 由 ``order_type`` 唯一确定；``offering_key`` / ``plan_key`` 为下单时已知的商品/档位快照
    （不强制解析 live 行——发货只认 ``kind``）。``order_type`` 未知时返回 ``None``（不落 ``offering_ref``）。
    """
    kind = ORDER_TYPE_TO_KIND.get(order_type)
    if not kind:
        return None
    return {'offering_key': offering_key, 'plan_key': plan_key, 'kind': kind}


async def dispatch_fulfillment(db: Any, order: Any) -> None:
    """按 ``order.offering_ref.kind`` 分发履约，**在调用方事务内执行**。

    handler 的职责已从「直接发货（改余额/改订阅）」改成「写一条待投递的履约命令」——
    发什么货仍由这张 kind 注册表路由，真正碰 NewAPI 的动作交给 outbox worker。
    这样 saga 与「同事务」就不冲突了：事务内只写命令，事务外才碰外部系统。

    **fail-closed（铁律）**：无 ``offering_ref`` 或 kind 未注册一律抛错，让订单进
    ``fulfillment_status=dead`` 等人工处置。原先的「回落 order_type 旧分发」分支已删除——
    静默回落一旦漏配，用户就会看到「支付成功」却永远收不到额度。

    :raises RequestError: 订单缺 ``offering_ref`` 或 kind 未注册处理器。
    """
    from backend.common.exception import errors

    order_no = getattr(order, 'order_no', '?')
    offering_ref = getattr(order, 'offering_ref', None) or {}
    kind = offering_ref.get('kind')
    if not kind:
        raise errors.RequestError(msg=f'订单 {order_no} 缺少商品类型快照（offering_ref），无法安全履约')
    handler = _fulfillment_handlers.get(kind)
    if not handler:
        raise errors.RequestError(msg=f'商品类型 {kind} 未注册履约处理器，拒绝静默放行订单 {order_no}')
    try:
        await handler(db, order=order)
    except Exception as e:
        log.error(f'[fulfillment] 履约处理器异常: kind={kind}, order_no={order_no}, error={e}')
        raise


def _resolve_order_kind(order: Any) -> str | None:
    """解析订单的 offering.kind：优先 ``offering_ref.kind``，回落 ``order_type`` 映射（存量订单无 offering_ref）。"""
    offering_ref = getattr(order, 'offering_ref', None) or {}
    kind = offering_ref.get('kind')
    if kind:
        return str(kind)
    order_type = getattr(order, 'order_type', None)
    if not order_type:
        return None
    return ORDER_TYPE_TO_KIND.get(str(order_type))


async def reverse_fulfillment(db: Any, order: Any) -> None:
    """退款时按 ``offering.kind`` 反向回收已发出的权益/额度（发货的逆操作）。

    **fail-closed（铁律）**：无法确定 kind、或该 kind 未注册退款回收处理器 → 直接抛错拒绝退款——
    绝不「退了钱却不回收权益」。回收在 refund_order 的**同一事务**内执行（收 ``db``），与订单状态翻转原子提交。

    :raises RequestError: 订单缺商品类型 / kind 未注册回收处理器（拒绝退款）。
    """
    # 延迟导入避免与 common.exception 顶层耦合（本模块仅依赖 log）。
    from backend.common.exception import errors

    kind = _resolve_order_kind(order)
    if not kind:
        raise errors.RequestError(msg=f'订单 {getattr(order, "order_no", "?")} 无商品类型，无法安全退款回收')
    handler = _refund_handlers.get(kind)
    if not handler:
        raise errors.RequestError(
            msg=f'商品类型 {kind} 未注册退款回收处理器，拒绝退款（避免退钱不回收权益）'
        )
    try:
        await handler(db, order=order)
    except Exception as e:
        log.error(f'[fulfillment] 退款回收处理器异常: kind={kind}, error={e}')
        raise
