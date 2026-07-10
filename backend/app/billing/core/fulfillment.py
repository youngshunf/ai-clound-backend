"""统一发货分发器（商业化内核唯一履约入口 · MK-3）。

支付成功后「发什么货」由商品目录 ``offering.kind`` 决定，而非散落各处的 ``order_type`` 字符串——
本模块提供一张「按 kind 索引」的履约处理器注册表 + dispatch，收编 pay/app/seat/lead 四处发货口径。

- ``register_fulfillment(kind, handler)``：各业务模块启动时把「某 kind 该怎么发货」注册进来
  （与既有 ``register_pay_callback(order_type, handler)`` 并存，逐步把发货轴从 order_type 收敛到 kind）。
- ``dispatch_fulfillment(order)``：读 ``order.offering_ref.kind`` → 命中处理器执行；无 ``offering_ref``
  或该 kind 未注册时返回 ``False``，由调用方（``handle_pay_notify``）回落旧 ``order_type`` 分发（存量兼容）。
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


def register_fulfillment(kind: str, handler: Callable[..., Coroutine[Any, Any, None]]) -> None:
    """注册某 kind 的发货处理器（各业务模块启动时调用）。"""
    _fulfillment_handlers[kind] = handler
    log.info(f'[fulfillment] 注册发货处理器: kind={kind}, handler={handler.__name__}')


def get_registered_kinds() -> set[str]:
    """已注册发货处理器的 kind 集合（一致性守卫 / 自省用）。"""
    return set(_fulfillment_handlers)


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


async def dispatch_fulfillment(order: Any) -> bool:
    """按 ``order.offering_ref.kind`` 分发发货。

    :return: True=命中 kind 处理器且执行成功；False=无 ``offering_ref`` / kind 未注册（调用方回落旧分发）。
    """
    offering_ref = getattr(order, 'offering_ref', None) or {}
    kind = offering_ref.get('kind')
    if not kind:
        return False
    handler = _fulfillment_handlers.get(kind)
    if not handler:
        log.warning(
            f'[fulfillment] kind 未注册发货处理器，回落旧分发: '
            f'kind={kind}, order_no={getattr(order, "order_no", "?")}'
        )
        return False
    try:
        await handler(order)
        return True
    except Exception as e:
        log.error(f'[fulfillment] 发货处理器异常: kind={kind}, error={e}')
        raise
